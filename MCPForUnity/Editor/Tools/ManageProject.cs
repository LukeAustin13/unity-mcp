using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// Whole-project, READ-only health and intelligence scans: missing scripts,
    /// broken prefab instances, dangling serialized references, material/shader
    /// errors, and an asset-type inventory. Unlike manage_scene(validate) — which
    /// only checks the active scene's root objects — these walk assets on disk.
    ///
    /// Scans are synchronous and bounded: pass page_size + cursor to page over a
    /// stable GUID list, and max_issues to cap the returned example list. Scene
    /// assets are intentionally NOT opened here (that is disruptive and slow); use
    /// manage_scene(action=validate) per scene for in-scene checks.
    /// </summary>
    [McpForUnityTool("manage_project", AutoRegister = false, Group = "core")]
    public static class ManageProject
    {
        // Detection scope note surfaced in responses so callers know the limits.
        private const string DetectionNote =
            "Scans prefab and ScriptableObject assets on disk. Scene contents are not " +
            "opened here — use manage_scene(action=validate) for the active scene.";

        public static object HandleCommand(JObject @params)
        {
            string action = @params["action"]?.ToString()?.ToLowerInvariant();
            if (string.IsNullOrEmpty(action))
            {
                return new ErrorResponse("Action is required");
            }

            try
            {
                switch (action)
                {
                    case "validate_assets":
                        return ValidateAssets(@params);
                    case "validate_materials":
                        return ValidateMaterials(@params);
                    case "asset_inventory":
                        return AssetInventory(@params);
                    case "project_health":
                        return ProjectHealth(@params);
                    default:
                        return new ErrorResponse($"Unknown action: {action}");
                }
            }
            catch (Exception ex)
            {
                return new ErrorResponse(
                    $"Error in manage_project: {ex.Message}",
                    new { stackTrace = ex.StackTrace });
            }
        }

        // --- shared helpers -------------------------------------------------

        private static string[] ResolveFolders(ToolParams p)
        {
            var folders = new List<string>();
            var scope = p.GetStringArray("folder_scope");
            if (scope != null && scope.Length > 0)
            {
                foreach (var s in scope)
                {
                    var f = (s ?? string.Empty).Trim().TrimEnd('/');
                    if (!string.IsNullOrEmpty(f) && AssetDatabase.IsValidFolder(f))
                        folders.Add(f);
                }
            }
            if (folders.Count == 0)
                folders.Add("Assets");

            if (p.GetBool("include_packages", false) && !folders.Contains("Packages"))
                folders.Add("Packages");

            return folders.ToArray();
        }

        private static string[] FindGuids(string filter, string[] folders)
        {
            // AssetDatabase.FindAssets treats an empty searchInFolders as "everything";
            // we always pass explicit, validated folders.
            return AssetDatabase.FindAssets(filter, folders);
        }

        // --- validate_assets ------------------------------------------------

        private sealed class AssetScan
        {
            public int total;
            public int scanned;
            public int nextCursor = -1; // -1 => done
            public int missingScripts;
            public int missingPrefabs;
            public int danglingReferences;
            public readonly List<object> issues = new();
        }

        private static AssetScan ScanAssets(
            string[] folders, int startCursor, int pageSize, int maxIssues,
            HashSet<string> typeFilter)
        {
            // Stable, deduped, sorted GUID list so cursor paging is deterministic.
            var guids = new SortedSet<string>(StringComparer.Ordinal);
            foreach (var g in FindGuids("t:Prefab", folders)) guids.Add(g);
            foreach (var g in FindGuids("t:ScriptableObject", folders)) guids.Add(g);
            var ordered = guids.ToList();

            var scan = new AssetScan { total = ordered.Count };
            bool Want(string type) => typeFilter == null || typeFilter.Contains(type);

            int cursor = Mathf.Clamp(startCursor, 0, ordered.Count);
            int end = Mathf.Min(ordered.Count, cursor + pageSize);

            for (int i = cursor; i < end; i++)
            {
                string path = AssetDatabase.GUIDToAssetPath(ordered[i]);
                if (string.IsNullOrEmpty(path)) continue;
                scan.scanned++;

                var root = AssetDatabase.LoadAssetAtPath<GameObject>(path);
                if (root != null)
                {
                    ScanPrefab(root, path, scan, maxIssues, Want);
                    continue;
                }

                // Not a GameObject prefab — try as a ScriptableObject.
                var so = AssetDatabase.LoadAssetAtPath<ScriptableObject>(path);
                if (so != null)
                {
                    ScanObjectForDanglingRefs(so, path, so.GetType().Name, scan, maxIssues, Want);
                    continue;
                }

                // Could not load as a ScriptableObject. Only treat it as a missing
                // script when the asset has NO resolvable main type — that is the
                // reliable "broken/missing MonoScript" signal. A non-null main type
                // just means it is some other (non-SO) asset, not a break.
                if (Want("missing_scripts") && AssetDatabase.GetMainAssetTypeAtPath(path) == null)
                {
                    scan.missingScripts++;
                    if (scan.issues.Count < maxIssues)
                        scan.issues.Add(new { type = "missing_scripts", asset = path, context = "asset root", count = 1 });
                }
            }

            scan.nextCursor = end < ordered.Count ? end : -1;
            return scan;
        }

        private static void ScanPrefab(GameObject root, string path, AssetScan scan, int maxIssues, Func<string, bool> want)
        {
            foreach (var tr in root.GetComponentsInChildren<Transform>(true))
            {
                var go = tr.gameObject;

                if (want("missing_scripts"))
                {
                    int missing = GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(go);
                    if (missing > 0)
                    {
                        scan.missingScripts += missing;
                        if (scan.issues.Count < maxIssues)
                            scan.issues.Add(new { type = "missing_scripts", asset = path, context = go.name, count = missing });
                    }
                }

                if (want("missing_prefab"))
                {
                    var status = PrefabUtility.GetPrefabInstanceStatus(go);
                    if (status == PrefabInstanceStatus.MissingAsset)
                    {
                        scan.missingPrefabs++;
                        if (scan.issues.Count < maxIssues)
                            scan.issues.Add(new { type = "missing_prefab", asset = path, context = go.name, status = status.ToString() });
                    }
                }

                if (want("dangling_reference"))
                {
                    foreach (var comp in go.GetComponents<Component>())
                    {
                        if (comp == null) continue; // missing script already counted
                        ScanObjectForDanglingRefs(comp, path, $"{go.name}/{comp.GetType().Name}", scan, maxIssues, want);
                    }
                }
            }
        }

        private static void ScanObjectForDanglingRefs(UnityEngine.Object obj, string path, string context, AssetScan scan, int maxIssues, Func<string, bool> want)
        {
            if (obj == null || !want("dangling_reference")) return;
            using var so = new SerializedObject(obj);
            var it = so.GetIterator();
            bool enterChildren = true;
            while (it.NextVisible(enterChildren))
            {
                enterChildren = true;
                if (it.propertyType != SerializedPropertyType.ObjectReference) continue;
                // A reference that pointed at an object that no longer exists:
                // the value is null but the stored instance id is non-zero.
                if (it.objectReferenceValue == null && it.objectReferenceInstanceIDValue != 0)
                {
                    scan.danglingReferences++;
                    if (scan.issues.Count < maxIssues)
                        scan.issues.Add(new { type = "dangling_reference", asset = path, context, property = it.propertyPath });
                }
            }
        }

        private static object ValidateAssets(JObject @params)
        {
            var p = new ToolParams(@params);
            string[] folders = ResolveFolders(p);
            int pageSize = Mathf.Clamp(p.GetInt("page_size") ?? 100, 1, 1000);
            int cursor = Mathf.Max(0, p.GetInt("cursor") ?? 0);
            int maxIssues = Mathf.Clamp(p.GetInt("max_issues") ?? 200, 1, 2000);

            HashSet<string> typeFilter = null;
            var filter = p.GetStringArray("issue_types");
            if (filter != null && filter.Length > 0)
                typeFilter = new HashSet<string>(filter.Select(s => s.Trim().ToLowerInvariant()));

            var scan = ScanAssets(folders, cursor, pageSize, maxIssues, typeFilter);
            int totalIssues = scan.missingScripts + scan.missingPrefabs + scan.danglingReferences;
            string next = scan.nextCursor >= 0 ? scan.nextCursor.ToString() : null;

            var payload = new
            {
                folders,
                totalAssets = scan.total,
                scanned = scan.scanned,
                cursor,
                pageSize,
                next_cursor = next,
                truncated = next != null || scan.issues.Count >= maxIssues,
                counts = new
                {
                    missing_scripts = scan.missingScripts,
                    missing_prefab = scan.missingPrefabs,
                    dangling_reference = scan.danglingReferences,
                },
                totalIssues,
                issues = scan.issues,
                note = DetectionNote,
            };

            string message = totalIssues == 0
                ? $"Scanned {scan.scanned} asset(s): no reference issues in this page."
                : $"Scanned {scan.scanned} asset(s): {totalIssues} reference issue(s) in this page.";
            return new SuccessResponse(message, payload);
        }

        // --- validate_materials --------------------------------------------

        private sealed class MaterialScan
        {
            public int total;
            public int scanned;
            public int nextCursor = -1;
            public int missingShader;
            public int errorShader;
            public int unsupportedShader;
            public readonly List<object> issues = new();
        }

        private static MaterialScan ScanMaterials(string[] folders, int startCursor, int pageSize, int maxIssues)
        {
            var ordered = new SortedSet<string>(FindGuids("t:Material", folders), StringComparer.Ordinal).ToList();
            var scan = new MaterialScan { total = ordered.Count };

            int cursor = Mathf.Clamp(startCursor, 0, ordered.Count);
            int end = Mathf.Min(ordered.Count, cursor + pageSize);

            for (int i = cursor; i < end; i++)
            {
                string path = AssetDatabase.GUIDToAssetPath(ordered[i]);
                if (string.IsNullOrEmpty(path)) continue;
                var mat = AssetDatabase.LoadAssetAtPath<Material>(path);
                if (mat == null) continue;
                scan.scanned++;

                var shader = mat.shader;
                string issueType = null;
                if (shader == null)
                {
                    issueType = "missing_shader";
                    scan.missingShader++;
                }
                else if (shader.name == "Hidden/InternalErrorShader")
                {
                    issueType = "error_shader"; // the classic "pink material"
                    scan.errorShader++;
                }
                else if (!shader.isSupported)
                {
                    issueType = "unsupported_shader";
                    scan.unsupportedShader++;
                }

                if (issueType != null && scan.issues.Count < maxIssues)
                {
                    scan.issues.Add(new
                    {
                        type = issueType,
                        asset = path,
                        shader = shader != null ? shader.name : null,
                    });
                }
            }

            scan.nextCursor = end < ordered.Count ? end : -1;
            return scan;
        }

        private static object ValidateMaterials(JObject @params)
        {
            var p = new ToolParams(@params);
            string[] folders = ResolveFolders(p);
            int pageSize = Mathf.Clamp(p.GetInt("page_size") ?? 200, 1, 2000);
            int cursor = Mathf.Max(0, p.GetInt("cursor") ?? 0);
            int maxIssues = Mathf.Clamp(p.GetInt("max_issues") ?? 200, 1, 2000);

            var scan = ScanMaterials(folders, cursor, pageSize, maxIssues);
            int totalIssues = scan.missingShader + scan.errorShader + scan.unsupportedShader;
            string next = scan.nextCursor >= 0 ? scan.nextCursor.ToString() : null;

            var payload = new
            {
                folders,
                totalMaterials = scan.total,
                scanned = scan.scanned,
                cursor,
                pageSize,
                next_cursor = next,
                truncated = next != null || scan.issues.Count >= maxIssues,
                counts = new
                {
                    missing_shader = scan.missingShader,
                    error_shader = scan.errorShader,
                    unsupported_shader = scan.unsupportedShader,
                },
                totalIssues,
                issues = scan.issues,
            };

            string message = totalIssues == 0
                ? $"Scanned {scan.scanned} material(s): all shaders OK in this page."
                : $"Scanned {scan.scanned} material(s): {totalIssues} shader issue(s) in this page.";
            return new SuccessResponse(message, payload);
        }

        // --- asset_inventory ------------------------------------------------

        private static object AssetInventory(JObject @params)
        {
            var p = new ToolParams(@params);
            string[] folders = ResolveFolders(p);
            bool includeGuids = p.GetBool("include_guids", false);
            int maxScan = Mathf.Clamp(p.GetInt("max_issues") ?? 50000, 1, 200000);
            const int MaxGuidsPerType = 100;

            var guids = FindGuids(string.Empty, folders); // empty filter => all assets
            int total = guids.Length;
            int scanned = 0;

            var counts = new Dictionary<string, int>(StringComparer.Ordinal);
            var guidsByType = new Dictionary<string, List<string>>(StringComparer.Ordinal);

            foreach (var guid in guids)
            {
                if (scanned >= maxScan) break;
                string path = AssetDatabase.GUIDToAssetPath(guid);
                if (string.IsNullOrEmpty(path) || AssetDatabase.IsValidFolder(path)) continue;
                scanned++;

                var type = AssetDatabase.GetMainAssetTypeAtPath(path);
                string typeName = type != null ? type.Name : "Unknown";
                counts.TryGetValue(typeName, out int c);
                counts[typeName] = c + 1;

                if (includeGuids)
                {
                    if (!guidsByType.TryGetValue(typeName, out var list))
                    {
                        list = new List<string>();
                        guidsByType[typeName] = list;
                    }
                    if (list.Count < MaxGuidsPerType) list.Add(guid);
                }
            }

            var byType = counts
                .OrderByDescending(kv => kv.Value)
                .Select(kv =>
                {
                    List<string> g = null;
                    if (includeGuids) guidsByType.TryGetValue(kv.Key, out g);
                    return (object)new
                    {
                        type = kv.Key,
                        count = kv.Value,
                        guids = g,
                        guidsTruncated = includeGuids && kv.Value > MaxGuidsPerType,
                    };
                })
                .ToList();

            return new SuccessResponse(
                $"Inventoried {scanned} asset(s) across {counts.Count} type(s).",
                new
                {
                    folders,
                    totalAssets = total,
                    scanned,
                    truncated = scanned < total,
                    typeCount = counts.Count,
                    byType,
                });
        }

        // --- project_health -------------------------------------------------

        private static object ProjectHealth(JObject @params)
        {
            var p = new ToolParams(@params);
            string[] folders = ResolveFolders(p);
            // Bounded whole-project pass so we do not stall on huge projects.
            int assetBudget = Mathf.Clamp(p.GetInt("max_assets") ?? 5000, 1, 50000);
            const int exampleCap = 25;

            var assets = ScanAssets(folders, 0, assetBudget, exampleCap, null);
            var materials = ScanMaterials(folders, 0, assetBudget, exampleCap);

            bool assetsTruncated = assets.nextCursor >= 0;
            bool materialsTruncated = materials.nextCursor >= 0;

            int assetIssues = assets.missingScripts + assets.missingPrefabs + assets.danglingReferences;
            int materialIssues = materials.missingShader + materials.errorShader + materials.unsupportedShader;

            var report = new
            {
                healthy = assetIssues == 0 && materialIssues == 0,
                truncated = assetsTruncated || materialsTruncated,
                folders,
                assets = new
                {
                    healthy = assetIssues == 0,
                    scanned = assets.scanned,
                    total = assets.total,
                    truncated = assetsTruncated,
                    missing_scripts = assets.missingScripts,
                    missing_prefab = assets.missingPrefabs,
                    dangling_reference = assets.danglingReferences,
                    examples = assets.issues,
                },
                materials = new
                {
                    healthy = materialIssues == 0,
                    scanned = materials.scanned,
                    total = materials.total,
                    truncated = materialsTruncated,
                    missing_shader = materials.missingShader,
                    error_shader = materials.errorShader,
                    unsupported_shader = materials.unsupportedShader,
                    examples = materials.issues,
                },
                note =
                    "Asset + material health only. For console errors use read_console; " +
                    "for test health use run_tests_and_summarize; for the active scene use " +
                    "manage_scene(action=validate).",
            };

            string message = report.healthy
                ? "Project healthy: no asset or material issues found in the scanned scope."
                : $"Project issues found — assets: {assetIssues}, materials: {materialIssues}.";
            return new SuccessResponse(message, report);
        }
    }
}
