using System;
using System.Collections.Generic;
using System.IO;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

namespace MCPForUnity.Editor.Tools.Cameras
{
    /// <summary>
    /// Captures the current frame and numerically diffs it against a previously saved
    /// screenshot (baseline_path). Returns per-pixel change stats — changed_pixel_pct,
    /// diff_bbox, mean_delta, dimensions — with NO image by default, so an agent can
    /// verify "my change affected only region Y" for a handful of tokens. Set
    /// save_diff=true to also write a diff-visualization PNG next to the capture.
    /// </summary>
    internal static class ScreenshotCompare
    {
        private const int DefaultThreshold = 8;

        public static object HandleCommand(JObject @params)
        {
            var p = new ToolParams(@params);

            string baselineRef = p.Get("baseline_path");
            if (string.IsNullOrWhiteSpace(baselineRef))
            {
                return new ErrorResponse(
                    "screenshot_compare requires 'baseline_path' (a prior screenshot to diff against).");
            }

            // Validate and resolve the baseline path (rejects traversal / external paths).
            if (!TryResolveBaselinePath(baselineRef, p.Get("output_folder"), out string baselineFull, out string pathError))
            {
                return new ErrorResponse(pathError);
            }
            if (!File.Exists(baselineFull))
            {
                return new ErrorResponse($"Baseline screenshot not found at '{baselineRef}'.");
            }

            int threshold = Mathf.Clamp(p.GetInt("diff_threshold", DefaultThreshold) ?? DefaultThreshold, 0, 255);
            bool saveDiff = p.GetBool("save_diff", false);

            // Capture the current frame to disk, then load it back for comparison.
            if (!TryCaptureCurrentFrame(@params, out string currentFull, out string currentRelative, out string captureError))
            {
                return new ErrorResponse(captureError);
            }

            Texture2D baseTex = null;
            Texture2D curTex = null;
            try
            {
                baseTex = LoadTexture(baselineFull);
                if (baseTex == null)
                    return new ErrorResponse($"Failed to decode baseline image '{baselineRef}'.");
                curTex = LoadTexture(currentFull);
                if (curTex == null)
                    return new ErrorResponse($"Failed to decode captured frame '{currentRelative}'.");

                if (baseTex.width != curTex.width || baseTex.height != curTex.height)
                {
                    return new ErrorResponse(
                        $"Dimension mismatch: baseline is {baseTex.width}x{baseTex.height} but the current " +
                        $"capture is {curTex.width}x{curTex.height}. Re-capture the baseline at the current resolution.");
                }

                var data = Compare(baseTex, curTex, threshold, saveDiff, currentFull);
                data["baseline_path"] = ToProjectRelative(baselineFull);
                data["current_path"] = currentRelative;
                data["threshold"] = threshold;

                float changedPct = Convert.ToSingle(data["changed_pixel_pct"]);
                return new SuccessResponse(
                    $"Compared capture against baseline: {changedPct:F2}% of pixels changed.",
                    data);
            }
            finally
            {
                DestroyTexture(baseTex);
                DestroyTexture(curTex);
            }
        }

        private static Dictionary<string, object> Compare(
            Texture2D baseline, Texture2D current, int threshold, bool saveDiff, string capturePath)
        {
            int width = baseline.width;
            int height = baseline.height;
            Color32[] a = baseline.GetPixels32();
            Color32[] b = current.GetPixels32();

            long changed = 0;
            long deltaSum = 0;
            int minX = width, minY = height, maxX = -1, maxY = -1;

            Color32[] diffPixels = saveDiff ? new Color32[width * height] : null;

            for (int y = 0; y < height; y++)
            {
                for (int x = 0; x < width; x++)
                {
                    int idx = y * width + x;
                    Color32 pa = a[idx];
                    Color32 pb = b[idx];
                    int dr = Mathf.Abs(pa.r - pb.r);
                    int dg = Mathf.Abs(pa.g - pb.g);
                    int db = Mathf.Abs(pa.b - pb.b);
                    int maxDelta = Mathf.Max(dr, Mathf.Max(dg, db));
                    deltaSum += maxDelta;

                    if (maxDelta > threshold)
                    {
                        changed++;
                        if (x < minX) minX = x;
                        if (y < minY) minY = y;
                        if (x > maxX) maxX = x;
                        if (y > maxY) maxY = y;
                        if (diffPixels != null)
                            diffPixels[idx] = new Color32(255, 0, 0, 255);
                    }
                    else if (diffPixels != null)
                    {
                        // Dim the unchanged background so the changed region stands out.
                        byte g = (byte)(pa.r / 3 + pa.g / 3 + pa.b / 3);
                        diffPixels[idx] = new Color32(g, g, g, 255);
                    }
                }
            }

            long totalPixels = (long)width * height;
            float changedPct = totalPixels > 0 ? (float)changed / totalPixels * 100f : 0f;
            float meanDelta = totalPixels > 0 ? (float)deltaSum / totalPixels : 0f;

            var data = new Dictionary<string, object>
            {
                { "changed_pixel_pct", Mathf.Round(changedPct * 10000f) / 10000f },
                { "mean_delta", Mathf.Round(meanDelta * 100f) / 100f },
                { "changed_pixels", changed },
                { "dimensions", new Dictionary<string, object> { { "width", width }, { "height", height } } },
            };

            if (maxX >= 0)
            {
                data["diff_bbox"] = new Dictionary<string, object>
                {
                    { "x", minX }, { "y", minY },
                    { "w", maxX - minX + 1 }, { "h", maxY - minY + 1 },
                };
            }
            else
            {
                data["diff_bbox"] = null;
            }

            if (saveDiff && diffPixels != null)
            {
                string diffPath = WriteDiffImage(diffPixels, width, height, capturePath);
                if (diffPath != null)
                    data["diff_path"] = ToProjectRelative(diffPath);
            }

            return data;
        }

        private static string WriteDiffImage(Color32[] pixels, int width, int height, string capturePath)
        {
            Texture2D diff = null;
            try
            {
                diff = new Texture2D(width, height, TextureFormat.RGBA32, false);
                diff.SetPixels32(pixels);
                diff.Apply();
                byte[] png = diff.EncodeToPNG();

                string dir = Path.GetDirectoryName(capturePath) ?? "";
                string baseName = Path.GetFileNameWithoutExtension(capturePath);
                string diffPath = Path.Combine(dir, baseName + "-diff.png").Replace('\\', '/');
                File.WriteAllBytes(diffPath, png);

                string rel = ScreenshotUtility.ToProjectRelativePath(diffPath);
                if (ScreenshotUtility.IsUnderAssets(rel))
                    AssetDatabase.ImportAsset(rel, ImportAssetOptions.ForceSynchronousImport);
                return diffPath;
            }
            catch (Exception ex)
            {
                McpLog.Warn($"[ScreenshotCompare] Failed to write diff image: {ex.Message}");
                return null;
            }
            finally
            {
                DestroyTexture(diff);
            }
        }

        /// <summary>
        /// Captures the current frame to the screenshot folder and returns its paths.
        /// Uses camera-based capture (a specific camera or the main camera) so the
        /// comparison is deterministic across edit/play mode.
        /// </summary>
        private static bool TryCaptureCurrentFrame(JObject @params, out string fullPath, out string relativePath, out string error)
        {
            fullPath = null;
            relativePath = null;
            error = null;
            try
            {
                var p = new ToolParams(@params);
                Camera camera = CameraHelpers.ResolveCameraOrMain(p.Get("camera"));
                if (camera == null)
                {
                    error = "No camera found to capture the current frame. Provide 'camera' or add a Camera to the scene.";
                    return false;
                }

                string folderOverride = ScreenshotPreferences.Resolve(p.Get("output_folder"));
                int superSize = Mathf.Max(1, p.GetInt("super_size", 1) ?? 1);

                var result = ScreenshotUtility.CaptureFromCameraToProjectFolder(
                    camera, p.Get("file_name"), superSize, ensureUniqueFileName: true,
                    includeImage: false, maxResolution: 0, folderOverride: folderOverride);

                if (ScreenshotUtility.IsUnderAssets(result.ProjectRelativePath))
                    AssetDatabase.ImportAsset(result.ProjectRelativePath, ImportAssetOptions.ForceSynchronousImport);

                fullPath = result.FullPath;
                relativePath = result.ProjectRelativePath;
                return true;
            }
            catch (Exception ex)
            {
                error = $"Failed to capture current frame: {ex.Message}";
                return false;
            }
        }

        /// <summary>
        /// Resolves and hardens a baseline path. The baseline must live inside the resolved
        /// screenshot output folder OR under the project's Assets/ folder. Rejects parent
        /// ('..') segments and absolute/external paths, mirroring the build/checkpoint
        /// path-hardening style.
        /// </summary>
        private static bool TryResolveBaselinePath(string baselineRef, string outputFolderOverride, out string fullPath, out string error)
        {
            fullPath = null;
            error = null;

            string normalized = baselineRef.Replace('\\', '/').Trim();
            if (normalized.Length == 0)
            {
                error = "baseline_path is empty.";
                return false;
            }
            foreach (var seg in normalized.Split('/'))
            {
                if (seg == "..")
                {
                    error = $"baseline_path '{baselineRef}' contains a parent-directory ('..') segment; " +
                            "path traversal is not allowed.";
                    return false;
                }
            }

            string projectRoot = ProjectRootWithSlash();
            string assetsRoot = CanonicalDir(Path.Combine(projectRoot, "Assets"));

            string screenshotsRoot;
            try
            {
                string spec = ScreenshotPreferences.Resolve(outputFolderOverride);
                screenshotsRoot = CanonicalDir(ScreenshotUtility.ResolveFolderAbsolute(spec));
            }
            catch (Exception)
            {
                screenshotsRoot = CanonicalDir(Path.Combine(projectRoot, ScreenshotUtility.DefaultFolder));
            }

            string combined = Path.IsPathRooted(normalized)
                ? normalized
                : Path.Combine(projectRoot, normalized);
            string full = Path.GetFullPath(combined).Replace('\\', '/');

            var cmp = Application.platform == RuntimePlatform.WindowsEditor
                ? StringComparison.OrdinalIgnoreCase
                : StringComparison.Ordinal;

            bool underScreens = full.StartsWith(screenshotsRoot, cmp);
            bool underAssets = full.StartsWith(assetsRoot, cmp);
            if (!underScreens && !underAssets)
            {
                error = $"baseline_path '{baselineRef}' resolves outside the screenshot output folder and Assets/. " +
                        "Baselines must be prior screenshots inside the project.";
                return false;
            }

            fullPath = full;
            return true;
        }

        private static string ProjectRootWithSlash()
        {
            string root = Path.GetFullPath(Path.Combine(Application.dataPath, "..")).Replace('\\', '/');
            if (!root.EndsWith("/")) root += "/";
            return root;
        }

        private static string CanonicalDir(string path)
        {
            string full = Path.GetFullPath(path).Replace('\\', '/');
            if (!full.EndsWith("/")) full += "/";
            return full;
        }

        private static string ToProjectRelative(string fullPath)
        {
            return ScreenshotUtility.ToProjectRelativePath(fullPath.Replace('\\', '/'));
        }

        private static Texture2D LoadTexture(string fullPath)
        {
            try
            {
                byte[] bytes = File.ReadAllBytes(fullPath);
                var tex = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                if (tex.LoadImage(bytes))
                    return tex;
                DestroyTexture(tex);
                return null;
            }
            catch
            {
                return null;
            }
        }

        private static void DestroyTexture(Texture2D tex)
        {
            if (tex == null) return;
            if (Application.isPlaying) UnityEngine.Object.Destroy(tex);
            else UnityEngine.Object.DestroyImmediate(tex);
        }
    }
}
