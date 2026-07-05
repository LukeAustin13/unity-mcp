using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build;
using UnityEngine;
using UnityEngine.Rendering;

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
                    case "get_dependencies":
                        return GetDependencies(@params);
                    case "find_references":
                        return FindReferences(@params);
                    case "unused_assets":
                        return UnusedAssets(@params);
                    case "audit_mobile":
                        return AuditMobile(@params);
                    case "prefab_health":
                        return PrefabHealth(@params);
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

        // --- dependency intelligence ---------------------------------------

        /// <summary>
        /// Resolve a caller-supplied target (asset path OR GUID) to an asset path.
        /// Returns null when it cannot be resolved to an existing asset.
        /// </summary>
        private static string ResolveTargetPath(string target)
        {
            if (string.IsNullOrEmpty(target)) return null;
            string t = target.Trim();

            // A GUID has no path separators; try GUID -> path first when it looks like one.
            if (!t.Contains("/") && !t.Contains("\\"))
            {
                string byGuid = AssetDatabase.GUIDToAssetPath(t);
                if (!string.IsNullOrEmpty(byGuid)) return byGuid;
            }

            // Otherwise treat it as a path — accept it only if the asset exists.
            if (AssetDatabase.GetMainAssetTypeAtPath(t) != null || AssetDatabase.IsValidFolder(t))
                return t;

            return null;
        }

        private static object DescribeAsset(string path)
        {
            var type = AssetDatabase.GetMainAssetTypeAtPath(path);
            return new
            {
                path,
                type = type != null ? type.Name : "Unknown",
                guid = AssetDatabase.AssetPathToGUID(path),
            };
        }

        // --- get_dependencies ----------------------------------------------

        private static object GetDependencies(JObject @params)
        {
            var p = new ToolParams(@params);
            string target = p.Get("target");
            if (string.IsNullOrEmpty(target))
                return new ErrorResponse("'target' parameter is required (asset path or GUID).");

            string path = ResolveTargetPath(target);
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse($"Could not resolve target '{target}' to an asset path or GUID.");

            bool recursive = p.GetBool("recursive", false);
            int pageSize = Mathf.Clamp(p.GetInt("page_size") ?? 100, 1, 1000);
            int cursor = Mathf.Max(0, p.GetInt("cursor") ?? 0);

            // Stable, deduped, sorted list so cursor paging is deterministic; drop
            // the target itself so we only report what it depends on.
            var deps = new SortedSet<string>(
                AssetDatabase.GetDependencies(path, recursive), StringComparer.Ordinal);
            deps.Remove(path);
            var ordered = deps.ToList();

            int start = Mathf.Clamp(cursor, 0, ordered.Count);
            int end = Mathf.Min(ordered.Count, start + pageSize);
            var page = new List<object>();
            for (int i = start; i < end; i++)
                page.Add(DescribeAsset(ordered[i]));

            string next = end < ordered.Count ? end.ToString() : null;

            var payload = new
            {
                target = DescribeAsset(path),
                recursive,
                totalDependencies = ordered.Count,
                cursor = start,
                pageSize,
                next_cursor = next,
                truncated = next != null,
                dependencies = page,
            };
            return new SuccessResponse(
                $"{ordered.Count} dependenc(ies) for '{path}'{(recursive ? " (recursive)" : "")}.",
                payload);
        }

        // --- find_references -----------------------------------------------

        private static object FindReferences(JObject @params)
        {
            var p = new ToolParams(@params);
            string target = p.Get("target");
            if (string.IsNullOrEmpty(target))
                return new ErrorResponse("'target' parameter is required (asset path or GUID).");

            string path = ResolveTargetPath(target);
            if (string.IsNullOrEmpty(path))
                return new ErrorResponse($"Could not resolve target '{target}' to an asset path or GUID.");

            string[] folders = ResolveFolders(p);
            int pageSize = Mathf.Clamp(p.GetInt("page_size") ?? 100, 1, 1000);
            int cursor = Mathf.Max(0, p.GetInt("cursor") ?? 0);
            // Cap the reverse scan so a huge project cannot stall the editor.
            int maxScan = Mathf.Clamp(p.GetInt("max_issues") ?? 20000, 1, 200000);

            var ordered = new SortedSet<string>(
                FindGuids(string.Empty, folders), StringComparer.Ordinal).ToList();

            int start = Mathf.Clamp(cursor, 0, ordered.Count);
            var referencing = new List<object>();
            int scanned = 0;
            int i = start;
            bool capHit = false;
            for (; i < ordered.Count; i++)
            {
                if (scanned >= maxScan) { capHit = true; break; }
                if (referencing.Count >= pageSize) break;

                string candPath = AssetDatabase.GUIDToAssetPath(ordered[i]);
                if (string.IsNullOrEmpty(candPath) || candPath == path) continue;
                if (AssetDatabase.IsValidFolder(candPath)) continue;
                scanned++;

                // Direct (non-recursive) dependencies only: does this candidate use target?
                if (AssetDatabase.GetDependencies(candPath, false).Contains(path))
                    referencing.Add(DescribeAsset(candPath));
            }

            string next = i < ordered.Count && !capHit ? i.ToString() : null;

            var payload = new
            {
                target = DescribeAsset(path),
                folders,
                totalCandidates = ordered.Count,
                scanned_count = scanned,
                cursor = start,
                pageSize,
                next_cursor = next,
                truncated = next != null || capHit,
                referencing,
            };
            return new SuccessResponse(
                $"Scanned {scanned} candidate(s): {referencing.Count} direct reference(s) to '{path}' in this page.",
                payload);
        }

        // --- unused_assets --------------------------------------------------

        private static readonly string[] UnusedCaveats =
        {
            "Advisory only — never deletes anything. Verify before removing any asset.",
            "Cannot see Addressables or AssetBundle references.",
            "Cannot see reflection/dynamic loads. Resources.Load(string) is covered only " +
            "because every asset under a Resources/ folder is treated as a root.",
            "Cannot see references from external tooling or code that constructs paths at runtime.",
            "Scripts (.cs), folders, and assets under Editor/ folders are excluded from the unused verdict.",
        };

        private static bool IsUnderEditorFolder(string path)
        {
            // Any path segment named exactly "Editor" makes this an editor-only asset.
            foreach (var seg in path.Split('/'))
                if (string.Equals(seg, "Editor", StringComparison.Ordinal))
                    return true;
            return false;
        }

        private static object UnusedAssets(JObject @params)
        {
            var p = new ToolParams(@params);
            string[] folders = ResolveFolders(p);
            int pageSize = Mathf.Clamp(p.GetInt("page_size") ?? 100, 1, 1000);
            int cursor = Mathf.Max(0, p.GetInt("cursor") ?? 0);
            int maxScan = Mathf.Clamp(p.GetInt("max_issues") ?? 20000, 1, 200000);

            // --- collect roots (cheap enumerations only) ---
            var roots = new List<string>();
            foreach (var scene in EditorBuildSettings.scenes)
            {
                if (scene != null && scene.enabled && !string.IsNullOrEmpty(scene.path))
                    roots.Add(scene.path);
            }
            // Everything under any Resources/ folder and any StreamingAssets folder.
            foreach (var guid in AssetDatabase.FindAssets(string.Empty, new[] { "Assets" }))
            {
                string rp = AssetDatabase.GUIDToAssetPath(guid);
                if (string.IsNullOrEmpty(rp) || AssetDatabase.IsValidFolder(rp)) continue;
                if (rp.Contains("/Resources/") || rp.Contains("/StreamingAssets/"))
                    roots.Add(rp);
            }

            // reachable = union of GetDependencies(root, true) for every root.
            var reachable = new HashSet<string>(StringComparer.Ordinal);
            foreach (var root in roots)
            {
                foreach (var dep in AssetDatabase.GetDependencies(root, true))
                    reachable.Add(dep);
                reachable.Add(root);
            }

            var ordered = new SortedSet<string>(
                FindGuids(string.Empty, folders), StringComparer.Ordinal).ToList();

            int start = Mathf.Clamp(cursor, 0, ordered.Count);
            var unused = new List<object>();
            int scanned = 0;
            int i = start;
            bool capHit = false;
            for (; i < ordered.Count; i++)
            {
                if (scanned >= maxScan) { capHit = true; break; }
                if (unused.Count >= pageSize) break;

                string path = AssetDatabase.GUIDToAssetPath(ordered[i]);
                if (string.IsNullOrEmpty(path)) continue;
                if (AssetDatabase.IsValidFolder(path)) continue;
                // Never flag scripts, editor-only assets — they are not "unused content".
                if (path.EndsWith(".cs", StringComparison.OrdinalIgnoreCase)) continue;
                if (IsUnderEditorFolder(path)) continue;
                scanned++;

                if (!reachable.Contains(path))
                    unused.Add(DescribeAsset(path));
            }

            string next = i < ordered.Count && !capHit ? i.ToString() : null;

            var payload = new
            {
                folders,
                rootCount = roots.Count,
                reachableCount = reachable.Count,
                totalCandidates = ordered.Count,
                scanned_count = scanned,
                cursor = start,
                pageSize,
                next_cursor = next,
                truncated = next != null || capHit,
                unused,
                caveats = UnusedCaveats,
            };
            return new SuccessResponse(
                $"Advisory: {unused.Count} possibly-unused asset(s) in this page (scanned {scanned}). " +
                "Read 'caveats' before acting — this never deletes anything.",
                payload);
        }

        // --- audit_mobile ---------------------------------------------------
        //
        // A data-driven mobile-performance heuristic scan. Assets are paged and
        // capped exactly like the other asset scans (stable GUID cursor). Project
        // settings and the active scene are single, un-paged advisory passes. This
        // NEVER modifies anything — it only reports findings and fixes.

        private static readonly string[] AuditCaveats =
        {
            "Static heuristics only — not a profiler. Findings are guidance, not measurements.",
            "Thresholds are conservative rules of thumb; the right value depends on your target device and art style.",
            "Addressables and AssetBundles are not analyzed; per-platform asset overrides beyond textures are not fully modeled.",
            "Project-setting checks target Android/iOS; a rule_error entry means that specific check could not run on this Unity version.",
            "The active-scene pass reflects only the currently open scene's objects, not scenes loaded at runtime.",
            "Never modifies anything — this is a read-only report.",
        };

        /// <summary>A single mobile-audit rule. Static metadata; the predicate side lives in each check.</summary>
        private sealed class MobileRule
        {
            public readonly string Id;
            public readonly string Category;   // texture | audio | model | player_settings | quality_settings | scene
            public readonly string Severity;   // info | warn | critical
            public readonly string Message;
            public readonly string Fix;

            public MobileRule(string id, string category, string severity, string message, string fix)
            {
                Id = id; Category = category; Severity = severity; Message = message; Fix = fix;
            }
        }

        // The static rule table. Findings reference these by Id so the catalog is
        // the single source of truth for message/fix/severity.
        private static readonly Dictionary<string, MobileRule> MobileRules = new(StringComparer.Ordinal)
        {
            // Textures
            ["texture_no_mobile_override"] = new MobileRule(
                "texture_no_mobile_override", "texture", "warn",
                "Texture has no Android/iOS platform override and its default format is uncompressed or high-size.",
                "Add an Android and iOS override in the texture's import settings and pick a compressed format (ASTC)."),
            ["texture_max_size_too_large"] = new MobileRule(
                "texture_max_size_too_large", "texture", "warn",
                "Max texture size exceeds 2048, which is large for mobile GPUs and memory.",
                "Lower Max Size to 2048 or below (often 1024) unless the texture genuinely needs the resolution."),
            ["texture_readable"] = new MobileRule(
                "texture_readable", "texture", "warn",
                "Read/Write Enabled keeps a second CPU copy of the texture, doubling its memory footprint.",
                "Disable Read/Write Enabled unless the texture is sampled on the CPU (GetPixels/Readback)."),
            ["texture_no_mipmaps_3d"] = new MobileRule(
                "texture_no_mipmaps_3d", "texture", "info",
                "Mipmaps are disabled on a texture that is likely used in 3D, causing shimmering and wasted bandwidth at distance.",
                "Enable Generate Mip Maps for textures on 3D meshes (leave off for UI/sprites)."),
            // Audio
            ["audio_decompress_on_load"] = new MobileRule(
                "audio_decompress_on_load", "audio", "warn",
                "Clip uses Decompress On Load, which holds the fully decompressed clip in memory.",
                "Use Compressed In Memory for medium clips, or Streaming for long music tracks."),
            ["audio_decompress_pcm"] = new MobileRule(
                "audio_decompress_pcm", "audio", "critical",
                "Clip is Decompress On Load AND stored as uncompressed PCM — worst case for mobile memory.",
                "Set the compression format to Vorbis (or ADPCM) and use Compressed In Memory / Streaming."),
            ["audio_preload_large"] = new MobileRule(
                "audio_preload_large", "audio", "info",
                "Preload Audio Data is enabled on a large clip, extending scene load time and memory.",
                "Disable Preload Audio Data for large or infrequently-used clips."),
            // Models
            ["model_readable"] = new MobileRule(
                "model_readable", "model", "warn",
                "Model has Read/Write Enabled, keeping a CPU copy of every mesh and doubling mesh memory.",
                "Disable Read/Write Enabled unless meshes are read on the CPU at runtime."),
            ["model_mesh_uncompressed"] = new MobileRule(
                "model_mesh_uncompressed", "model", "info",
                "Mesh Compression is Off; enabling it shrinks on-disk and runtime mesh size.",
                "Set Mesh Compression to Low/Medium for models that do not need full vertex precision."),
            // Player settings
            ["player_scripting_backend"] = new MobileRule(
                "player_scripting_backend", "player_settings", "critical",
                "Android scripting backend is not IL2CPP; Mono is slower and cannot ship 64-bit-only to Google Play.",
                "Set Player Settings > Android > Scripting Backend to IL2CPP."),
            ["player_missing_arm64"] = new MobileRule(
                "player_missing_arm64", "player_settings", "critical",
                "Android target architectures do not include ARM64, which Google Play requires.",
                "Enable ARM64 under Player Settings > Android > Target Architectures."),
            ["player_gles2"] = new MobileRule(
                "player_gles2", "player_settings", "warn",
                "Android graphics APIs are set manually and still include OpenGLES2, which lacks modern features and is slow.",
                "Remove OpenGLES2 from the Android graphics API list; prefer Vulkan and OpenGLES3."),
            // Quality settings
            ["quality_msaa_high"] = new MobileRule(
                "quality_msaa_high", "quality_settings", "warn",
                "Anti-aliasing (MSAA) is above 4x, which is expensive on mobile GPU bandwidth.",
                "Lower Anti Aliasing to 2x or 4x, or use a post-process AA in your render pipeline."),
            ["quality_shadow_distance"] = new MobileRule(
                "quality_shadow_distance", "quality_settings", "warn",
                "Shadow distance is high; large shadow distances multiply shadow-map cost on mobile.",
                "Reduce Shadow Distance (often 20-50) so shadows only render near the camera."),
            ["quality_pixel_lights"] = new MobileRule(
                "quality_pixel_lights", "quality_settings", "warn",
                "Pixel Light Count is high; each extra per-pixel light adds a full lighting pass.",
                "Lower Pixel Light Count (1-2 is typical for mobile) and bake static lighting."),
            ["quality_realtime_reflections"] = new MobileRule(
                "quality_realtime_reflections", "quality_settings", "info",
                "Realtime reflection probes are enabled, which re-render the scene each update.",
                "Disable Realtime Reflection Probes or switch probes to Baked for mobile."),
            // Scene
            ["scene_many_realtime_lights"] = new MobileRule(
                "scene_many_realtime_lights", "scene", "warn",
                "The active scene has more than 4 realtime lights; realtime lighting is costly on mobile.",
                "Bake static lights and keep only a few realtime lights (ideally one directional)."),
            ["scene_many_cameras"] = new MobileRule(
                "scene_many_cameras", "scene", "info",
                "The active scene has more than 2 enabled cameras; each extra camera is another full render.",
                "Reduce the number of active cameras or disable ones that are not needed every frame."),
        };

        private sealed class MobileAudit
        {
            public readonly List<object> findings = new();
            public readonly List<object> ruleErrors = new();
            public readonly Dictionary<string, int> bySeverity = new(StringComparer.Ordinal);
            public readonly Dictionary<string, int> byCategory = new(StringComparer.Ordinal);
            public int scannedCount;
            public bool truncated;
            public int nextCursor = -1;

            public void Add(string ruleId, string assetPath)
            {
                if (!MobileRules.TryGetValue(ruleId, out var rule)) return;
                findings.Add(new
                {
                    rule_id = rule.Id,
                    category = rule.Category,
                    severity = rule.Severity,
                    asset_path = assetPath,
                    message = rule.Message,
                    fix = rule.Fix,
                });
                bySeverity.TryGetValue(rule.Severity, out int s);
                bySeverity[rule.Severity] = s + 1;
                byCategory.TryGetValue(rule.Category, out int c);
                byCategory[rule.Category] = c + 1;
            }

            public void RuleError(string ruleId, string reason)
            {
                ruleErrors.Add(new { rule_id = ruleId, error = reason });
            }
        }

        private static object AuditMobile(JObject @params)
        {
            var p = new ToolParams(@params);
            string[] folders = ResolveFolders(p);
            int pageSize = Mathf.Clamp(p.GetInt("page_size") ?? 200, 1, 2000);
            int cursor = Mathf.Max(0, p.GetInt("cursor") ?? 0);
            int maxIssues = Mathf.Clamp(p.GetInt("max_issues") ?? 500, 1, 5000);

            var audit = new MobileAudit();

            // (1) ASSET pass — paged over a stable, deduped GUID list.
            AuditAssets(folders, cursor, pageSize, maxIssues, audit);

            // (2) PROJECT SETTINGS + (3) ACTIVE SCENE — only on the first page so
            //     paging over assets does not re-report them on every page.
            if (cursor == 0)
            {
                AuditPlayerSettings(audit);
                AuditQualitySettings(audit);
                AuditActiveScene(audit);
            }

            string next = audit.nextCursor >= 0 ? audit.nextCursor.ToString() : null;
            audit.truncated = next != null || audit.findings.Count >= maxIssues;

            var summary = new
            {
                by_severity = audit.bySeverity,
                by_category = audit.byCategory,
                scanned_count = audit.scannedCount,
                truncated = audit.truncated,
            };

            var payload = new
            {
                folders,
                cursor,
                pageSize,
                next_cursor = next,
                findings = audit.findings,
                summary,
                rule_errors = audit.ruleErrors,
                caveats = AuditCaveats,
            };

            int critical = audit.bySeverity.TryGetValue("critical", out int cc) ? cc : 0;
            int warn = audit.bySeverity.TryGetValue("warn", out int wc) ? wc : 0;
            string message = audit.findings.Count == 0
                ? "Mobile audit: no findings in this scope (heuristics only — read 'caveats')."
                : $"Mobile audit: {audit.findings.Count} finding(s) in this page ({critical} critical, {warn} warn). Read 'caveats'.";
            return new SuccessResponse(message, payload);
        }

        private static void AuditAssets(string[] folders, int startCursor, int pageSize, int maxIssues, MobileAudit audit)
        {
            // Stable, deduped, sorted GUID list across texture/audio/model importers.
            var guids = new SortedSet<string>(StringComparer.Ordinal);
            foreach (var g in FindGuids("t:Texture2D", folders)) guids.Add(g);
            foreach (var g in FindGuids("t:AudioClip", folders)) guids.Add(g);
            foreach (var g in FindGuids("t:Model", folders)) guids.Add(g);
            var ordered = guids.ToList();

            int cursor = Mathf.Clamp(startCursor, 0, ordered.Count);
            int end = Mathf.Min(ordered.Count, cursor + pageSize);

            for (int i = cursor; i < end; i++)
            {
                if (audit.findings.Count >= maxIssues) break;
                string path = AssetDatabase.GUIDToAssetPath(ordered[i]);
                if (string.IsNullOrEmpty(path)) continue;

                var importer = AssetImporter.GetAtPath(path);
                if (importer == null) continue;
                audit.scannedCount++;

                switch (importer)
                {
                    case TextureImporter ti:
                        AuditTexture(ti, path, audit);
                        break;
                    case ModelImporter mi:
                        AuditModel(mi, path, audit);
                        break;
                    case AudioImporter ai:
                        AuditAudio(ai, path, audit);
                        break;
                }
            }

            audit.nextCursor = end < ordered.Count ? end : -1;
        }

        private static void AuditTexture(TextureImporter ti, string path, MobileAudit audit)
        {
            try
            {
                // No Android/iOS override AND an uncompressed/high-size default.
                bool hasAndroid = false, hasiOS = false;
                try { hasAndroid = ti.GetPlatformTextureSettings("Android").overridden; } catch { }
                try { hasiOS = ti.GetPlatformTextureSettings("iPhone").overridden; } catch { }
                var def = ti.GetDefaultPlatformTextureSettings();
                bool uncompressed = def.textureCompression == TextureImporterCompression.Uncompressed;
                bool highSize = def.maxTextureSize > 2048;
                if (!hasAndroid && !hasiOS && (uncompressed || highSize))
                    audit.Add("texture_no_mobile_override", path);

                if (ti.maxTextureSize > 2048)
                    audit.Add("texture_max_size_too_large", path);

                if (ti.isReadable)
                    audit.Add("texture_readable", path);

                // Mipmaps off on a likely-3D texture (Default type, not a Sprite/UI/GUI).
                if (!ti.mipmapEnabled && ti.textureType == TextureImporterType.Default)
                    audit.Add("texture_no_mipmaps_3d", path);
            }
            catch (Exception ex)
            {
                audit.RuleError("texture", $"{path}: {ex.Message}");
            }
        }

        private static void AuditAudio(AudioImporter ai, string path, MobileAudit audit)
        {
            try
            {
                var s = ai.defaultSampleSettings;
                bool decompressOnLoad = s.loadType == AudioClipLoadType.DecompressOnLoad;
                bool pcm = s.compressionFormat == AudioCompressionFormat.PCM;

                if (decompressOnLoad && pcm)
                    audit.Add("audio_decompress_pcm", path);
                else if (decompressOnLoad)
                    audit.Add("audio_decompress_on_load", path);

                // "Large" heuristic by file size on disk; avoids loading the clip.
                if (ai.preloadAudioData && IsLargeFile(path, 1_000_000))
                    audit.Add("audio_preload_large", path);
            }
            catch (Exception ex)
            {
                audit.RuleError("audio", $"{path}: {ex.Message}");
            }
        }

        private static void AuditModel(ModelImporter mi, string path, MobileAudit audit)
        {
            try
            {
                if (mi.isReadable)
                    audit.Add("model_readable", path);
                if (mi.meshCompression == ModelImporterMeshCompression.Off)
                    audit.Add("model_mesh_uncompressed", path);
            }
            catch (Exception ex)
            {
                audit.RuleError("model", $"{path}: {ex.Message}");
            }
        }

        private static bool IsLargeFile(string assetPath, long thresholdBytes)
        {
            try
            {
                var info = new System.IO.FileInfo(assetPath);
                return info.Exists && info.Length >= thresholdBytes;
            }
            catch { return false; }
        }

        private static void AuditPlayerSettings(MobileAudit audit)
        {
            var android = NamedBuildTarget.FromBuildTargetGroup(BuildTargetGroup.Android);

            // Scripting backend != IL2CPP.
            try
            {
                if (PlayerSettings.GetScriptingBackend(android) != ScriptingImplementation.IL2CPP)
                    audit.Add("player_scripting_backend", null);
            }
            catch (Exception ex) { audit.RuleError("player_scripting_backend", ex.Message); }

            // Target architectures missing ARM64. PlayerSettings.Android.targetArchitectures
            // is a long-stable flags enum; ARM64 == value 2.
            try
            {
                var arch = PlayerSettings.Android.targetArchitectures;
                if ((arch & AndroidArchitecture.ARM64) == 0)
                    audit.Add("player_missing_arm64", null);
            }
            catch (Exception ex) { audit.RuleError("player_missing_arm64", ex.Message); }

            // Manual graphics APIs that still include GLES2.
            try
            {
                if (!PlayerSettings.GetUseDefaultGraphicsAPIs(BuildTarget.Android))
                {
                    var apis = PlayerSettings.GetGraphicsAPIs(BuildTarget.Android);
                    if (apis != null && apis.Contains(GraphicsDeviceType.OpenGLES2))
                        audit.Add("player_gles2", null);
                }
            }
            catch (Exception ex) { audit.RuleError("player_gles2", ex.Message); }
        }

        private static void AuditQualitySettings(MobileAudit audit)
        {
            try
            {
                if (QualitySettings.antiAliasing > 4)
                    audit.Add("quality_msaa_high", null);
            }
            catch (Exception ex) { audit.RuleError("quality_msaa_high", ex.Message); }

            try
            {
                if (QualitySettings.shadowDistance > 60f)
                    audit.Add("quality_shadow_distance", null);
            }
            catch (Exception ex) { audit.RuleError("quality_shadow_distance", ex.Message); }

            try
            {
                if (QualitySettings.pixelLightCount > 2)
                    audit.Add("quality_pixel_lights", null);
            }
            catch (Exception ex) { audit.RuleError("quality_pixel_lights", ex.Message); }

            try
            {
                if (QualitySettings.realtimeReflectionProbes)
                    audit.Add("quality_realtime_reflections", null);
            }
            catch (Exception ex) { audit.RuleError("quality_realtime_reflections", ex.Message); }
        }

        private static void AuditActiveScene(MobileAudit audit)
        {
            try
            {
                var scene = UnityEngine.SceneManagement.SceneManager.GetActiveScene();
                if (!scene.IsValid() || !scene.isLoaded) return;

                int realtimeLights = 0;
                int enabledCameras = 0;
                foreach (var root in scene.GetRootGameObjects())
                {
                    foreach (var light in root.GetComponentsInChildren<Light>(true))
                    {
                        if (light != null && light.lightmapBakeType == LightmapBakeType.Realtime)
                            realtimeLights++;
                    }
                    foreach (var cam in root.GetComponentsInChildren<Camera>(true))
                    {
                        if (cam != null && cam.enabled)
                            enabledCameras++;
                    }
                }

                if (realtimeLights > 4)
                    audit.Add("scene_many_realtime_lights", null);
                if (enabledCameras > 2)
                    audit.Add("scene_many_cameras", null);
            }
            catch (Exception ex)
            {
                audit.RuleError("scene", ex.Message);
            }
        }

        // --- prefab_health --------------------------------------------------
        //
        // Deep, READ-only validation of prefab assets on disk. Prefabs are loaded
        // with AssetDatabase.LoadAssetAtPath<GameObject> (never PrefabUtility
        // .LoadPrefabContents, never instantiated into a scene) and their component
        // graph is inspected. Each per-prefab check group is wrapped in try/catch so
        // one broken prefab or Unity API mismatch cannot kill the scan. Paged over a
        // stable GUID cursor exactly like validate_assets/audit_mobile.

        private static readonly string[] PrefabHealthCaveats =
        {
            "Read-only: prefabs are loaded via AssetDatabase, never opened/instantiated or saved.",
            "Static inspection only — findings are guidance, not a runtime guarantee.",
            "disabled_critical, unnamed_layer, nested_prefab_depth and large_bounds are advisory: they are often intentional.",
            "material_issues sees only Renderer.sharedMaterials on the asset; runtime-assigned materials are not visible.",
            "A rule_error entry means that specific check could not run on this prefab/Unity version.",
        };

        private sealed class PrefabHealthScan
        {
            public int total;
            public int scanned;
            public int nextCursor = -1; // -1 => done
            public readonly List<object> findings = new();
            public readonly List<object> ruleErrors = new();
            public readonly Dictionary<string, int> bySeverity = new(StringComparer.Ordinal);
            public readonly Dictionary<string, int> byCheck = new(StringComparer.Ordinal);

            public void Add(string check, string severity, bool advisory, string prefabPath,
                string objectPath, string reason, string suggestedFix)
            {
                if (findings.Count >= MaxFindings) return;
                findings.Add(new
                {
                    check,
                    severity,
                    advisory,
                    prefab_path = prefabPath,
                    object_path = objectPath,
                    reason,
                    suggested_fix = suggestedFix,
                });
                bySeverity.TryGetValue(severity, out int s);
                bySeverity[severity] = s + 1;
                byCheck.TryGetValue(check, out int c);
                byCheck[check] = c + 1;
            }

            public void RuleError(string check, string prefabPath, string reason)
            {
                ruleErrors.Add(new { check, prefab_path = prefabPath, error = reason });
            }

            public bool Full => findings.Count >= MaxFindings;
            public int MaxFindings = 200;
        }

        private static object PrefabHealth(JObject @params)
        {
            var p = new ToolParams(@params);
            bool includeVariants = p.GetBool("include_variants", true);
            int pageSize = Mathf.Clamp(p.GetInt("page_size") ?? 50, 1, 200);
            int cursor = Mathf.Max(0, p.GetInt("cursor") ?? 0);
            int maxIssues = Mathf.Clamp(p.GetInt("max_issues") ?? 200, 1, 2000);

            var scan = new PrefabHealthScan { MaxFindings = maxIssues };

            // Single-prefab mode: prefab_path wins over folder when both are given.
            string prefabPath = p.Get("prefab_path");
            object dependencies = null;

            if (!string.IsNullOrEmpty(prefabPath))
            {
                prefabPath = prefabPath.Trim();
                var root = AssetDatabase.LoadAssetAtPath<GameObject>(prefabPath);
                if (root == null || !IsPrefabAsset(prefabPath))
                    return new ErrorResponse(
                        $"'{prefabPath}' was not found or is not a prefab asset.");

                scan.total = 1;
                scan.scanned = 1;
                ScanPrefabHealth(root, prefabPath, includeVariants, scan);
                dependencies = BuildDependencies(prefabPath);
            }
            else
            {
                string folder = (p.Get("folder") ?? "Assets").Trim().TrimEnd('/');
                if (string.IsNullOrEmpty(folder) || !AssetDatabase.IsValidFolder(folder))
                    folder = "Assets";
                var folders = new[] { folder };

                var ordered = new SortedSet<string>(
                    FindGuids("t:Prefab", folders), StringComparer.Ordinal).ToList();
                scan.total = ordered.Count;

                int start = Mathf.Clamp(cursor, 0, ordered.Count);
                int end = Mathf.Min(ordered.Count, start + pageSize);
                for (int i = start; i < end; i++)
                {
                    if (scan.Full) { end = i; break; }
                    string path = AssetDatabase.GUIDToAssetPath(ordered[i]);
                    if (string.IsNullOrEmpty(path)) continue;
                    var root = AssetDatabase.LoadAssetAtPath<GameObject>(path);
                    if (root == null) continue;
                    scan.scanned++;
                    ScanPrefabHealth(root, path, includeVariants, scan);
                }
                scan.nextCursor = end < ordered.Count ? end : -1;
            }

            string next = scan.nextCursor >= 0 ? scan.nextCursor.ToString() : null;
            bool truncated = next != null || scan.Full;

            var summary = new
            {
                by_severity = scan.bySeverity,
                by_check = scan.byCheck,
                truncated,
            };

            var payload = new
            {
                prefabs_scanned = scan.scanned,
                findings = scan.findings,
                summary,
                next_cursor = next,
                rule_errors = scan.ruleErrors,
                dependencies,
                caveats = PrefabHealthCaveats,
            };

            int errors = scan.bySeverity.TryGetValue("error", out int ec) ? ec : 0;
            int warnings = scan.bySeverity.TryGetValue("warning", out int wc) ? wc : 0;
            string message = scan.findings.Count == 0
                ? $"Prefab health: {scan.scanned} prefab(s) scanned, no findings in this page."
                : $"Prefab health: {scan.findings.Count} finding(s) in this page ({errors} error, {warnings} warning). Read 'caveats'.";
            return new SuccessResponse(message, payload);
        }

        private static bool IsPrefabAsset(string path)
        {
            if (string.IsNullOrEmpty(path)) return false;
            return path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase)
                && AssetDatabase.GetMainAssetTypeAtPath(path) == typeof(GameObject);
        }

        private static object BuildDependencies(string prefabPath)
        {
            try
            {
                const int cap = 100;
                var deps = new SortedSet<string>(
                    AssetDatabase.GetDependencies(prefabPath, false), StringComparer.Ordinal);
                deps.Remove(prefabPath);
                var all = deps.ToList();
                var page = all.Take(cap).ToList();
                return new
                {
                    total = all.Count,
                    truncated = all.Count > cap,
                    paths = page,
                };
            }
            catch
            {
                return null;
            }
        }

        private static void ScanPrefabHealth(GameObject root, string path, bool includeVariants, PrefabHealthScan scan)
        {
            // broken_variant / variant filtering — evaluated once per asset.
            bool isVariant = false;
            try
            {
                var assetType = PrefabUtility.GetPrefabAssetType(root);
                isVariant = assetType == PrefabAssetType.Variant;
                if (isVariant)
                {
                    // A variant whose base prefab no longer resolves is broken.
                    var basePrefab = PrefabUtility.GetCorrespondingObjectFromSource(root);
                    if (basePrefab == null)
                    {
                        scan.Add("broken_variant", "error", false, path, "",
                            "Prefab variant's base prefab is missing or could not be resolved.",
                            "Restore the base prefab or recreate this variant from an existing base.");
                    }
                }
            }
            catch (Exception ex) { scan.RuleError("broken_variant", path, ex.Message); }

            // include_variants=false skips further per-object checks on variants (but
            // the broken_variant error above always reports, as it is the point).
            if (isVariant && !includeVariants) return;

            var definedTags = SafeTags();

            foreach (var tr in root.GetComponentsInChildren<Transform>(true))
            {
                if (scan.Full) return;
                var go = tr.gameObject;
                string objectPath = HierarchyPath(root.transform, tr);

                // missing_scripts
                try
                {
                    int missing = GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(go);
                    if (missing > 0)
                        scan.Add("missing_scripts", "error", false, path, objectPath,
                            $"{missing} MonoBehaviour(s) with a missing script.",
                            "Reassign or remove the missing MonoBehaviour component(s).");
                }
                catch (Exception ex) { scan.RuleError("missing_scripts", path, ex.Message); }

                var components = go.GetComponents<Component>();

                // missing_references (dangling serialized object refs)
                try
                {
                    foreach (var comp in components)
                    {
                        if (comp == null) continue; // missing script counted above
                        ScanDanglingRefs(comp, path, objectPath, comp.GetType().Name, scan);
                        if (scan.Full) return;
                    }
                }
                catch (Exception ex) { scan.RuleError("missing_references", path, ex.Message); }

                // duplicate_components
                try
                {
                    var seen = new Dictionary<Type, int>();
                    foreach (var comp in components)
                    {
                        if (comp == null) continue;
                        var t = comp.GetType();
                        seen.TryGetValue(t, out int n);
                        seen[t] = n + 1;
                    }
                    foreach (var kv in seen)
                    {
                        if (kv.Value >= 2 && !DuplicatesAllowed(kv.Key))
                            scan.Add("duplicate_components", "info", true, path, objectPath,
                                $"{kv.Value} components of the same type '{kv.Key.Name}' on one GameObject.",
                                "Remove the redundant duplicate component(s) unless duplicates are intended.");
                    }
                }
                catch (Exception ex) { scan.RuleError("duplicate_components", path, ex.Message); }

                // invalid_tag
                try
                {
                    string tag = null;
                    try { tag = go.tag; } catch { /* reading an undefined tag can throw */ }
                    if (!string.IsNullOrEmpty(tag) && definedTags != null
                        && !definedTags.Contains(tag))
                        scan.Add("invalid_tag", "warning", false, path, objectPath,
                            $"Tag '{tag}' is not defined in the TagManager.",
                            "Define the tag in Project Settings > Tags and Layers, or reassign a valid tag.");
                }
                catch (Exception ex) { scan.RuleError("invalid_tag", path, ex.Message); }

                // unnamed_layer
                try
                {
                    int layer = go.layer;
                    if (string.IsNullOrEmpty(LayerMask.LayerToName(layer)))
                        scan.Add("unnamed_layer", "info", true, path, objectPath,
                            $"GameObject is on layer index {layer}, which has no name.",
                            "Name the layer in Project Settings > Tags and Layers, or move to a named layer.");
                }
                catch (Exception ex) { scan.RuleError("unnamed_layer", path, ex.Message); }

                // material_issues + disabled_critical + large_bounds (renderer-based)
                try
                {
                    foreach (var comp in components)
                    {
                        if (comp == null) continue;

                        if (comp is Renderer renderer)
                        {
                            ScanRendererMaterials(renderer, path, objectPath, scan);

                            if (!renderer.enabled)
                                scan.Add("disabled_critical", "info", true, path, objectPath,
                                    $"Renderer '{comp.GetType().Name}' is disabled (may be intentional).",
                                    "Enable the Renderer if it should draw, otherwise ignore.");

                            try
                            {
                                var ext = renderer.bounds.extents;
                                if (ext.x > 1000f || ext.y > 1000f || ext.z > 1000f)
                                    scan.Add("large_bounds", "info", true, path, objectPath,
                                        $"Renderer bounds extent exceeds 1000 units ({ext.x:0}, {ext.y:0}, {ext.z:0}).",
                                        "Verify the mesh scale — very large bounds can hurt culling/lighting.");
                            }
                            catch { /* bounds may be unavailable on the asset */ }
                        }
                        else if (comp is Collider collider && !collider.enabled)
                        {
                            scan.Add("disabled_critical", "info", true, path, objectPath,
                                $"Collider '{comp.GetType().Name}' is disabled (may be intentional).",
                                "Enable the Collider if it should participate in physics, otherwise ignore.");
                        }
                        if (scan.Full) return;
                    }
                }
                catch (Exception ex) { scan.RuleError("material_issues", path, ex.Message); }

                // nested_prefab_depth
                try
                {
                    int depth = NestedInstanceDepth(root, go);
                    if (depth > 3)
                        scan.Add("nested_prefab_depth", "info", true, path, objectPath,
                            $"Nested prefab instance depth is {depth} (> 3).",
                            "Flatten deeply nested prefab instances to reduce load/merge cost.");
                }
                catch (Exception ex) { scan.RuleError("nested_prefab_depth", path, ex.Message); }
            }
        }

        private static void ScanDanglingRefs(UnityEngine.Object obj, string path, string objectPath, string compName, PrefabHealthScan scan)
        {
            if (obj == null) return;
            using var so = new SerializedObject(obj);
            var it = so.GetIterator();
            bool enterChildren = true;
            while (it.NextVisible(enterChildren))
            {
                enterChildren = true;
                if (it.propertyType != SerializedPropertyType.ObjectReference) continue;
                // Value is null but a stored instance id remains — the reference pointed
                // at an object that no longer exists.
                if (it.objectReferenceValue == null && it.objectReferenceInstanceIDValue != 0)
                {
                    scan.Add("missing_references", "warning", false, path,
                        $"{objectPath}/{compName}",
                        $"Dangling serialized reference at '{it.propertyPath}'.",
                        "Reassign the missing reference or clear the field.");
                    if (scan.Full) return;
                }
            }
        }

        private static void ScanRendererMaterials(Renderer renderer, string path, string objectPath, PrefabHealthScan scan)
        {
            var mats = renderer.sharedMaterials;
            if (mats == null) return;
            for (int i = 0; i < mats.Length; i++)
            {
                var mat = mats[i];
                if (mat == null)
                {
                    scan.Add("material_issues", "warning", false, path, objectPath,
                        $"Renderer material slot {i} is empty (null material).",
                        "Assign a material to the empty slot or remove the extra slot.");
                    continue;
                }
                var shader = mat.shader;
                if (shader == null)
                    scan.Add("material_issues", "warning", false, path, objectPath,
                        $"Material '{mat.name}' has a missing shader.",
                        "Reassign a valid shader to the material.");
                else if (shader.name == "Hidden/InternalErrorShader")
                    scan.Add("material_issues", "warning", false, path, objectPath,
                        $"Material '{mat.name}' uses the internal error ('pink') shader.",
                        "Fix or reassign the material's shader.");
                else if (!shader.isSupported)
                    scan.Add("material_issues", "warning", false, path, objectPath,
                        $"Material '{mat.name}' uses an unsupported shader '{shader.name}'.",
                        "Use a shader supported on the current build target.");
                if (scan.Full) return;
            }
        }

        // Duplicates of these are legitimate; everything else is flagged as info.
        private static bool DuplicatesAllowed(Type t)
        {
            return t == typeof(BoxCollider)
                || t == typeof(SphereCollider)
                || t == typeof(CapsuleCollider)
                || t == typeof(MeshCollider);
        }

        private static string[] SafeTags()
        {
            try { return UnityEditorInternal.InternalEditorUtility.tags; }
            catch { return null; }
        }

        private static string HierarchyPath(Transform root, Transform target)
        {
            if (target == root) return "";
            var stack = new List<string>();
            var cur = target;
            while (cur != null && cur != root)
            {
                stack.Add(cur.name);
                cur = cur.parent;
            }
            stack.Reverse();
            return string.Join("/", stack);
        }

        // Depth of nested prefab instances at this GameObject within the asset: how
        // many prefab-instance boundaries sit between the asset root and this object.
        private static int NestedInstanceDepth(GameObject root, GameObject go)
        {
            int depth = 0;
            var handles = new HashSet<UnityEngine.Object>();
            var cur = go.transform;
            var rootTr = root.transform;
            while (cur != null)
            {
                var instanceRoot = PrefabUtility.GetNearestPrefabInstanceRoot(cur.gameObject);
                if (instanceRoot != null)
                {
                    if (handles.Add(instanceRoot))
                        depth++;
                    var instTr = instanceRoot.transform;
                    if (instTr == rootTr || instTr.parent == null) break;
                    cur = instTr.parent;
                }
                else
                {
                    break;
                }
            }
            return depth;
        }
    }
}
