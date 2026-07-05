using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// "SQL for the scene": one call applies AND-combined filters and per-row
    /// projections across every loaded scene, returning compact rows. Replaces the
    /// find-object -> get-components -> get-properties loop for read-only inspection.
    ///
    /// READ-only: traverses loaded scenes' root GameObjects depth-first, never
    /// mutating anything. Deterministic depth-first ordering makes the flat-index
    /// cursor stable for paging. Everything is null-guarded (missing scripts are
    /// reported as the component type "&lt;Missing&gt;").
    /// </summary>
    [McpForUnityTool("query_scene", AutoRegister = false, Group = "core")]
    public static class QueryScene
    {
        private const int DefaultPageSize = 50;
        private const int MaxPageSize = 500;

        private static readonly HashSet<string> ValidIncludes = new(StringComparer.Ordinal)
        {
            "transform", "world_bounds", "components", "materials", "mesh_stats"
        };

        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");

            try
            {
                var p = new ToolParams(@params);

                // --- filters (all optional, AND-combined) ---
                string nameContains = p.Get("name_contains");
                string tag = p.Get("tag");
                string layerRaw = p.Get("layer");
                string componentTypeName = p.Get("component_type");
                string rootPath = p.Get("root_path");
                string sceneName = p.Get("scene");
                bool includeInactive = p.GetBool("include_inactive", false);

                // Resolve layer filter (name or index) to an int, or -1 for "no filter".
                int layerFilter = -1;
                if (!string.IsNullOrEmpty(layerRaw))
                {
                    layerFilter = LayerMask.NameToLayer(layerRaw);
                    if (layerFilter == -1)
                    {
                        if (!int.TryParse(layerRaw, out layerFilter) || layerFilter < 0 || layerFilter > 31)
                            return new ErrorResponse(
                                $"Invalid layer '{layerRaw}'. Provide a layer name or an index 0-31.");
                    }
                }

                // Resolve component_type filter to a Type (null when no filter).
                Type componentFilter = null;
                if (!string.IsNullOrEmpty(componentTypeName))
                {
                    componentFilter = GameObjectLookup.FindComponentType(componentTypeName);
                    if (componentFilter == null)
                        return new ErrorResponse(
                            $"Component type '{componentTypeName}' not found. Use a short (e.g. 'Rigidbody') " +
                            "or namespaced (e.g. 'UnityEngine.Rigidbody') type name.");
                }

                // --- projections ---
                var includes = ParseIncludes(@params, out string includeError);
                if (includeError != null)
                    return new ErrorResponse(includeError);

                // --- paging ---
                int pageSize = Mathf.Clamp(p.GetInt("page_size") ?? DefaultPageSize, 1, MaxPageSize);
                int cursor = Mathf.Max(0, p.GetInt("cursor") ?? 0);

                // Case-insensitive substring is precomputed once.
                string nameNeedle = string.IsNullOrEmpty(nameContains) ? null : nameContains.ToLowerInvariant();

                // Deterministic depth-first walk of all loaded scenes' root objects.
                int totalMatched = 0;
                var rows = new List<object>();

                foreach (var go in EnumerateLoadedScenes(sceneName, includeInactive))
                {
                    if (!Matches(go, nameNeedle, tag, layerFilter, componentFilter, rootPath))
                        continue;

                    // totalMatched is the flat match index; use it for cursor paging.
                    int matchIndex = totalMatched;
                    totalMatched++;

                    if (matchIndex < cursor) continue;
                    if (rows.Count >= pageSize) continue; // keep counting total, stop collecting rows

                    rows.Add(BuildRow(go, includes));
                }

                int nextCursorValue = cursor + rows.Count;
                string nextCursor = nextCursorValue < totalMatched ? nextCursorValue.ToString() : null;

                var payload = new Dictionary<string, object>
                {
                    ["total_matched"] = totalMatched,
                    ["page_size"] = pageSize,
                    ["cursor"] = cursor,
                    ["rows"] = rows,
                };
                if (nextCursor != null)
                    payload["next_cursor"] = nextCursor;

                return new SuccessResponse(
                    $"Matched {totalMatched} object(s); returning {rows.Count} in this page.",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse(
                    $"Error in query_scene: {ex.Message}",
                    new { stackTrace = ex.StackTrace });
            }
        }

        // --- projection parsing --------------------------------------------

        private static HashSet<string> ParseIncludes(JObject @params, out string error)
        {
            error = null;
            var result = new HashSet<string>(StringComparer.Ordinal);
            var arr = ToolParams.CoerceStringArray(@params["include"] ?? @params["Include"]);
            if (arr == null || arr.Length == 0)
            {
                result.Add("transform"); // default projection
                return result;
            }
            foreach (var raw in arr)
            {
                var name = (raw ?? string.Empty).Trim().ToLowerInvariant();
                if (name.Length == 0) continue;
                if (!ValidIncludes.Contains(name))
                {
                    error = $"Unknown projection '{name}'. Valid projections: {string.Join(", ", ValidIncludes)}.";
                    return null;
                }
                result.Add(name);
            }
            if (result.Count == 0)
                result.Add("transform");
            return result;
        }

        // --- scene traversal -----------------------------------------------

        private static IEnumerable<GameObject> EnumerateLoadedScenes(string sceneName, bool includeInactive)
        {
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (!scene.IsValid() || !scene.isLoaded) continue;
                if (!string.IsNullOrEmpty(sceneName) && scene.name != sceneName) continue;

                foreach (var root in scene.GetRootGameObjects())
                {
                    foreach (var go in DepthFirst(root, includeInactive))
                        yield return go;
                }
            }
        }

        private static IEnumerable<GameObject> DepthFirst(GameObject go, bool includeInactive)
        {
            if (go == null) yield break;
            if (!includeInactive && !go.activeInHierarchy) yield break;

            yield return go;

            var t = go.transform;
            int childCount = t.childCount;
            for (int i = 0; i < childCount; i++)
            {
                var child = t.GetChild(i);
                if (child == null) continue;
                foreach (var descendant in DepthFirst(child.gameObject, includeInactive))
                    yield return descendant;
            }
        }

        // --- filtering ------------------------------------------------------

        private static bool Matches(
            GameObject go, string nameNeedle, string tag, int layerFilter,
            Type componentFilter, string rootPath)
        {
            if (nameNeedle != null && !go.name.ToLowerInvariant().Contains(nameNeedle))
                return false;

            if (!string.IsNullOrEmpty(tag))
            {
                try
                {
                    if (!go.CompareTag(tag)) return false;
                }
                catch (UnityException)
                {
                    return false; // undefined tag matches nothing
                }
            }

            if (layerFilter >= 0 && go.layer != layerFilter)
                return false;

            if (componentFilter != null && go.GetComponent(componentFilter) == null)
                return false;

            if (!string.IsNullOrEmpty(rootPath))
            {
                string path = GameObjectLookup.GetGameObjectPath(go);
                if (path != rootPath && !path.StartsWith(rootPath + "/", StringComparison.Ordinal))
                    return false;
            }

            return true;
        }

        // --- row projection -------------------------------------------------

        private static Dictionary<string, object> BuildRow(GameObject go, HashSet<string> includes)
        {
            var row = new Dictionary<string, object>
            {
                ["name"] = go.name,
                ["path"] = GameObjectLookup.GetGameObjectPath(go),
                ["instance_id"] = go.GetInstanceIDCompat(),
                ["active"] = go.activeInHierarchy,
            };

            if (includes.Contains("transform"))
            {
                var t = go.transform;
                row["transform"] = new Dictionary<string, object>
                {
                    ["position"] = Vec3(t.position),
                    ["rotation"] = Vec3(t.eulerAngles),
                    ["scale"] = Vec3(t.lossyScale),
                    ["local_position"] = Vec3(t.localPosition),
                };
            }

            if (includes.Contains("world_bounds"))
            {
                var bounds = TryGetWorldBounds(go);
                if (bounds.HasValue)
                {
                    row["world_bounds"] = new Dictionary<string, object>
                    {
                        ["center"] = Vec3(bounds.Value.center),
                        ["size"] = Vec3(bounds.Value.size),
                    };
                }
            }

            if (includes.Contains("components"))
            {
                var names = new List<string>();
                foreach (var comp in go.GetComponents<Component>())
                    names.Add(comp == null ? "<Missing>" : comp.GetType().Name);
                row["components"] = names;
            }

            if (includes.Contains("materials"))
            {
                var mats = CollectMaterials(go);
                if (mats != null)
                    row["materials"] = mats;
            }

            if (includes.Contains("mesh_stats"))
            {
                var stats = TryGetMeshStats(go);
                if (stats != null)
                    row["mesh_stats"] = stats;
            }

            return row;
        }

        private static float[] Vec3(Vector3 v) => new[] { v.x, v.y, v.z };

        /// <summary>
        /// World bounds via renderer -> collider -> RectTransform (UI) fallback.
        /// Returns null when the object has no measurable extent.
        /// </summary>
        private static Bounds? TryGetWorldBounds(GameObject go)
        {
            var renderer = go.GetComponent<Renderer>();
            if (renderer != null)
                return renderer.bounds;

            var collider = go.GetComponent<Collider>();
            if (collider != null)
                return collider.bounds;

            var collider2D = go.GetComponent<Collider2D>();
            if (collider2D != null)
                return collider2D.bounds;

            var rect = go.GetComponent<RectTransform>();
            if (rect != null)
            {
                var corners = new Vector3[4];
                rect.GetWorldCorners(corners);
                var b = new Bounds(corners[0], Vector3.zero);
                for (int i = 1; i < corners.Length; i++)
                    b.Encapsulate(corners[i]);
                return b;
            }

            return null;
        }

        private static List<object> CollectMaterials(GameObject go)
        {
            var renderer = go.GetComponent<Renderer>();
            if (renderer == null) return null;

            var result = new List<object>();
            var mats = renderer.sharedMaterials;
            if (mats == null) return result;

            foreach (var mat in mats)
            {
                if (mat == null)
                {
                    result.Add(new Dictionary<string, object>
                    {
                        ["material"] = null,
                        ["shader"] = null,
                    });
                    continue;
                }
                result.Add(new Dictionary<string, object>
                {
                    ["material"] = mat.name,
                    ["shader"] = mat.shader != null ? mat.shader.name : null,
                });
            }
            return result;
        }

        private static Dictionary<string, object> TryGetMeshStats(GameObject go)
        {
            Mesh mesh = null;

            var meshFilter = go.GetComponent<MeshFilter>();
            if (meshFilter != null)
                mesh = meshFilter.sharedMesh;

            if (mesh == null)
            {
                var skinned = go.GetComponent<SkinnedMeshRenderer>();
                if (skinned != null)
                    mesh = skinned.sharedMesh;
            }

            if (mesh == null) return null;

            int triangleCount = 0;
            try
            {
                var tris = mesh.triangles;
                if (tris != null) triangleCount = tris.Length / 3;
            }
            catch (Exception)
            {
                // Non-triangle topology (points/lines) has no triangle array; report 0.
                triangleCount = 0;
            }

            return new Dictionary<string, object>
            {
                ["vertex_count"] = mesh.vertexCount,
                ["triangle_count"] = triangleCount,
            };
        }
    }
}
