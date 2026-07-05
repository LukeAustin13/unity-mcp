using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// READ-only scene-contract validator. A project declares, as JSON, what a
    /// scene MUST and MUST NOT contain (required/forbidden objects, required
    /// components, camera/light caps, tags/layers, build-settings membership, UI
    /// wiring), and this tool checks the CURRENTLY-LOADED scene against it and
    /// returns structured pass/fail findings.
    ///
    /// Strictly read-only: it NEVER opens, closes, saves, or modifies scenes,
    /// never touches assets or settings, and never enters play mode. The only
    /// filesystem access is reading a project-relative contract file (guarded:
    /// must end .json, resolve under the project root, and be &lt;= 256 KB).
    ///
    /// Fails closed: an unknown top-level contract key, an unknown action, or
    /// supplying both/neither of contract and contract_path is a structured
    /// error, never a silent pass — a typo'd rule must not validate as clean.
    /// </summary>
    [McpForUnityTool("validate_scene_contracts", AutoRegister = false, Group = "core")]
    public static class ValidateSceneContracts
    {
        private const long MaxContractBytes = 256 * 1024;

        // Objects scanned by forbid_missing_references before truncating.
        internal const int MaxScannedObjects = 20000;

        // Every top-level contract key we understand. Anything outside this set
        // is rejected so a typo can never silently pass.
        internal static readonly HashSet<string> SupportedKeys = new(StringComparer.Ordinal)
        {
            "required_objects",
            "forbidden_objects",
            "required_components",
            "max_cameras",
            "max_lights",
            "forbid_missing_references",
            "required_tags",
            "required_layers",
            "require_in_build_settings",
            "ui",
        };

        internal static readonly HashSet<string> SupportedUiKeys = new(StringComparer.Ordinal)
        {
            "require_event_system",
            "require_graphic_raycaster",
        };

        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");

            try
            {
                var p = new ToolParams(@params);

                // Fail closed on an unexpected action. Absent or "validate" only.
                string action = p.Get("action");
                if (!string.IsNullOrEmpty(action) &&
                    !string.Equals(action.Trim(), "validate", StringComparison.OrdinalIgnoreCase))
                {
                    return new ErrorResponse(
                        $"Unsupported action '{action}'. validate_scene_contracts only supports 'validate' (or no action).");
                }

                // Exactly one of contract | contract_path.
                JToken inlineContract = p.GetRaw("contract");
                bool hasInline = inlineContract != null && inlineContract.Type != JTokenType.Null;
                string contractPath = p.Get("contract_path");
                bool hasPath = !string.IsNullOrWhiteSpace(contractPath);

                if (hasInline && hasPath)
                    return new ErrorResponse(
                        "Provide exactly one of 'contract' (inline JSON) or 'contract_path' (project-relative path), not both.");
                if (!hasInline && !hasPath)
                    return new ErrorResponse(
                        "A contract is required. Provide either 'contract' (inline JSON object) or 'contract_path' (project-relative .json path).");

                JObject contract;
                if (hasInline)
                {
                    if (inlineContract.Type != JTokenType.Object)
                        return new ErrorResponse("'contract' must be a JSON object.");
                    contract = (JObject)inlineContract;
                }
                else
                {
                    var loaded = LoadContractFromPath(contractPath, out string loadError);
                    if (loaded == null)
                        return new ErrorResponse(loadError);
                    contract = loaded;
                }

                // Reject unknown top-level keys (fail closed).
                var unknownKeys = contract.Properties()
                    .Select(pr => pr.Name)
                    .Where(name => !SupportedKeys.Contains(name))
                    .ToList();
                if (unknownKeys.Count > 0)
                {
                    return new ErrorResponse(
                        $"Contract has unsupported top-level key(s): {string.Join(", ", unknownKeys)}. " +
                        $"Supported keys: {string.Join(", ", SupportedKeys.OrderBy(k => k, StringComparer.Ordinal))}.");
                }

                // Resolve the target scene (must be LOADED; never opens one).
                string sceneName = p.Get("scene");
                Scene scene;
                string sceneError = ResolveLoadedScene(sceneName, out scene);
                if (sceneError != null)
                    return new ErrorResponse(sceneError);

                var findings = new List<object>();
                var caveats = new List<string>();
                var byRule = new Dictionary<string, int>();

                EvaluateContract(contract, scene, findings, caveats);

                int failed = 0;
                foreach (var f in findings)
                {
                    var dict = (Dictionary<string, object>)f;
                    string rule = (string)dict["rule"];
                    if ((string)dict["status"] == "fail")
                    {
                        failed++;
                        byRule.TryGetValue(rule, out int c);
                        byRule[rule] = c + 1;
                    }
                }

                bool passed = failed == 0;
                var payload = new Dictionary<string, object>
                {
                    ["passed"] = passed,
                    ["scene"] = scene.name,
                    ["findings"] = findings,
                    ["summary"] = new Dictionary<string, object>
                    {
                        ["rules_evaluated"] = findings.Count,
                        ["failed"] = failed,
                        ["by_rule"] = byRule,
                    },
                    ["caveats"] = caveats,
                };

                string message = passed
                    ? $"Scene '{scene.name}' satisfies the contract ({findings.Count} check(s))."
                    : $"Scene '{scene.name}' violates the contract: {failed} of {findings.Count} check(s) failed.";
                return new SuccessResponse(message, payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse(
                    $"Error in validate_scene_contracts: {ex.Message}",
                    new { stackTrace = ex.StackTrace });
            }
        }

        // --- contract loading ----------------------------------------------

        // Project root is the folder that contains Assets/ (Application.dataPath
        // ends with "/Assets"). Mirrors ManageCheckpoint.ProjectRoot().
        private static string ProjectRoot()
        {
            string dataPath = Application.dataPath.Replace("\\", "/");
            return dataPath.Substring(0, dataPath.Length - "Assets".Length);
        }

        // Canonical, separator-normalized directory with a trailing slash so a
        // prefix check cannot be fooled by "..\ProjectRootEvil".
        private static string CanonicalDir(string path)
        {
            string full = Path.GetFullPath(path).Replace("\\", "/");
            if (!full.EndsWith("/")) full += "/";
            return full;
        }

        // Load and parse the contract from a project-relative path, applying the
        // .json / prefix / size guards. Returns null and sets error on rejection.
        private static JObject LoadContractFromPath(string contractPath, out string error)
        {
            error = null;

            string raw = contractPath.Trim();
            if (!raw.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
            {
                error = "contract_path must point to a .json file.";
                return null;
            }

            string root = ProjectRoot();
            string canonicalRoot = CanonicalDir(root);

            string fullPath;
            try
            {
                fullPath = Path.GetFullPath(Path.Combine(root, raw)).Replace("\\", "/");
            }
            catch (Exception ex)
            {
                error = $"contract_path is not a valid path: {ex.Message}";
                return null;
            }

            if (!fullPath.StartsWith(canonicalRoot, StringComparison.Ordinal))
            {
                error = "contract_path must resolve to a file inside the project root.";
                return null;
            }

            if (!File.Exists(fullPath))
            {
                error = $"Contract file not found: {contractPath}";
                return null;
            }

            long size;
            try
            {
                size = new FileInfo(fullPath).Length;
            }
            catch (Exception ex)
            {
                error = $"Could not read contract file: {ex.Message}";
                return null;
            }
            if (size > MaxContractBytes)
            {
                error = $"Contract file is too large ({size} bytes; limit {MaxContractBytes}).";
                return null;
            }

            string text;
            try
            {
                text = File.ReadAllText(fullPath);
            }
            catch (Exception ex)
            {
                error = $"Could not read contract file: {ex.Message}";
                return null;
            }

            JToken parsed;
            try
            {
                parsed = JToken.Parse(text);
            }
            catch (JsonException ex)
            {
                error = $"Contract file is not valid JSON: {ex.Message}";
                return null;
            }

            if (parsed.Type != JTokenType.Object)
            {
                error = "Contract file must contain a JSON object at its root.";
                return null;
            }
            return (JObject)parsed;
        }

        // --- scene resolution ----------------------------------------------

        // Resolve to a LOADED scene. When a name is given it must match a loaded
        // scene, else a structured error asks the caller to open it first — we
        // NEVER open a scene. Default target is the active scene.
        private static string ResolveLoadedScene(string sceneName, out Scene scene)
        {
            scene = default;

            if (!string.IsNullOrWhiteSpace(sceneName))
            {
                string wanted = sceneName.Trim();
                for (int i = 0; i < SceneManager.sceneCount; i++)
                {
                    var s = SceneManager.GetSceneAt(i);
                    if (s.IsValid() && s.isLoaded && s.name == wanted)
                    {
                        scene = s;
                        return null;
                    }
                }
                return $"Scene '{wanted}' is not loaded. Open it first, then re-run this validation. " +
                       "validate_scene_contracts never opens scenes.";
            }

            var active = SceneManager.GetActiveScene();
            if (!active.IsValid() || !active.isLoaded)
                return "No active loaded scene to validate.";
            scene = active;
            return null;
        }

        // --- evaluation -----------------------------------------------------

        internal static void EvaluateContract(
            JObject contract, Scene scene, List<object> findings, List<string> caveats)
        {
            // Snapshot the scene's objects once (active + inactive) for reuse.
            var allObjects = CollectSceneObjects(scene);

            if (contract["required_objects"] != null)
                EvaluateRequiredObjects(contract["required_objects"], allObjects, findings);

            if (contract["forbidden_objects"] != null)
                EvaluateForbiddenObjects(contract["forbidden_objects"], allObjects, findings);

            if (contract["required_components"] != null)
                EvaluateRequiredComponents(contract["required_components"], allObjects, findings);

            if (contract["max_cameras"] != null)
                EvaluateMaxComponent(contract["max_cameras"], "max_cameras", allObjects, findings, IsEnabledCamera);

            if (contract["max_lights"] != null)
                EvaluateMaxComponent(contract["max_lights"], "max_lights", allObjects, findings, IsEnabledLight);

            if (contract["forbid_missing_references"] != null)
                EvaluateMissingReferences(contract["forbid_missing_references"], allObjects, findings, caveats);

            if (contract["required_tags"] != null)
                EvaluateRequiredTags(contract["required_tags"], allObjects, findings);

            if (contract["required_layers"] != null)
                EvaluateRequiredLayers(contract["required_layers"], allObjects, findings);

            if (contract["require_in_build_settings"] != null)
                EvaluateRequireInBuildSettings(contract["require_in_build_settings"], scene, findings);

            if (contract["ui"] != null)
                EvaluateUi(contract["ui"], allObjects, findings);
        }

        private static List<GameObject> CollectSceneObjects(Scene scene)
        {
            var result = new List<GameObject>();
            if (!scene.IsValid() || !scene.isLoaded) return result;
            foreach (var root in scene.GetRootGameObjects())
                CollectRecursive(root, result);
            return result;
        }

        private static void CollectRecursive(GameObject go, List<GameObject> into)
        {
            if (go == null) return;
            into.Add(go);
            var t = go.transform;
            int count = t.childCount;
            for (int i = 0; i < count; i++)
            {
                var child = t.GetChild(i);
                if (child != null)
                    CollectRecursive(child.gameObject, into);
            }
        }

        // A contract entry "Canvas/PlayButton" matches by full hierarchy path, and
        // a bare "PlayButton" matches by exact name anywhere in the scene.
        internal static bool ObjectExists(string spec, List<GameObject> objects)
        {
            if (string.IsNullOrEmpty(spec)) return false;
            bool isPath = spec.Contains('/');
            foreach (var go in objects)
            {
                if (go == null) continue;
                if (isPath)
                {
                    if (GameObjectLookup.GetGameObjectPath(go) == spec) return true;
                }
                else
                {
                    if (go.name == spec) return true;
                }
            }
            return false;
        }

        internal static GameObject FindObject(string spec, List<GameObject> objects)
        {
            if (string.IsNullOrEmpty(spec)) return null;
            bool isPath = spec.Contains('/');
            foreach (var go in objects)
            {
                if (go == null) continue;
                if (isPath)
                {
                    if (GameObjectLookup.GetGameObjectPath(go) == spec) return go;
                }
                else
                {
                    if (go.name == spec) return go;
                }
            }
            return null;
        }

        private static void EvaluateRequiredObjects(JToken token, List<GameObject> objects, List<object> findings)
        {
            foreach (var entry in AsStringList(token))
            {
                bool exists = ObjectExists(entry, objects);
                findings.Add(Finding(
                    "required_objects", exists,
                    exists ? $"Object '{entry}' exists." : $"Required object '{entry}' is missing.",
                    objectPath: entry,
                    suggestedFix: exists ? null : $"Add a GameObject matching '{entry}' to the scene."));
            }
        }

        private static void EvaluateForbiddenObjects(JToken token, List<GameObject> objects, List<object> findings)
        {
            foreach (var entry in AsStringList(token))
            {
                bool exists = ObjectExists(entry, objects);
                findings.Add(Finding(
                    "forbidden_objects", !exists,
                    exists ? $"Forbidden object '{entry}' is present." : $"Object '{entry}' is absent.",
                    objectPath: entry,
                    suggestedFix: exists ? $"Remove the GameObject matching '{entry}' from the scene." : null));
            }
        }

        private static void EvaluateRequiredComponents(JToken token, List<GameObject> objects, List<object> findings)
        {
            if (token.Type != JTokenType.Array)
            {
                findings.Add(Finding("required_components", false,
                    "required_components must be an array of {object, components} entries.", severity: "error"));
                return;
            }

            foreach (var entryToken in (JArray)token)
            {
                if (entryToken.Type != JTokenType.Object)
                {
                    findings.Add(Finding("required_components", false,
                        "Each required_components entry must be an object with 'object' and 'components'.",
                        severity: "error"));
                    continue;
                }
                var entry = (JObject)entryToken;
                string objectSpec = entry["object"]?.ToString();
                var components = AsStringList(entry["components"]).ToList();

                if (string.IsNullOrEmpty(objectSpec))
                {
                    findings.Add(Finding("required_components", false,
                        "A required_components entry is missing its 'object'.", severity: "error"));
                    continue;
                }

                var go = FindObject(objectSpec, objects);
                if (go == null)
                {
                    findings.Add(Finding("required_components", false,
                        $"Required object '{objectSpec}' is missing, so its components cannot be checked.",
                        objectPath: objectSpec,
                        suggestedFix: $"Add a GameObject matching '{objectSpec}' to the scene."));
                    continue;
                }

                if (components.Count == 0)
                {
                    findings.Add(Finding("required_components", true,
                        $"Object '{objectSpec}' exists (no components listed to require).",
                        objectPath: objectSpec));
                    continue;
                }

                foreach (var compName in components)
                {
                    var type = GameObjectLookup.FindComponentType(compName);
                    if (type == null)
                    {
                        findings.Add(Finding("required_components", false,
                            $"Component '{compName}' on '{objectSpec}': unknown component type.",
                            objectPath: objectSpec,
                            suggestedFix: $"Use a resolvable type name (short e.g. 'Rigidbody' or namespaced e.g. 'UnityEngine.Rigidbody')."));
                        continue;
                    }
                    bool has = go.GetComponent(type) != null;
                    findings.Add(Finding("required_components", has,
                        has
                            ? $"Object '{objectSpec}' has component '{compName}'."
                            : $"Object '{objectSpec}' is missing component '{compName}'.",
                        objectPath: objectSpec,
                        suggestedFix: has ? null : $"Add a {compName} component to '{objectSpec}'."));
                }
            }
        }

        private static void EvaluateMaxComponent(
            JToken token, string rule, List<GameObject> objects, List<object> findings, Func<Component, bool> counts)
        {
            int? max = AsInt(token);
            if (max == null)
            {
                findings.Add(Finding(rule, false, $"{rule} must be an integer.", severity: "error"));
                return;
            }

            int count = 0;
            foreach (var go in objects)
            {
                if (go == null) continue;
                foreach (var comp in go.GetComponents<Component>())
                {
                    if (comp == null) continue;
                    if (counts(comp)) count++;
                }
            }

            bool ok = count <= max.Value;
            string noun = rule == "max_cameras" ? "enabled Camera component(s)" : "enabled Light component(s)";
            findings.Add(Finding(rule, ok,
                ok
                    ? $"Scene has {count} {noun} (limit {max.Value})."
                    : $"Scene has {count} {noun}, exceeding the limit of {max.Value}.",
                suggestedFix: ok ? null : $"Reduce {noun} to at most {max.Value}."));
        }

        private static bool IsEnabledCamera(Component comp)
        {
            return comp is Camera cam && cam.enabled && cam.gameObject.activeInHierarchy;
        }

        private static bool IsEnabledLight(Component comp)
        {
            return comp is Light light && light.enabled && light.gameObject.activeInHierarchy;
        }

        private static void EvaluateMissingReferences(
            JToken token, List<GameObject> objects, List<object> findings, List<string> caveats)
        {
            bool enabled = token.Type == JTokenType.Boolean ? token.Value<bool>() : false;
            if (token.Type != JTokenType.Boolean)
            {
                findings.Add(Finding("forbid_missing_references", false,
                    "forbid_missing_references must be a boolean.", severity: "error"));
                return;
            }
            if (!enabled)
            {
                findings.Add(Finding("forbid_missing_references", true,
                    "Missing-reference scan not requested (forbid_missing_references=false)."));
                return;
            }

            int scanned = 0;
            int missingScripts = 0;
            int danglingRefs = 0;
            bool truncated = false;
            var firstOffenders = new List<string>();

            foreach (var go in objects)
            {
                if (go == null) continue;
                if (scanned >= MaxScannedObjects)
                {
                    truncated = true;
                    break;
                }
                scanned++;

                int missing = GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(go);
                if (missing > 0)
                {
                    missingScripts += missing;
                    if (firstOffenders.Count < 20)
                        firstOffenders.Add($"{GameObjectLookup.GetGameObjectPath(go)} (missing script)");
                }

                foreach (var comp in go.GetComponents<Component>())
                {
                    if (comp == null) continue; // missing script already counted above
                    danglingRefs += CountDanglingRefs(comp, go, firstOffenders);
                }
            }

            if (truncated)
                caveats.Add($"forbid_missing_references scan truncated after {MaxScannedObjects} object(s); results are partial.");

            int total = missingScripts + danglingRefs;
            bool ok = total == 0;
            string detail = ok
                ? $"No missing scripts or dangling references found ({scanned} object(s) scanned)."
                : $"Found {missingScripts} missing script(s) and {danglingRefs} dangling reference(s) across {scanned} object(s) scanned.";
            if (!ok && firstOffenders.Count > 0)
                detail += " Examples: " + string.Join("; ", firstOffenders.Take(5)) + ".";

            findings.Add(Finding("forbid_missing_references", ok, detail,
                suggestedFix: ok ? null : "Reassign or remove the missing scripts and broken object references."));
        }

        private static int CountDanglingRefs(Component comp, GameObject owner, List<string> firstOffenders)
        {
            int count = 0;
            using var so = new SerializedObject(comp);
            var it = so.GetIterator();
            bool enterChildren = true;
            while (it.NextVisible(enterChildren))
            {
                enterChildren = true;
                if (it.propertyType != SerializedPropertyType.ObjectReference) continue;
                if (it.objectReferenceValue == null && it.objectReferenceInstanceIDValue != 0)
                {
                    count++;
                    if (firstOffenders.Count < 20)
                        firstOffenders.Add(
                            $"{GameObjectLookup.GetGameObjectPath(owner)}/{comp.GetType().Name}.{it.propertyPath} (dangling ref)");
                }
            }
            return count;
        }

        private static void EvaluateRequiredTags(JToken token, List<GameObject> objects, List<object> findings)
        {
            if (token.Type != JTokenType.Array)
            {
                findings.Add(Finding("required_tags", false,
                    "required_tags must be an array of {object, tag} entries.", severity: "error"));
                return;
            }
            foreach (var entryToken in (JArray)token)
            {
                if (entryToken.Type != JTokenType.Object)
                {
                    findings.Add(Finding("required_tags", false,
                        "Each required_tags entry must be an object with 'object' and 'tag'.", severity: "error"));
                    continue;
                }
                var entry = (JObject)entryToken;
                string objectSpec = entry["object"]?.ToString();
                string tag = entry["tag"]?.ToString();
                if (string.IsNullOrEmpty(objectSpec) || string.IsNullOrEmpty(tag))
                {
                    findings.Add(Finding("required_tags", false,
                        "A required_tags entry needs both 'object' and 'tag'.", severity: "error"));
                    continue;
                }
                var go = FindObject(objectSpec, objects);
                if (go == null)
                {
                    findings.Add(Finding("required_tags", false,
                        $"Object '{objectSpec}' is missing; cannot check tag '{tag}'.",
                        objectPath: objectSpec,
                        suggestedFix: $"Add a GameObject matching '{objectSpec}'."));
                    continue;
                }
                bool ok;
                try { ok = go.CompareTag(tag); }
                catch (UnityException) { ok = false; }
                findings.Add(Finding("required_tags", ok,
                    ok ? $"Object '{objectSpec}' has tag '{tag}'."
                       : $"Object '{objectSpec}' does not have tag '{tag}' (or the tag is undefined).",
                    objectPath: objectSpec,
                    suggestedFix: ok ? null : $"Set the tag of '{objectSpec}' to '{tag}' (define the tag first if needed)."));
            }
        }

        private static void EvaluateRequiredLayers(JToken token, List<GameObject> objects, List<object> findings)
        {
            if (token.Type != JTokenType.Array)
            {
                findings.Add(Finding("required_layers", false,
                    "required_layers must be an array of {object, layer} entries.", severity: "error"));
                return;
            }
            foreach (var entryToken in (JArray)token)
            {
                if (entryToken.Type != JTokenType.Object)
                {
                    findings.Add(Finding("required_layers", false,
                        "Each required_layers entry must be an object with 'object' and 'layer'.", severity: "error"));
                    continue;
                }
                var entry = (JObject)entryToken;
                string objectSpec = entry["object"]?.ToString();
                string layerName = entry["layer"]?.ToString();
                if (string.IsNullOrEmpty(objectSpec) || string.IsNullOrEmpty(layerName))
                {
                    findings.Add(Finding("required_layers", false,
                        "A required_layers entry needs both 'object' and 'layer'.", severity: "error"));
                    continue;
                }
                int wantedLayer = LayerMask.NameToLayer(layerName);
                if (wantedLayer == -1)
                {
                    findings.Add(Finding("required_layers", false,
                        $"Layer '{layerName}' is not defined in the project.",
                        objectPath: objectSpec,
                        suggestedFix: $"Define the layer '{layerName}' in Tags and Layers settings."));
                    continue;
                }
                var go = FindObject(objectSpec, objects);
                if (go == null)
                {
                    findings.Add(Finding("required_layers", false,
                        $"Object '{objectSpec}' is missing; cannot check layer '{layerName}'.",
                        objectPath: objectSpec,
                        suggestedFix: $"Add a GameObject matching '{objectSpec}'."));
                    continue;
                }
                bool ok = go.layer == wantedLayer;
                findings.Add(Finding("required_layers", ok,
                    ok ? $"Object '{objectSpec}' is on layer '{layerName}'."
                       : $"Object '{objectSpec}' is not on layer '{layerName}'.",
                    objectPath: objectSpec,
                    suggestedFix: ok ? null : $"Move '{objectSpec}' to layer '{layerName}'."));
            }
        }

        private static void EvaluateRequireInBuildSettings(JToken token, Scene scene, List<object> findings)
        {
            if (token.Type != JTokenType.Boolean)
            {
                findings.Add(Finding("require_in_build_settings", false,
                    "require_in_build_settings must be a boolean.", severity: "error"));
                return;
            }
            if (!token.Value<bool>())
            {
                findings.Add(Finding("require_in_build_settings", true,
                    "Build-settings membership not required (require_in_build_settings=false)."));
                return;
            }

            string scenePath = scene.path;
            if (string.IsNullOrEmpty(scenePath))
            {
                findings.Add(Finding("require_in_build_settings", false,
                    "The active scene has no saved path, so it cannot be in Build Settings.",
                    suggestedFix: "Save the scene to disk, then add it to Build Settings."));
                return;
            }

            var match = EditorBuildSettings.scenes.FirstOrDefault(s =>
                string.Equals(s.path, scenePath, StringComparison.OrdinalIgnoreCase));
            bool present = match != null;
            bool enabled = present && match.enabled;

            if (enabled)
            {
                findings.Add(Finding("require_in_build_settings", true,
                    $"Scene '{scene.name}' is present and enabled in Build Settings."));
            }
            else if (present)
            {
                findings.Add(Finding("require_in_build_settings", false,
                    $"Scene '{scene.name}' is in Build Settings but is DISABLED.",
                    suggestedFix: "Enable the scene's checkbox in Build Settings."));
            }
            else
            {
                findings.Add(Finding("require_in_build_settings", false,
                    $"Scene '{scene.name}' is not in Build Settings.",
                    suggestedFix: "Add the scene to Build Settings and enable it."));
            }
        }

        private static void EvaluateUi(JToken token, List<GameObject> objects, List<object> findings)
        {
            if (token.Type != JTokenType.Object)
            {
                findings.Add(Finding("ui", false,
                    "ui must be an object with optional 'require_event_system' / 'require_graphic_raycaster' flags.",
                    severity: "error"));
                return;
            }
            var ui = (JObject)token;

            var unknown = ui.Properties().Select(pr => pr.Name).Where(n => !SupportedUiKeys.Contains(n)).ToList();
            if (unknown.Count > 0)
            {
                findings.Add(Finding("ui", false,
                    $"ui has unsupported key(s): {string.Join(", ", unknown)}. Supported: {string.Join(", ", SupportedUiKeys)}.",
                    severity: "error"));
                return;
            }

            if (ui["require_event_system"] != null && ui["require_event_system"].Type == JTokenType.Boolean
                && ui["require_event_system"].Value<bool>())
            {
                var esType = GameObjectLookup.FindComponentType("UnityEngine.EventSystems.EventSystem");
                bool hasActive = esType != null && objects.Any(go =>
                    go != null && go.activeInHierarchy && go.GetComponent(esType) != null);
                findings.Add(Finding("ui", hasActive,
                    hasActive ? "An active EventSystem is present."
                              : "No active EventSystem found in the scene.",
                    suggestedFix: hasActive ? null : "Add an EventSystem GameObject (GameObject > UI > Event System)."));
            }

            if (ui["require_graphic_raycaster"] != null && ui["require_graphic_raycaster"].Type == JTokenType.Boolean
                && ui["require_graphic_raycaster"].Value<bool>())
            {
                EvaluateGraphicRaycaster(objects, findings);
            }
        }

        // Every Canvas that contains an interactable Selectable must carry a
        // GraphicRaycaster (otherwise its buttons/toggles cannot receive input).
        private static void EvaluateGraphicRaycaster(List<GameObject> objects, List<object> findings)
        {
            var canvasType = GameObjectLookup.FindComponentType("UnityEngine.Canvas");
            var raycasterType = GameObjectLookup.FindComponentType("UnityEngine.UI.GraphicRaycaster");
            var selectableType = GameObjectLookup.FindComponentType("UnityEngine.UI.Selectable");

            if (canvasType == null || selectableType == null)
            {
                findings.Add(Finding("ui", true,
                    "GraphicRaycaster check skipped: uGUI (UnityEngine.UI) types are not available in this project."));
                return;
            }

            // Find root Canvases with any interactable Selectable in their subtree.
            int checkedCanvases = 0;
            int offenders = 0;
            foreach (var go in objects)
            {
                if (go == null) continue;
                var canvas = go.GetComponent(canvasType);
                if (canvas == null) continue;

                // Only consider a root canvas (no Canvas above it) to avoid double-counting.
                bool hasAncestorCanvas = go.GetComponentsInParent(canvasType, true)
                    .Any(c => c != null && c.gameObject != go);
                if (hasAncestorCanvas) continue;

                var selectables = go.GetComponentsInChildren(selectableType, true);
                bool hasInteractable = selectables.Any(IsInteractableSelectable);
                if (!hasInteractable) continue;

                checkedCanvases++;
                bool hasRaycaster = raycasterType != null && go.GetComponent(raycasterType) != null;
                if (!hasRaycaster)
                {
                    offenders++;
                    findings.Add(Finding("ui", false,
                        $"Canvas '{GameObjectLookup.GetGameObjectPath(go)}' has interactable UI but no GraphicRaycaster.",
                        objectPath: GameObjectLookup.GetGameObjectPath(go),
                        suggestedFix: "Add a GraphicRaycaster component to the Canvas."));
                }
            }

            if (checkedCanvases == 0)
            {
                findings.Add(Finding("ui", true, "No Canvas with interactable UI found (GraphicRaycaster not required)."));
            }
            else if (offenders == 0)
            {
                findings.Add(Finding("ui", true,
                    $"All {checkedCanvases} interactable Canvas(es) have a GraphicRaycaster."));
            }
        }

        private static bool IsInteractableSelectable(Component comp)
        {
            if (comp == null) return false;
            // Selectable exposes a bool 'interactable' and 'enabled'; read via reflection
            // so we don't need a compile-time reference to UnityEngine.UI.
            if (!(comp is Behaviour behaviour) || !behaviour.enabled) return false;
            if (!comp.gameObject.activeInHierarchy) return false;
            var prop = comp.GetType().GetProperty("interactable");
            if (prop == null) return true; // treat as interactable if we can't tell
            try
            {
                object value = prop.GetValue(comp);
                return value is bool b ? b : true;
            }
            catch
            {
                return true;
            }
        }

        // --- finding construction ------------------------------------------

        private static Dictionary<string, object> Finding(
            string rule, bool pass, string details,
            string severity = "error", string objectPath = null, string suggestedFix = null)
        {
            var f = new Dictionary<string, object>
            {
                ["rule"] = rule,
                ["status"] = pass ? "pass" : "fail",
                ["severity"] = severity,
                ["details"] = details,
            };
            if (objectPath != null) f["object_path"] = objectPath;
            if (suggestedFix != null) f["suggested_fix"] = suggestedFix;
            return f;
        }

        private static IEnumerable<string> AsStringList(JToken token)
        {
            var arr = ToolParams.CoerceStringArray(token);
            return arr ?? Array.Empty<string>();
        }

        private static int? AsInt(JToken token)
        {
            if (token == null) return null;
            if (token.Type == JTokenType.Integer) return token.Value<int>();
            if (token.Type == JTokenType.String && int.TryParse(token.ToString(), out int v)) return v;
            return null;
        }
    }
}
