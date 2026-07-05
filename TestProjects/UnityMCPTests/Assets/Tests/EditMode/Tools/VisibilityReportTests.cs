using System;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using MCPForUnity.Editor.Tools.Cameras;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// EditMode tests for the zero-pixel visibility_report action. These require the
    /// Unity Editor (they build a real scene with a camera + renderers) and are the
    /// authoritative verification of VisibilityReport's frustum math.
    /// </summary>
    public class VisibilityReportTests
    {
        private GameObject _camGo;
        private Camera _cam;
        private GameObject _inFront;
        private GameObject _behind;

        [SetUp]
        public void SetUp()
        {
            _camGo = new GameObject("VisTestCamera");
            _cam = _camGo.AddComponent<Camera>();
            _camGo.transform.position = Vector3.zero;
            _camGo.transform.rotation = Quaternion.identity; // looking down +Z
            _cam.nearClipPlane = 0.1f;
            _cam.farClipPlane = 100f;
            _cam.fieldOfView = 60f;

            // A cube clearly in front of the camera (down +Z).
            _inFront = GameObject.CreatePrimitive(PrimitiveType.Cube);
            _inFront.name = "InFrontCube";
            _inFront.transform.position = new Vector3(0f, 0f, 10f);

            // A cube clearly behind the camera (down -Z) — outside the frustum.
            _behind = GameObject.CreatePrimitive(PrimitiveType.Cube);
            _behind.name = "BehindCube";
            _behind.transform.position = new Vector3(0f, 0f, -10f);
        }

        [TearDown]
        public void TearDown()
        {
            if (_inFront != null) UnityEngine.Object.DestroyImmediate(_inFront);
            if (_behind != null) UnityEngine.Object.DestroyImmediate(_behind);
            if (_camGo != null) UnityEngine.Object.DestroyImmediate(_camGo);
        }

        private JObject Run(JObject extra = null)
        {
            var p = new JObject
            {
                ["action"] = "visibility_report",
                ["camera"] = "VisTestCamera",
            };
            if (extra != null)
                foreach (var prop in extra.Properties())
                    p[prop.Name] = prop.Value;
            return ToJObject(ManageCamera.HandleCommand(p));
        }

        [Test]
        public void VisibilityReport_Succeeds_ForNamedCamera()
        {
            var result = Run();
            Assert.IsTrue(result["success"].Value<bool>(), result.ToString());
            Assert.AreEqual("VisTestCamera", result["data"]?["camera"]?.ToString());
        }

        [Test]
        public void VisibilityReport_IncludesInFrontCube_ExcludesBehindCube()
        {
            var result = Run();
            var items = result["data"]?["items"] as JArray;
            Assert.IsNotNull(items, "items array missing");

            var names = items.Select(i => i["name"]?.ToString()).ToList();
            Assert.Contains("InFrontCube", names, "The cube in front of the camera must be in-frustum.");
            Assert.IsFalse(names.Contains("BehindCube"), "The cube behind the camera must NOT be in-frustum.");
        }

        [Test]
        public void VisibilityReport_InFrustumCount_IsAtLeastOne_AndLessThanTotal()
        {
            var result = Run();
            int total = result["data"]?["total_renderers"]?.Value<int>() ?? 0;
            int inFrustum = result["data"]?["in_frustum_count"]?.Value<int>() ?? -1;

            Assert.GreaterOrEqual(total, 2, "Both test cubes should be counted as renderers.");
            Assert.GreaterOrEqual(inFrustum, 1, "At least the front cube is in-frustum.");
            Assert.Less(inFrustum, total, "The behind cube must not be counted as in-frustum.");
        }

        [Test]
        public void VisibilityReport_ItemHasScreenRectAndCoverage()
        {
            var result = Run();
            var items = result["data"]?["items"] as JArray;
            var front = items?.FirstOrDefault(i => i["name"]?.ToString() == "InFrontCube");
            Assert.IsNotNull(front, "InFrontCube item missing");

            Assert.IsNotNull(front["screen_rect_pct"], "screen_rect_pct missing");
            Assert.IsNotNull(front["coverage_pct"], "coverage_pct missing");
            Assert.Greater(front["coverage_pct"].Value<float>(), 0f, "In-frustum object should have positive coverage.");
            Assert.Greater(front["distance"].Value<float>(), 0f);
        }

        [Test]
        public void VisibilityReport_RespectsPageSize()
        {
            var result = Run(new JObject { ["page_size"] = 1 });
            var items = result["data"]?["items"] as JArray;
            Assert.IsNotNull(items);
            Assert.LessOrEqual(items.Count, 1, "page_size=1 must return at most one item.");
            Assert.AreEqual(1, result["data"]?["page_size"]?.Value<int>());
        }
    }
}
