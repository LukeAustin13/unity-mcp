using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Editor.Tools.Build;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// READ-only build pre-flight. Inspects whether a player build for a given
    /// target would succeed WITHOUT producing any artifacts or mutating project
    /// state. Safe to run in read_only/review_only before a real manage_build.
    /// </summary>
    [McpForUnityTool("validate_build", AutoRegister = false, Group = "core")]
    public static class ValidateBuild
    {
        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");

            try
            {
                var p = new ToolParams(@params);
                string targetName = p.Get("target");

                var checks = new List<object>();
                var issues = new List<string>();
                bool wouldBuild = true;

                // (a) target resolves — reuse ManageBuild's mapping
                if (!BuildTargetMapping.TryResolveBuildTarget(targetName, out var target))
                {
                    string detail = BuildTargetMapping.GetUnknownBuildTargetMessage(targetName);
                    checks.Add(Check("target_resolves", false, detail));
                    issues.Add(detail);
                    // Cannot proceed with further target-dependent checks.
                    return new SuccessResponse("Build pre-flight complete.", new
                    {
                        would_build = false,
                        target = targetName,
                        checks,
                        issues
                    });
                }
                checks.Add(Check("target_resolves", true, $"Resolved to {target}."));

                // (b) build support installed for this target
                var group = BuildTargetMapping.GetTargetGroup(target);
                bool supported = BuildPipeline.IsBuildTargetSupported(group, target);
                if (!supported)
                {
                    string detail = $"Build support for '{target}' is not installed. Install it via Unity Hub.";
                    checks.Add(Check("build_support_installed", false, detail));
                    issues.Add(detail);
                    wouldBuild = false;
                }
                else
                {
                    checks.Add(Check("build_support_installed", true, $"Build support for {target} is installed."));
                }

                // (c) scenes in EditorBuildSettings — count, enabled count, on-disk existence
                var allScenes = EditorBuildSettings.scenes;
                int totalScenes = allScenes.Length;
                var enabledScenes = allScenes.Where(s => s.enabled).ToArray();
                int enabledCount = enabledScenes.Length;

                var missingScenes = enabledScenes
                    .Where(s => string.IsNullOrEmpty(s.path) || !File.Exists(s.path))
                    .Select(s => s.path ?? "(empty path)")
                    .ToArray();

                if (enabledCount == 0)
                {
                    string detail = "No enabled scenes in Build Settings. The build would use only the active scene (if any).";
                    checks.Add(Check("scenes_enabled", false, detail));
                    issues.Add(detail);
                    // A scene-less build can still technically produce a player; do not
                    // fail wouldBuild solely on this, but surface it as an issue.
                }
                else if (missingScenes.Length > 0)
                {
                    string detail =
                        $"{missingScenes.Length} enabled scene(s) do not exist on disk: {string.Join(", ", missingScenes)}.";
                    checks.Add(Check("scenes_enabled", false, detail));
                    issues.Add(detail);
                    wouldBuild = false;
                }
                else
                {
                    checks.Add(Check("scenes_enabled", true,
                        $"{enabledCount} of {totalScenes} scene(s) enabled; all exist on disk."));
                }

                // (d) no pending compile errors
                bool compilationFailed = EditorUtility.scriptCompilationFailed;
                if (compilationFailed)
                {
                    string detail = "Scripts failed to compile — fix compile errors before building.";
                    checks.Add(Check("scripts_compile", false, detail));
                    issues.Add(detail);
                    wouldBuild = false;
                }
                else
                {
                    checks.Add(Check("scripts_compile", true, "No script compilation errors detected."));
                }

                // (e) PlayerSettings sanity — productName / companyName / applicationIdentifier
                var namedTarget = BuildTargetMapping.GetNamedBuildTarget(target);
                CheckNonEmptyPlayerSetting("product_name", PlayerSettings.productName, checks, issues, ref wouldBuild);
                CheckNonEmptyPlayerSetting("company_name", PlayerSettings.companyName, checks, issues, ref wouldBuild);

                string appId = SafeGetApplicationIdentifier(namedTarget);
                CheckNonEmptyPlayerSetting("application_identifier", appId, checks, issues, ref wouldBuild);

                return new SuccessResponse("Build pre-flight complete.", new
                {
                    would_build = wouldBuild,
                    target = target.ToString(),
                    checks,
                    issues
                });
            }
            catch (Exception ex)
            {
                return new ErrorResponse(ex.Message, new { stackTrace = ex.StackTrace });
            }
        }

        private static object Check(string name, bool ok, string detail)
        {
            return new { name, ok, detail };
        }

        private static void CheckNonEmptyPlayerSetting(
            string name, string value, List<object> checks, List<string> issues, ref bool wouldBuild)
        {
            if (string.IsNullOrWhiteSpace(value))
            {
                string detail = $"PlayerSettings.{name} is empty.";
                checks.Add(Check(name, false, detail));
                issues.Add(detail);
                wouldBuild = false;
            }
            else
            {
                checks.Add(Check(name, true, $"{name} = '{value}'."));
            }
        }

        private static string SafeGetApplicationIdentifier(NamedBuildTarget namedTarget)
        {
            try
            {
                return PlayerSettings.GetApplicationIdentifier(namedTarget);
            }
            catch
            {
                return null;
            }
        }
    }
}
