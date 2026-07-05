using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using NUnit.Framework;
using UnityEngine;
using MCPForUnity.Editor.Tools;
using static MCPForUnityTests.Editor.TestUtilities;

namespace MCPForUnityTests.Editor.Tools
{
    /// <summary>
    /// EditMode tests for audit_ui_layout. The analytic-math helpers are exercised
    /// directly (they are internal, pure, and take plain values), which is the
    /// authoritative verification of the CanvasScaler / screen-rect / overlap math.
    /// A handful of HandleCommand tests cover parameter validation and the fail-closed
    /// action guard without needing the uGUI package linked at compile time.
    /// </summary>
    public class AuditUiLayoutTests
    {
        private readonly List<GameObject> _spawned = new();

        [TearDown]
        public void TearDown()
        {
            foreach (var go in _spawned)
                if (go != null) UnityEngine.Object.DestroyImmediate(go);
            _spawned.Clear();
        }

        private static JObject Audit(JObject p) => ToJObject(AuditUiLayout.HandleCommand(p));

        // ==============================================================
        //  Resolution parsing
        // ==============================================================

        [Test]
        public void ParseResolutions_NullOrEmpty_UsesDefaultMatrix()
        {
            var result = AuditUiLayout.ParseResolutions(null, out string error);
            Assert.IsNull(error, error);
            Assert.IsNotNull(result);
            Assert.AreEqual(4, result.Count);
            Assert.AreEqual("1920x1080", result[0].Label);
        }

        [Test]
        public void ParseResolutions_ValidList_Parsed()
        {
            var result = AuditUiLayout.ParseResolutions(new[] { "1920x1080", "1280x720" }, out string error);
            Assert.IsNull(error, error);
            Assert.AreEqual(2, result.Count);
            Assert.AreEqual(1920, result[0].W);
            Assert.AreEqual(720, result[1].H);
        }

        [Test]
        public void ParseResolutions_Dedupes()
        {
            var result = AuditUiLayout.ParseResolutions(new[] { "1920x1080", "1920x1080" }, out string error);
            Assert.IsNull(error, error);
            Assert.AreEqual(1, result.Count);
        }

        [Test]
        public void ParseResolutions_MissingX_ReturnsError()
        {
            var result = AuditUiLayout.ParseResolutions(new[] { "1920" }, out string error);
            Assert.IsNull(result);
            Assert.IsNotNull(error);
        }

        [Test]
        public void ParseResolutions_NonInteger_ReturnsError()
        {
            var result = AuditUiLayout.ParseResolutions(new[] { "axb" }, out string error);
            Assert.IsNull(result);
            Assert.IsNotNull(error);
        }

        [Test]
        public void ParseResolutions_OutOfRange_ReturnsError()
        {
            Assert.IsNull(AuditUiLayout.ParseResolutions(new[] { "8x8" }, out string tooSmall));
            Assert.IsNotNull(tooSmall);
            Assert.IsNull(AuditUiLayout.ParseResolutions(new[] { "20000x1080" }, out string tooBig));
            Assert.IsNotNull(tooBig);
        }

        [Test]
        public void ParseResolutions_TooMany_ReturnsError()
        {
            var many = Enumerable.Range(0, 9).Select(i => $"{100 + i}x100").ToArray();
            var result = AuditUiLayout.ParseResolutions(many, out string error);
            Assert.IsNull(result);
            Assert.IsNotNull(error);
        }

        [Test]
        public void TryParseResolution_UppercaseX_Accepted()
        {
            Assert.IsTrue(AuditUiLayout.TryParseResolution("1920X1080", out var res, out _));
            Assert.AreEqual(1920, res.W);
            Assert.AreEqual(1080, res.H);
        }

        // ==============================================================
        //  CanvasScaler scale-factor math
        // ==============================================================

        [Test]
        public void ScaleFactor_ConstantPixelSize_ReturnsScaleFactorAsIs()
        {
            var canvas = new CanvasInfo
            {
                ScaleMode = CanvasScaleMode.ConstantPixelSize,
                ScaleFactor = 2f,
            };
            Assert.AreEqual(2f, AuditUiLayout.ComputeCanvasScaleFactor(canvas, 1920, 1080), 0.0001f);
        }

        [Test]
        public void ScaleFactor_ScaleWithScreenSize_MatchesUnityLog2Formula()
        {
            // Reference 1920x1080, target 3840x2160, match 0.5 → both axes doubled → factor 2.
            var canvas = new CanvasInfo
            {
                ScaleMode = CanvasScaleMode.ScaleWithScreenSize,
                ScreenMatchMode = ScreenMatchMode.MatchWidthOrHeight,
                MatchWidthOrHeight = 0.5f,
                ReferenceResolution = new Vector2(1920f, 1080f),
            };
            Assert.AreEqual(2f, AuditUiLayout.ComputeCanvasScaleFactor(canvas, 3840, 2160), 0.001f);
        }

        [Test]
        public void ScaleFactor_ScaleWithScreenSize_MatchWidthOnly()
        {
            // match=0 → follow width ratio only. Ref 1000 wide, target 2000 wide → 2.
            float factor = AuditUiLayout.ScaleWithScreenSizeFactor(
                2000, 500, new Vector2(1000f, 1000f), 0f, ScreenMatchMode.MatchWidthOrHeight);
            Assert.AreEqual(2f, factor, 0.001f);
        }

        [Test]
        public void ScaleFactor_ScaleWithScreenSize_MatchHeightOnly()
        {
            // match=1 → follow height ratio only. Ref 1000 tall, target 500 tall → 0.5.
            float factor = AuditUiLayout.ScaleWithScreenSizeFactor(
                4000, 500, new Vector2(1000f, 1000f), 1f, ScreenMatchMode.MatchWidthOrHeight);
            Assert.AreEqual(0.5f, factor, 0.001f);
        }

        [Test]
        public void ScaleFactor_ScaleWithScreenSize_ShrinkTakesMin()
        {
            // Shrink → min of width/height ratios. 2x width, 3x height → 2.
            float factor = AuditUiLayout.ScaleWithScreenSizeFactor(
                2000, 3000, new Vector2(1000f, 1000f), 0.5f, ScreenMatchMode.Shrink);
            Assert.AreEqual(2f, factor, 0.001f);
        }

        [Test]
        public void ScaleFactor_ScaleWithScreenSize_ExpandTakesMax()
        {
            float factor = AuditUiLayout.ScaleWithScreenSizeFactor(
                2000, 3000, new Vector2(1000f, 1000f), 0.5f, ScreenMatchMode.Expand);
            Assert.AreEqual(3f, factor, 0.001f);
        }

        [Test]
        public void ScaleFactor_ConstantPhysicalSize_ReturnsOne()
        {
            var canvas = new CanvasInfo { ScaleMode = CanvasScaleMode.ConstantPhysicalSize };
            Assert.AreEqual(1f, AuditUiLayout.ComputeCanvasScaleFactor(canvas, 1920, 1080), 0.0001f);
        }

        // ==============================================================
        //  RectTransform screen-rect math
        // ==============================================================

        [Test]
        public void ComputeChildRect_StretchFillsParent()
        {
            var parent = new Rect(0f, 0f, 800f, 600f);
            var rect = AuditUiLayout.ComputeChildRect(
                parent,
                anchorMin: Vector2.zero, anchorMax: Vector2.one,
                offsetMin: Vector2.zero, offsetMax: Vector2.zero,
                pivot: new Vector2(0.5f, 0.5f), localScale: Vector2.one);
            Assert.AreEqual(0f, rect.x, 0.001f);
            Assert.AreEqual(0f, rect.y, 0.001f);
            Assert.AreEqual(800f, rect.width, 0.001f);
            Assert.AreEqual(600f, rect.height, 0.001f);
        }

        [Test]
        public void ComputeChildRect_CenterPointAnchor_UsesSizeDelta()
        {
            // Point anchor at center (0.5,0.5); offsetMin/Max encode a 100x50 box centered.
            var parent = new Rect(0f, 0f, 800f, 600f);
            // For a point anchor, offsetMin = anchoredPos - pivot*size, offsetMax = anchoredPos + (1-pivot)*size.
            // Centered box 100x50 at parent center: anchoredPos = 0, pivot 0.5.
            var rect = AuditUiLayout.ComputeChildRect(
                parent,
                anchorMin: new Vector2(0.5f, 0.5f), anchorMax: new Vector2(0.5f, 0.5f),
                offsetMin: new Vector2(-50f, -25f), offsetMax: new Vector2(50f, 25f),
                pivot: new Vector2(0.5f, 0.5f), localScale: Vector2.one);
            Assert.AreEqual(100f, rect.width, 0.001f);
            Assert.AreEqual(50f, rect.height, 0.001f);
            Assert.AreEqual(350f, rect.x, 0.001f); // 400 center - 50 half-width
            Assert.AreEqual(275f, rect.y, 0.001f); // 300 center - 25 half-height
        }

        [Test]
        public void ComputeChildRect_LocalScaleShrinksAboutPivot()
        {
            var parent = new Rect(0f, 0f, 800f, 600f);
            var rect = AuditUiLayout.ComputeChildRect(
                parent,
                anchorMin: new Vector2(0.5f, 0.5f), anchorMax: new Vector2(0.5f, 0.5f),
                offsetMin: new Vector2(-50f, -25f), offsetMax: new Vector2(50f, 25f),
                pivot: new Vector2(0.5f, 0.5f), localScale: new Vector2(0.5f, 0.5f));
            // 100x50 scaled by 0.5 about its center → 50x25 centered at same point.
            Assert.AreEqual(50f, rect.width, 0.001f);
            Assert.AreEqual(25f, rect.height, 0.001f);
            Assert.AreEqual(375f, rect.x, 0.001f); // center 400 - 25
            Assert.AreEqual(287.5f, rect.y, 0.001f); // center 300 - 12.5
        }

        // ==============================================================
        //  Off-screen / overlap / cover / tiny predicates
        // ==============================================================

        [Test]
        public void IsFullyOffScreen_DetectsOutsideRect()
        {
            var screen = new Rect(0f, 0f, 1920f, 1080f);
            Assert.IsTrue(AuditUiLayout.IsFullyOffScreen(new Rect(2000f, 0f, 100f, 100f), screen));
            Assert.IsTrue(AuditUiLayout.IsFullyOffScreen(new Rect(-200f, 0f, 100f, 100f), screen));
            Assert.IsFalse(AuditUiLayout.IsFullyOffScreen(new Rect(10f, 10f, 100f, 100f), screen));
        }

        [Test]
        public void FractionOffScreen_HalfOutside_IsAboutHalf()
        {
            var screen = new Rect(0f, 0f, 1000f, 1000f);
            // 200x200 box with left half off the right edge.
            var rect = new Rect(900f, 400f, 200f, 200f);
            float off = AuditUiLayout.FractionOffScreen(rect, screen);
            Assert.AreEqual(0.5f, off, 0.01f);
        }

        [Test]
        public void FractionOffScreen_FullyInside_IsZero()
        {
            var screen = new Rect(0f, 0f, 1000f, 1000f);
            Assert.AreEqual(0f, AuditUiLayout.FractionOffScreen(new Rect(100f, 100f, 100f, 100f), screen), 0.001f);
        }

        [Test]
        public void RectsOverlap_DetectsIntersection()
        {
            Assert.IsTrue(AuditUiLayout.RectsOverlap(new Rect(0f, 0f, 100f, 100f), new Rect(50f, 50f, 100f, 100f)));
            Assert.IsFalse(AuditUiLayout.RectsOverlap(new Rect(0f, 0f, 100f, 100f), new Rect(200f, 200f, 100f, 100f)));
            // Edge-touching does not count as overlap.
            Assert.IsFalse(AuditUiLayout.RectsOverlap(new Rect(0f, 0f, 100f, 100f), new Rect(100f, 0f, 100f, 100f)));
        }

        [Test]
        public void FullyCovers_OnlyTrueWhenInnerContained()
        {
            var outer = new Rect(0f, 0f, 200f, 200f);
            Assert.IsTrue(AuditUiLayout.FullyCovers(outer, new Rect(50f, 50f, 100f, 100f)));
            Assert.IsFalse(AuditUiLayout.FullyCovers(outer, new Rect(150f, 150f, 100f, 100f))); // pokes out
            Assert.IsFalse(AuditUiLayout.FullyCovers(outer, new Rect(0f, 0f, 0f, 0f))); // zero-area inner
        }

        [Test]
        public void IsTinyTarget_FlagsSmallDimension()
        {
            Assert.IsTrue(AuditUiLayout.IsTinyTarget(new Rect(0f, 0f, 40f, 100f), 44f));
            Assert.IsTrue(AuditUiLayout.IsTinyTarget(new Rect(0f, 0f, 100f, 10f), 44f));
            Assert.IsFalse(AuditUiLayout.IsTinyTarget(new Rect(0f, 0f, 44f, 44f), 44f));
        }

        [Test]
        public void IsSuspiciousAnchor_PointAnchorWithLargeOffset_Flagged()
        {
            var reference = new Vector2(1920f, 1080f);
            var point = new Vector2(0.5f, 0.5f);
            // anchoredPosition.x = 1500 > 0.6*1920 (1152) → suspicious.
            Assert.IsTrue(AuditUiLayout.IsSuspiciousAnchor(
                point, point, new Vector2(1500f, 0f), new Vector2(100f, 100f), reference));
        }

        [Test]
        public void IsSuspiciousAnchor_PointAnchorWithLargeSize_Flagged()
        {
            var reference = new Vector2(1920f, 1080f);
            var point = new Vector2(0f, 1f);
            // sizeDelta.y = 900 > 0.6*1080 (648) → suspicious.
            Assert.IsTrue(AuditUiLayout.IsSuspiciousAnchor(
                point, point, Vector2.zero, new Vector2(100f, 900f), reference));
        }

        [Test]
        public void IsSuspiciousAnchor_StretchAnchor_NotFlagged()
        {
            var reference = new Vector2(1920f, 1080f);
            // Stretch anchor (min != max) is never a point-anchor smell.
            Assert.IsFalse(AuditUiLayout.IsSuspiciousAnchor(
                Vector2.zero, Vector2.one, new Vector2(1500f, 900f), new Vector2(1500f, 900f), reference));
        }

        [Test]
        public void IsSuspiciousAnchor_PointAnchorSmallOffset_NotFlagged()
        {
            var reference = new Vector2(1920f, 1080f);
            var point = new Vector2(0.5f, 0.5f);
            Assert.IsFalse(AuditUiLayout.IsSuspiciousAnchor(
                point, point, new Vector2(100f, 50f), new Vector2(200f, 60f), reference));
        }

        // ==============================================================
        //  HandleCommand — validation + fail-closed action guard
        // ==============================================================

        [Test]
        public void HandleCommand_NullParams_ReturnsError()
        {
            var result = ToJObject(AuditUiLayout.HandleCommand(null));
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void HandleCommand_UnknownAction_ReturnsError()
        {
            var result = Audit(new JObject { ["action"] = "delete_everything" });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void HandleCommand_AuditAction_Accepted()
        {
            var result = Audit(new JObject { ["action"] = "audit" });
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void HandleCommand_InvalidResolution_ReturnsError()
        {
            var result = Audit(new JObject { ["resolutions"] = new JArray("not_a_res") });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
        }

        [Test]
        public void HandleCommand_UnloadedScene_ReturnsError()
        {
            var result = Audit(new JObject { ["scene"] = "ThisSceneIsNotLoaded_ZZZ" });
            Assert.IsFalse(result.Value<bool>("success"), result.ToString());
            Assert.IsTrue(result.Value<string>("error").Contains("not loaded"), result.ToString());
        }

        [Test]
        public void HandleCommand_NoCanvases_ReturnsEmptySummary()
        {
            var result = Audit(new JObject());
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var summary = result["data"]["summary"];
            Assert.IsNotNull(summary, result.ToString());
            // findings is always present (possibly empty) and never contains images.
            Assert.IsNotNull(result["data"]["findings"], result.ToString());
        }

        [Test]
        public void HandleCommand_ResponseShape_HasFindingsSummaryCaveats()
        {
            var result = Audit(new JObject());
            Assert.IsTrue(result.Value<bool>("success"), result.ToString());
            var data = result["data"];
            Assert.IsNotNull(data["findings"], result.ToString());
            Assert.IsNotNull(data["summary"], result.ToString());
            Assert.IsNotNull(data["caveats"], result.ToString());
            var summary = data["summary"];
            Assert.IsNotNull(summary["by_severity"], result.ToString());
            Assert.IsNotNull(summary["by_check"], result.ToString());
            Assert.IsNotNull(summary["canvases_scanned"], result.ToString());
            Assert.IsNotNull(summary["controls_scanned"], result.ToString());
            Assert.IsNotNull(summary["truncated"], result.ToString());
        }
    }
}
