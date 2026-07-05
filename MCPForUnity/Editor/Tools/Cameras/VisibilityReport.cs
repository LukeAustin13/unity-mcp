using System;
using System.Collections.Generic;
using System.Linq;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Cameras
{
    /// <summary>
    /// Zero-pixel visual facts for a camera. Frustum-tests every active-in-hierarchy
    /// Renderer in the loaded scenes and, for the ones inside the frustum, computes a
    /// screen-space rect (as percentages of the frame) plus an approximate coverage.
    /// This answers "is X on screen / how big / behind what" without capturing any
    /// pixels — the cheapest tier of the visual-audit pipeline.
    /// </summary>
    internal static class VisibilityReport
    {
        private const int DefaultPageSize = 25;
        private const int MaxPageSize = 100;

        public static object HandleCommand(JObject @params)
        {
            var p = new ToolParams(@params);

            Camera camera = CameraHelpers.ResolveCameraOrMain(p.Get("camera"));
            if (camera == null)
            {
                return new ErrorResponse(
                    "No camera found for visibility_report. Provide 'camera' (name/path/ID) or add a Camera to the scene.");
            }

            bool checkOcclusion = p.GetBool("check_occlusion", false);
            int pageSize = Mathf.Clamp(p.GetInt("page_size", DefaultPageSize) ?? DefaultPageSize, 1, MaxPageSize);
            int cursor = Mathf.Max(0, p.GetInt("cursor", 0) ?? 0);

            var planes = GeometryUtility.CalculateFrustumPlanes(camera);
            var renderers = UnityFindObjectsCompat.FindAll<Renderer>();

            int totalRenderers = 0;
            var items = new List<Dictionary<string, object>>();

            foreach (var r in renderers)
            {
                if (r == null) continue;
                var go = r.gameObject;
                if (go == null || !go.activeInHierarchy) continue;
                totalRenderers++;

                Bounds bounds = r.bounds;
                if (!GeometryUtility.TestPlanesAABB(planes, bounds))
                    continue;

                if (!TryComputeScreenRect(camera, bounds, out Rect vpRect))
                    continue;

                float coverage = Mathf.Clamp01(vpRect.width) * Mathf.Clamp01(vpRect.height) * 100f;
                float distance = Vector3.Distance(camera.transform.position, bounds.center);

                var item = new Dictionary<string, object>
                {
                    { "name", go.name },
                    { "path", GetHierarchyPath(go) },
                    { "screen_rect_pct", new Dictionary<string, object>
                        {
                            { "x", Round(Mathf.Clamp01(vpRect.xMin) * 100f) },
                            { "y", Round(Mathf.Clamp01(vpRect.yMin) * 100f) },
                            { "w", Round(Mathf.Clamp01(vpRect.width) * 100f) },
                            { "h", Round(Mathf.Clamp01(vpRect.height) * 100f) },
                        }
                    },
                    { "coverage_pct", Round(coverage) },
                    { "distance", Round(distance) },
                };

                if (checkOcclusion)
                {
                    string occludedBy = ComputeOccluder(camera, go, bounds);
                    if (!string.IsNullOrEmpty(occludedBy))
                        item["occluded_by"] = occludedBy;
                }

                items.Add(item);
            }

            // Sort by coverage descending (largest on-screen object first).
            items.Sort((a, b) =>
                Convert.ToSingle(b["coverage_pct"]).CompareTo(Convert.ToSingle(a["coverage_pct"])));

            int inFrustumCount = items.Count;
            var page = items.Skip(cursor).Take(pageSize).ToList();
            int nextCursor = cursor + page.Count;
            bool hasMore = nextCursor < inFrustumCount;

            var data = new Dictionary<string, object>
            {
                { "camera", camera.name },
                { "total_renderers", totalRenderers },
                { "in_frustum_count", inFrustumCount },
                { "occlusion_checked", checkOcclusion },
                { "occlusion_note", checkOcclusion
                    ? "occluded_by is approximate: a single raycast from the camera to each object's bounds center."
                    : null },
                { "items", page },
                { "page_size", pageSize },
                { "cursor", cursor },
                { "next_cursor", hasMore ? (object)nextCursor : null },
            };

            return new SuccessResponse(
                $"Visibility report for camera '{camera.name}': {inFrustumCount} of {totalRenderers} renderers in frustum.",
                data);
        }

        /// <summary>
        /// Projects the 8 bounds corners to viewport space and returns their AABB as a
        /// Rect in [0,1] viewport coordinates. Returns false when the whole box is behind
        /// the camera.
        /// </summary>
        private static bool TryComputeScreenRect(Camera camera, Bounds bounds, out Rect vpRect)
        {
            vpRect = default;
            float minX = float.MaxValue, minY = float.MaxValue;
            float maxX = float.MinValue, maxY = float.MinValue;
            bool anyInFront = false;
            Vector3 c = bounds.center, e = bounds.extents;
            for (int i = 0; i < 8; i++)
            {
                var corner = new Vector3(
                    c.x + ((i & 1) == 0 ? -e.x : e.x),
                    c.y + ((i & 2) == 0 ? -e.y : e.y),
                    c.z + ((i & 4) == 0 ? -e.z : e.z));
                Vector3 vp = camera.WorldToViewportPoint(corner);
                if (vp.z > 0f) anyInFront = true;
                minX = Mathf.Min(minX, vp.x);
                minY = Mathf.Min(minY, vp.y);
                maxX = Mathf.Max(maxX, vp.x);
                maxY = Mathf.Max(maxY, vp.y);
            }
            if (!anyInFront) return false;
            vpRect = Rect.MinMaxRect(minX, minY, maxX, maxY);
            return true;
        }

        /// <summary>
        /// Single raycast from the camera to the object's bounds center. Reports the name of
        /// the first collider hit that is not the object itself. Approximate — objects without
        /// colliders never register as occluders and never appear occluded.
        /// </summary>
        private static string ComputeOccluder(Camera camera, GameObject target, Bounds bounds)
        {
            Vector3 origin = camera.transform.position;
            Vector3 dir = bounds.center - origin;
            float dist = dir.magnitude;
            if (dist <= Mathf.Epsilon) return null;
            dir /= dist;

            var hits = Physics.RaycastAll(origin, dir, dist);
            if (hits == null || hits.Length == 0) return null;
            Array.Sort(hits, (a, b) => a.distance.CompareTo(b.distance));
            foreach (var hit in hits)
            {
                if (hit.collider == null) continue;
                var hitGo = hit.collider.gameObject;
                if (hitGo == target) continue;
                if (hitGo.transform.IsChildOf(target.transform)) continue;
                if (target.transform.IsChildOf(hitGo.transform)) continue;
                return hitGo.name;
            }
            return null;
        }

        private static string GetHierarchyPath(GameObject go)
        {
            if (go == null) return null;
            var stack = new Stack<string>();
            var t = go.transform;
            while (t != null)
            {
                stack.Push(t.name);
                t = t.parent;
            }
            return string.Join("/", stack);
        }

        private static float Round(float value) => Mathf.Round(value * 100f) / 100f;
    }
}
