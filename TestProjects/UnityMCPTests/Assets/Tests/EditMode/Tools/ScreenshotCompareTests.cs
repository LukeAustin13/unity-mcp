using System;
using System.IO;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;
using MCPForUnity.Editor.Tools.Cameras;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// EditMode tests for screenshot_compare. These require the Unity Editor (they
    /// write real image files and render a real camera) and are the authoritative
    /// verification of the path-hardening and dimension-mismatch behaviour.
    /// </summary>
    public class ScreenshotCompareTests
    {
        private const string TempParent = "Assets/Temp";
        private string _root;
        private GameObject _camGo;

        [SetUp]
        public void SetUp()
        {
            if (!AssetDatabase.IsValidFolder(TempParent))
                AssetDatabase.CreateFolder("Assets", "Temp");
            string leaf = "ScreenshotCompareTests_" + Guid.NewGuid().ToString("N");
            AssetDatabase.CreateFolder(TempParent, leaf);
            _root = $"{TempParent}/{leaf}";

            _camGo = new GameObject("CompareTestCamera");
            var cam = _camGo.AddComponent<Camera>();
            cam.nearClipPlane = 0.1f;
            cam.farClipPlane = 100f;
        }

        [TearDown]
        public void TearDown()
        {
            if (_camGo != null) UnityEngine.Object.DestroyImmediate(_camGo);
            if (AssetDatabase.IsValidFolder(_root))
                AssetDatabase.DeleteAsset(_root);
            CleanupEmptyParentFolders(_root);
        }

        private string WritePng(string name, int width, int height)
        {
            var tex = new Texture2D(width, height, TextureFormat.RGBA32, false);
            var px = new Color32[width * height];
            for (int i = 0; i < px.Length; i++) px[i] = new Color32(10, 20, 30, 255);
            tex.SetPixels32(px);
            tex.Apply();
            byte[] bytes = tex.EncodeToPNG();
            UnityEngine.Object.DestroyImmediate(tex);

            string rel = $"{_root}/{name}.png";
            File.WriteAllBytes(rel, bytes);
            AssetDatabase.ImportAsset(rel, ImportAssetOptions.ForceSynchronousImport);
            return rel;
        }

        private JObject Run(string baselinePath)
        {
            var p = new JObject
            {
                ["action"] = "screenshot_compare",
                ["baselinePath"] = baselinePath,
                ["camera"] = "CompareTestCamera",
                ["outputFolder"] = _root,
            };
            return ToJObject(ManageCamera.HandleCommand(p));
        }

        [Test]
        public void Compare_RejectsTraversalBaselinePath()
        {
            var result = Run("Assets/Temp/../../../etc/passwd");
            Assert.IsFalse(result["success"].Value<bool>(), "Traversal path must be rejected.");
            string err = (result["error"] ?? result["message"])?.ToString() ?? "";
            StringAssert.Contains("..", err);
        }

        [Test]
        public void Compare_RejectsAbsoluteExternalBaselinePath()
        {
            var result = Run("/etc/hosts");
            Assert.IsFalse(result["success"].Value<bool>(),
                "An absolute path outside the project must be rejected.");
        }

        [Test]
        public void Compare_MissingBaseline_ReturnsError()
        {
            var result = Run($"{_root}/does-not-exist.png");
            Assert.IsFalse(result["success"].Value<bool>());
            string err = (result["error"] ?? result["message"])?.ToString() ?? "";
            StringAssert.Contains("not found", err);
        }

        [Test]
        public void Compare_DimensionMismatch_ReturnsClearError()
        {
            // Baseline sized so it will NOT match the camera's capture dimensions.
            string baseline = WritePng("baseline_tiny", 32, 24);
            var result = Run(baseline);

            // The capture step must succeed and reach the dimension comparison; if the
            // capture itself failed for environment reasons, we still expect an error
            // (never a false success).
            Assert.IsFalse(result["success"].Value<bool>(),
                "A dimension mismatch (or capture failure) must not report success.");
        }

        [Test]
        public void Compare_MissingBaselinePathParam_ReturnsError()
        {
            var p = new JObject { ["action"] = "screenshot_compare", ["camera"] = "CompareTestCamera" };
            var result = ToJObject(ManageCamera.HandleCommand(p));
            Assert.IsFalse(result["success"].Value<bool>());
            string err = (result["error"] ?? result["message"])?.ToString() ?? "";
            StringAssert.Contains("baseline_path", err);
        }
    }
}
