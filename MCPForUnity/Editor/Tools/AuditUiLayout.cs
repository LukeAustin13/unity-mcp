using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using MCPForUnity.Editor.Helpers;
using MCPForUnity.Runtime.Helpers;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace MCPForUnity.Editor.Tools
{
    /// <summary>
    /// READ-only uGUI layout auditor. Checks Canvas/RectTransform layout across a
    /// configurable resolution matrix using pure ANALYTIC simulation — it NEVER
    /// mutates anything: no GameView resolution switching, no Canvas.ForceUpdateCanvases,
    /// no play mode, no scene dirtying, no asset writes. Screen rects are computed by
    /// hand from anchors/pivot/offsets/localScale against a simulated parent rect, and
    /// CanvasScaler scale factors are derived with Unity's own formulas.
    ///
    /// Because layout-driven components (LayoutGroup / ContentSizeFitter /
    /// AspectRatioFitter) and DPI-dependent scaling can differ at runtime from the
    /// serialized values we read, any finding that could be wrong for that reason is
    /// marked advisory:true rather than dropped.
    ///
    /// All uGUI package types (CanvasScaler, GraphicRaycaster, Selectable, Graphic,
    /// Text) and all TextMeshPro types are accessed via REFLECTION: the MCPForUnity
    /// Editor assembly does not (and must not) hard-reference the UnityEngine.UI or
    /// TextMeshPro assemblies, so direct references would break the CI compile matrix.
    /// Canvas / CanvasGroup / RectTransform / EventSystem-free geometry come from the
    /// core engine and are used directly.
    /// </summary>
    [McpForUnityTool("audit_ui_layout", AutoRegister = false, Group = "core")]
    public static class AuditUiLayout
    {
        private const int MaxResolutions = 8;
        private const int MinDimension = 16;
        private const int MaxDimension = 16384;
        private const int MinTargetFloor = 8;
        private const int MinTargetCeil = 256;
        private const int MaxFindingsCeil = 2000;
        private const int MaxPairsPerCanvas = 2000;

        private static readonly string[] DefaultResolutions =
            { "1920x1080", "1280x720", "2560x1440", "3440x1440" };

        public static object HandleCommand(JObject @params)
        {
            if (@params == null)
                return new ErrorResponse("Parameters cannot be null.");

            try
            {
                var p = new ToolParams(@params);

                // Fail closed on an unexpected action: this tool only audits.
                string action = p.Get("action");
                if (!string.IsNullOrEmpty(action) &&
                    !string.Equals(action.Trim(), "audit", StringComparison.OrdinalIgnoreCase))
                {
                    return new ErrorResponse(
                        $"Unknown action '{action}'. audit_ui_layout only supports the 'audit' action (or none).");
                }

                // --- resolutions ---
                var rawResolutions = p.GetStringArray("resolutions");
                var resolutions = ParseResolutions(rawResolutions, out string resError);
                if (resError != null)
                    return new ErrorResponse(resError);

                // --- scene (must be loaded; never opened) ---
                string sceneName = p.Get("scene");
                if (!string.IsNullOrEmpty(sceneName) && !IsSceneLoaded(sceneName))
                {
                    return new ErrorResponse(
                        $"Scene '{sceneName}' is not loaded. audit_ui_layout never opens scenes — " +
                        "open it in the Editor (or load it additively) and retry.");
                }

                bool includeInactive = p.GetBool("include_inactive", false);
                int minTargetPx = Mathf.Clamp(p.GetInt("min_target_px") ?? 44, MinTargetFloor, MinTargetCeil);
                int maxFindings = Mathf.Clamp(p.GetInt("max_findings") ?? 500, 1, MaxFindingsCeil);

                var findings = new List<Finding>();
                var caveats = new List<string>();
                var bySeverity = new Dictionary<string, int>();
                var byCheck = new Dictionary<string, int>();
                var emittedKeys = new HashSet<string>(StringComparer.Ordinal);
                bool truncated = false;

                int canvasesScanned = 0;
                int controlsScanned = 0;

                foreach (var canvasGo in EnumerateCanvasRoots(sceneName, includeInactive))
                {
                    canvasesScanned++;
                    var canvasInfo = CanvasInfo.Build(canvasGo);

                    // Gather this canvas' controls once (Selectables + descendants of interest).
                    var controls = CollectControls(canvasGo, includeInactive);
                    controlsScanned += controls.Count;

                    // --- structural (resolution-independent) checks ---
                    RunStructuralChecks(canvasInfo, controls, sceneName, findings, emittedKeys, ref truncated, maxFindings);

                    if (canvasInfo.IsWorldSpace)
                    {
                        caveats.Add(
                            $"Canvas '{GameObjectLookup.GetGameObjectPath(canvasGo)}' is WorldSpace; " +
                            "geometric (per-resolution) checks were skipped — world-space rects are not screen-relative.");
                        continue;
                    }

                    if (canvasInfo.ScalerModeAdvisory != null)
                        caveats.Add(canvasInfo.ScalerModeAdvisory);

                    // --- per-resolution analytic checks ---
                    foreach (var res in resolutions)
                    {
                        float scaleFactor = ComputeCanvasScaleFactor(canvasInfo, res.W, res.H);
                        var canvasRect = new Rect(0f, 0f, res.W / scaleFactor, res.H / scaleFactor);
                        var screen = new Rect(0f, 0f, res.W, res.H);

                        RunPerResolutionChecks(
                            canvasInfo, controls, res, scaleFactor, canvasRect, screen,
                            minTargetPx, sceneName, findings, emittedKeys, caveats, ref truncated, maxFindings);
                    }
                }

                foreach (var f in findings)
                {
                    bySeverity[f.Severity] = bySeverity.TryGetValue(f.Severity, out int s) ? s + 1 : 1;
                    byCheck[f.Check] = byCheck.TryGetValue(f.Check, out int c) ? c + 1 : 1;
                }

                var payload = new Dictionary<string, object>
                {
                    ["findings"] = findings.Select(f => f.ToDict()).ToList(),
                    ["summary"] = new Dictionary<string, object>
                    {
                        ["by_severity"] = bySeverity,
                        ["by_check"] = byCheck,
                        ["canvases_scanned"] = canvasesScanned,
                        ["controls_scanned"] = controlsScanned,
                        ["truncated"] = truncated,
                    },
                    ["caveats"] = caveats.Distinct().ToList(),
                };

                return new SuccessResponse(
                    $"Audited {canvasesScanned} canvas(es), {controlsScanned} control(s); {findings.Count} finding(s).",
                    payload);
            }
            catch (Exception ex)
            {
                return new ErrorResponse($"Error in audit_ui_layout: {ex.Message}", new { stackTrace = ex.StackTrace });
            }
        }

        // ==================================================================
        //  Analytic helpers (internal static → exercised directly by tests)
        // ==================================================================

        internal readonly struct Resolution
        {
            public readonly int W;
            public readonly int H;
            public Resolution(int w, int h) { W = w; H = h; }
            public string Label => $"{W}x{H}";
        }

        /// <summary>
        /// Strict WxH integer parse. Returns null + error on any malformed entry, an
        /// out-of-range dimension (16..16384), or more than 8 entries. With no input,
        /// returns the default matrix.
        /// </summary>
        internal static List<Resolution> ParseResolutions(string[] raw, out string error)
        {
            error = null;
            var source = (raw == null || raw.Length == 0) ? DefaultResolutions : raw;

            if (source.Length > MaxResolutions)
            {
                error = $"Too many resolutions ({source.Length}); the maximum is {MaxResolutions}.";
                return null;
            }

            var result = new List<Resolution>();
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var entry in source)
            {
                if (!TryParseResolution(entry, out var res, out string entryError))
                {
                    error = entryError;
                    return null;
                }
                if (seen.Add(res.Label))
                    result.Add(res);
            }

            if (result.Count == 0)
            {
                error = "No valid resolutions provided.";
                return null;
            }
            return result;
        }

        internal static bool TryParseResolution(string entry, out Resolution res, out string error)
        {
            res = default;
            error = null;
            if (string.IsNullOrWhiteSpace(entry))
            {
                error = "Empty resolution entry. Use strict 'WxH' form, e.g. '1920x1080'.";
                return false;
            }

            string trimmed = entry.Trim();
            int xIndex = trimmed.IndexOf('x');
            if (xIndex < 0)
                xIndex = trimmed.IndexOf('X');

            if (xIndex <= 0 || xIndex >= trimmed.Length - 1)
            {
                error = $"Invalid resolution '{entry}'. Use strict 'WxH' form, e.g. '1920x1080'.";
                return false;
            }

            string wStr = trimmed.Substring(0, xIndex);
            string hStr = trimmed.Substring(xIndex + 1);

            if (!int.TryParse(wStr, out int w) || !int.TryParse(hStr, out int h))
            {
                error = $"Invalid resolution '{entry}'. Both dimensions must be integers, e.g. '1920x1080'.";
                return false;
            }

            if (w < MinDimension || w > MaxDimension || h < MinDimension || h > MaxDimension)
            {
                error = $"Resolution '{entry}' out of range. Each dimension must be {MinDimension}..{MaxDimension}.";
                return false;
            }

            res = new Resolution(w, h);
            return true;
        }

        /// <summary>
        /// Analytic CanvasScaler scale factor for a target pixel size. Implements the exact
        /// Unity formulas per uiScaleMode:
        ///   ConstantPixelSize    → the serialized scaleFactor as-is.
        ///   ScaleWithScreenSize  → log2-lerp of width/height ratios against the reference
        ///                          resolution, weighted by matchWidthOrHeight; Shrink/Expand
        ///                          use min/max of the two ratios.
        ///   ConstantPhysicalSize → 1 (DPI unknown headless; treated as advisory elsewhere).
        /// Guards against zero/negative reference dimensions.
        /// </summary>
        internal static float ComputeCanvasScaleFactor(CanvasInfo canvas, int screenW, int screenH)
        {
            switch (canvas.ScaleMode)
            {
                case CanvasScaleMode.ConstantPixelSize:
                    return canvas.ScaleFactor <= 0f ? 1f : canvas.ScaleFactor;

                case CanvasScaleMode.ScaleWithScreenSize:
                    return ScaleWithScreenSizeFactor(
                        screenW, screenH,
                        canvas.ReferenceResolution,
                        canvas.MatchWidthOrHeight,
                        canvas.ScreenMatchMode);

                case CanvasScaleMode.ConstantPhysicalSize:
                default:
                    // DPI is unknown in a headless/analytic context; assume 1.
                    return 1f;
            }
        }

        internal static float ScaleWithScreenSizeFactor(
            int screenW, int screenH, Vector2 reference, float match, ScreenMatchMode matchMode)
        {
            float refW = reference.x <= 0f ? 1f : reference.x;
            float refH = reference.y <= 0f ? 1f : reference.y;

            switch (matchMode)
            {
                case ScreenMatchMode.Shrink:
                {
                    float ratio = Mathf.Min(screenW / refW, screenH / refH);
                    return ratio <= 0f ? 1f : ratio;
                }
                case ScreenMatchMode.Expand:
                {
                    float ratio = Mathf.Max(screenW / refW, screenH / refH);
                    return ratio <= 0f ? 1f : ratio;
                }
                case ScreenMatchMode.MatchWidthOrHeight:
                default:
                {
                    // Unity: lerp in log2 space between the width and height ratios.
                    float logWidth = Mathf.Log(screenW / refW, 2f);
                    float logHeight = Mathf.Log(screenH / refH, 2f);
                    float logWeighted = Mathf.Lerp(logWidth, logHeight, Mathf.Clamp01(match));
                    float scale = Mathf.Pow(2f, logWeighted);
                    return scale <= 0f ? 1f : scale;
                }
            }
        }

        /// <summary>
        /// Computes a child's screen-space rect purely from anchors/pivot/offsets/localScale
        /// against a simulated parent rect. No lossyScale / live layout is read. The parent
        /// rect is in canvas-local pixels; localScale is applied about the pivot. The result is
        /// in the same coordinate space as <paramref name="parentRect"/> (origin bottom-left).
        /// </summary>
        internal static Rect ComputeChildRect(
            Rect parentRect,
            Vector2 anchorMin, Vector2 anchorMax,
            Vector2 offsetMin, Vector2 offsetMax,
            Vector2 pivot, Vector2 localScale)
        {
            // Anchor reference points inside the parent (px).
            float anchorLeft = parentRect.xMin + anchorMin.x * parentRect.width;
            float anchorRight = parentRect.xMin + anchorMax.x * parentRect.width;
            float anchorBottom = parentRect.yMin + anchorMin.y * parentRect.height;
            float anchorTop = parentRect.yMin + anchorMax.y * parentRect.height;

            // offsetMin = (left, bottom) inset from anchors; offsetMax = (right, top).
            float left = anchorLeft + offsetMin.x;
            float bottom = anchorBottom + offsetMin.y;
            float right = anchorRight + offsetMax.x;
            float top = anchorTop + offsetMax.y;

            float width = right - left;
            float height = top - bottom;

            // Apply localScale about the pivot so scaling a control shrinks/grows in place.
            float pivotX = left + pivot.x * width;
            float pivotY = bottom + pivot.y * height;
            float scaledWidth = width * localScale.x;
            float scaledHeight = height * localScale.y;
            float scaledLeft = pivotX - pivot.x * scaledWidth;
            float scaledBottom = pivotY - pivot.y * scaledHeight;

            return new Rect(scaledLeft, scaledBottom, scaledWidth, scaledHeight);
        }

        /// <summary>Fraction (0..1) of <paramref name="rect"/> lying outside <paramref name="screen"/>.</summary>
        internal static float FractionOffScreen(Rect rect, Rect screen)
        {
            float area = Mathf.Abs(rect.width) * Mathf.Abs(rect.height);
            if (area <= Mathf.Epsilon)
                return 1f; // a zero-area rect is treated as fully off-screen for auditing

            Rect norm = Normalize(rect);
            float ix = Mathf.Max(0f, Mathf.Min(norm.xMax, screen.xMax) - Mathf.Max(norm.xMin, screen.xMin));
            float iy = Mathf.Max(0f, Mathf.Min(norm.yMax, screen.yMax) - Mathf.Max(norm.yMin, screen.yMin));
            float inside = ix * iy;
            return Mathf.Clamp01(1f - inside / area);
        }

        internal static bool IsFullyOffScreen(Rect rect, Rect screen)
        {
            Rect norm = Normalize(rect);
            return norm.xMax <= screen.xMin || norm.xMin >= screen.xMax
                || norm.yMax <= screen.yMin || norm.yMin >= screen.yMax;
        }

        internal static bool RectsOverlap(Rect a, Rect b)
        {
            Rect na = Normalize(a), nb = Normalize(b);
            return na.xMin < nb.xMax && nb.xMin < na.xMax
                && na.yMin < nb.yMax && nb.yMin < na.yMax;
        }

        /// <summary>True when <paramref name="inner"/> is fully covered by <paramref name="outer"/>.</summary>
        internal static bool FullyCovers(Rect outer, Rect inner)
        {
            Rect o = Normalize(outer), i = Normalize(inner);
            if (i.width <= Mathf.Epsilon || i.height <= Mathf.Epsilon)
                return false;
            return o.xMin <= i.xMin && o.yMin <= i.yMin && o.xMax >= i.xMax && o.yMax >= i.yMax;
        }

        internal static bool IsTinyTarget(Rect rect, float minPx)
        {
            return Mathf.Abs(rect.width) < minPx || Mathf.Abs(rect.height) < minPx;
        }

        /// <summary>
        /// "Breaks at other aspect ratios" smell: a point anchor (anchorMin == anchorMax)
        /// whose |anchoredPosition| or |sizeDelta| exceeds ~60% of the reference resolution
        /// on either axis. Point-anchored controls do not stretch, so a large offset/size
        /// pins them to absolute pixels that overflow narrower/taller screens.
        /// </summary>
        internal static bool IsSuspiciousAnchor(
            Vector2 anchorMin, Vector2 anchorMax, Vector2 anchoredPosition, Vector2 sizeDelta, Vector2 reference)
        {
            bool pointAnchor =
                Mathf.Approximately(anchorMin.x, anchorMax.x) &&
                Mathf.Approximately(anchorMin.y, anchorMax.y);
            if (!pointAnchor) return false;

            float refW = reference.x <= 0f ? 1f : reference.x;
            float refH = reference.y <= 0f ? 1f : reference.y;
            const float threshold = 0.6f;

            return Mathf.Abs(anchoredPosition.x) > threshold * refW
                || Mathf.Abs(anchoredPosition.y) > threshold * refH
                || Mathf.Abs(sizeDelta.x) > threshold * refW
                || Mathf.Abs(sizeDelta.y) > threshold * refH;
        }

        private static Rect Normalize(Rect r)
        {
            float x = r.width < 0 ? r.x + r.width : r.x;
            float y = r.height < 0 ? r.y + r.height : r.y;
            return new Rect(x, y, Mathf.Abs(r.width), Mathf.Abs(r.height));
        }

        // ==================================================================
        //  Checks
        // ==================================================================

        private static void RunStructuralChecks(
            CanvasInfo canvas, List<ControlInfo> controls, string sceneName,
            List<Finding> findings, HashSet<string> emitted, ref bool truncated, int maxFindings)
        {
            bool anyInteractable = controls.Any(c => c.IsInteractable);

            // missing_event_system (error): interactable Selectables but no active EventSystem.
            if (anyInteractable && !HasActiveEventSystem())
            {
                Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                    "missing_event_system", "error", false, sceneName, canvas.Path, null, null,
                    "Scene has interactable UI controls but no active EventSystem — clicks/keys are never delivered.",
                    "Add an EventSystem (GameObject > UI > Event System)."));
            }

            // missing_graphic_raycaster (error): canvas with interactables lacks a GraphicRaycaster.
            if (anyInteractable && !canvas.HasGraphicRaycaster)
            {
                Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                    "missing_graphic_raycaster", "error", false, sceneName, canvas.Path, null, null,
                    "Canvas contains interactable controls but has no GraphicRaycaster — none of them can receive pointer input.",
                    "Add a GraphicRaycaster component to this Canvas."));
            }

            // canvas_scaler (warning, advisory): ScreenSpace scaler smells.
            foreach (var f in CanvasScalerFindings(canvas, sceneName))
                Emit(findings, emitted, ref truncated, maxFindings, f);

            foreach (var control in controls)
            {
                // missing_listeners (info, advisory): 0 persistent listeners on primary event.
                if (control.SupportsListeners && control.PersistentListenerCount == 0)
                {
                    Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                        "missing_listeners", "info", true, sceneName, control.Path, null, null,
                        $"{control.KindName} has 0 persistent listeners on its primary event. " +
                        "Runtime (AddListener) handlers are invisible to static analysis, so this may be a false positive.",
                        "Wire a handler in the Inspector, or ignore if listeners are added in code."));
                }

                // invisible_interactable (warning): interactable but effectively invisible.
                if (control.IsInteractable && control.IsEffectivelyInvisible)
                {
                    Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                        "invisible_interactable", "warning", false, sceneName, control.Path, null, null,
                        control.InvisibleReason,
                        "Give the control a visible Graphic (alpha > 0), or disable interactable/blocksRaycasts if it is intentionally hidden."));
                }

                // suspicious_anchors (info, advisory): point anchor with a large offset/size.
                if (IsSuspiciousAnchor(
                        control.AnchorMin, control.AnchorMax, control.AnchoredPosition,
                        control.SizeDelta, canvas.ReferenceResolution))
                {
                    Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                        "suspicious_anchors", "info", true, sceneName, control.Path, null, null,
                        "Point anchor (anchorMin == anchorMax) with an anchoredPosition/sizeDelta larger than ~60% of the " +
                        $"reference resolution ({(int)canvas.ReferenceResolution.x}x{(int)canvas.ReferenceResolution.y}) — " +
                        "likely to break at other aspect ratios. This can be intentional for fixed-pixel HUD elements.",
                        "Use a stretch anchor, or anchor to the relevant screen edge, so the layout adapts across aspect ratios."));
                }
            }
        }

        private static void RunPerResolutionChecks(
            CanvasInfo canvas, List<ControlInfo> controls, Resolution res,
            float scaleFactor, Rect canvasRect, Rect screen, int minTargetPx, string sceneName,
            List<Finding> findings, HashSet<string> emitted, List<string> caveats,
            ref bool truncated, int maxFindings)
        {
            // Simulated screen rect for the canvas root itself is the full screen.
            // Compute each control's screen rect from the chain of RectTransforms.
            var rects = new Dictionary<ControlInfo, Rect>();
            foreach (var control in controls)
            {
                Rect local = ComputeScreenRectForControl(canvas, control, canvasRect);
                Rect screenRect = new Rect(local.x * scaleFactor, local.y * scaleFactor,
                                           local.width * scaleFactor, local.height * scaleFactor);
                rects[control] = screenRect;

                if (!control.IsInteractable)
                    continue;

                bool advisory = control.LayoutDriven; // LayoutGroup/Fitter under/at this node

                // off_screen
                if (IsFullyOffScreen(screenRect, screen))
                {
                    Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                        "off_screen", "error", advisory, sceneName, control.Path, res.Label, RectDict(screenRect),
                        $"Interactable control is fully outside the {res.Label} screen.",
                        "Re-anchor or reposition so it lands inside the viewport at this resolution."));
                }
                else
                {
                    float off = FractionOffScreen(screenRect, screen);
                    if (off > 0.5f)
                    {
                        Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                            "off_screen", "warning", advisory, sceneName, control.Path, res.Label, RectDict(screenRect),
                            $"Interactable control is {Mathf.RoundToInt(off * 100f)}% outside the {res.Label} screen.",
                            "Re-anchor or reposition so it is fully on-screen at this resolution."));
                    }
                }

                // tiny_target
                if (!IsFullyOffScreen(screenRect, screen) && IsTinyTarget(screenRect, minTargetPx))
                {
                    Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                        "tiny_target", "warning", advisory, sceneName, control.Path, res.Label, RectDict(screenRect),
                        $"Interactable target is {Mathf.RoundToInt(Mathf.Abs(screenRect.width))}x{Mathf.RoundToInt(Mathf.Abs(screenRect.height))}px at {res.Label}, " +
                        $"smaller than the {minTargetPx}px minimum touch target in at least one dimension.",
                        "Enlarge the control (or its hit area) to at least the minimum touch-target size."));
                }
            }

            // overlapping_interactive (warning): pairwise overlaps between raycastable interactables.
            var interactables = controls.Where(c => c.IsInteractable && c.IsRaycastTarget).ToList();
            int pairBudget = MaxPairsPerCanvas;
            bool overBudget = false;
            for (int i = 0; i < interactables.Count && !overBudget; i++)
            {
                for (int j = i + 1; j < interactables.Count; j++)
                {
                    if (pairBudget-- <= 0) { overBudget = true; break; }
                    var a = interactables[i];
                    var b = interactables[j];
                    if (a.SharesHierarchyChainWith(b))
                        continue; // ignore parent/child pairs
                    if (RectsOverlap(rects[a], rects[b]))
                    {
                        bool advisory = a.LayoutDriven || b.LayoutDriven;
                        Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                            "overlapping_interactive", "warning", advisory, sceneName,
                            $"{a.Path} <> {b.Path}", res.Label, RectDict(rects[a]),
                            $"Two interactable controls overlap at {res.Label}; the one drawn last will steal input from the other.",
                            "Separate their rects, or ensure only the intended one is a raycast target."));
                    }
                }
            }
            if (overBudget)
                caveats.Add(
                    $"Canvas '{canvas.Path}' at {res.Label}: overlap check truncated at {MaxPairsPerCanvas} pairs.");

            // blocked_interactable (info, advisory): fully covered by a higher raycast-target graphic.
            var raycastGraphics = controls.Where(c => c.IsRaycastTarget).ToList();
            foreach (var control in controls)
            {
                if (!control.IsInteractable) continue;
                Rect target = rects[control];
                foreach (var cover in raycastGraphics)
                {
                    if (ReferenceEquals(cover, control)) continue;
                    if (cover.SharesHierarchyChainWith(control)) continue;
                    if (!IsDrawnAbove(cover, control)) continue;
                    if (FullyCovers(rects[cover], target))
                    {
                        Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                            "blocked_interactable", "info", true, sceneName, control.Path, res.Label, RectDict(target),
                            $"Interactable control appears fully covered by raycast-target '{cover.Path}' drawn above it at {res.Label}. " +
                            "Draw order is approximated from sibling index / sort order, so this may be a false positive.",
                            "If the cover should not block input, disable its raycastTarget, or reorder so the control is on top."));
                        break;
                    }
                }

                // clipped_text (info, advisory): preferred size exceeds the rect.
                if (control.IsText && control.TryGetPreferredSize(out Vector2 preferred) &&
                    !control.TextHandlesOverflow)
                {
                    Rect r = rects[control];
                    float w = Mathf.Abs(r.width), h = Mathf.Abs(r.height);
                    if (preferred.x > w + 0.5f || preferred.y > h + 0.5f)
                    {
                        Emit(findings, emitted, ref truncated, maxFindings, new Finding(
                            "clipped_text", "info", true, sceneName, control.Path, res.Label, RectDict(r),
                            $"Text preferred size ({Mathf.RoundToInt(preferred.x)}x{Mathf.RoundToInt(preferred.y)}px) exceeds its rect " +
                            $"({Mathf.RoundToInt(w)}x{Mathf.RoundToInt(h)}px) at {res.Label} with no auto-size/overflow handling — text may be clipped. " +
                            "GetPreferredValues is a static estimate; verify visually.",
                            "Enable auto-sizing/wrapping, enlarge the rect, or shorten the text."));
                    }
                }
            }
        }

        private static IEnumerable<Finding> CanvasScalerFindings(CanvasInfo canvas, string sceneName)
        {
            if (canvas.IsWorldSpace)
                yield break;

            if (!canvas.HasCanvasScaler)
            {
                yield return new Finding(
                    "canvas_scaler", "warning", true, sceneName, canvas.Path, null, null,
                    "ScreenSpace canvas has no CanvasScaler — UI will not scale across resolutions.",
                    "Add a CanvasScaler (usually ScaleWithScreenSize) to keep the UI consistent across resolutions.");
                yield break;
            }

            if (canvas.ScaleMode == CanvasScaleMode.ConstantPixelSize)
            {
                yield return new Finding(
                    "canvas_scaler", "warning", true, sceneName, canvas.Path, null, null,
                    "CanvasScaler is in Constant Pixel Size mode — the UI will not adapt to different screen resolutions.",
                    "Switch to Scale With Screen Size for resolution-independent layout, unless a fixed pixel size is intended.");
            }
            else if (canvas.ScaleMode == CanvasScaleMode.ScaleWithScreenSize &&
                     canvas.ScreenMatchMode == ScreenMatchMode.MatchWidthOrHeight &&
                     (Mathf.Approximately(canvas.MatchWidthOrHeight, 0f) || Mathf.Approximately(canvas.MatchWidthOrHeight, 1f)))
            {
                string axis = Mathf.Approximately(canvas.MatchWidthOrHeight, 0f) ? "width" : "height";
                yield return new Finding(
                    "canvas_scaler", "warning", true, sceneName, canvas.Path, null, null,
                    $"CanvasScaler matchWidthOrHeight is {(int)canvas.MatchWidthOrHeight} (matches {axis} only) — " +
                    "other aspect ratios can over/under-scale. This is sometimes intentional.",
                    "Consider matchWidthOrHeight 0.5 for a balanced match, unless matching one axis is deliberate.");
            }
        }

        // ==================================================================
        //  Screen-rect walk
        // ==================================================================

        /// <summary>
        /// Walks the RectTransform chain from the canvas root down to the control, applying
        /// ComputeChildRect at each hop, starting from the simulated canvas rect. Pure math.
        /// </summary>
        private static Rect ComputeScreenRectForControl(CanvasInfo canvas, ControlInfo control, Rect canvasRect)
        {
            var chain = control.RectChainFromCanvas; // canvas-root child .. control (excludes canvas root)
            Rect current = canvasRect;
            foreach (var rt in chain)
            {
                current = ComputeChildRect(
                    current,
                    rt.anchorMin, rt.anchorMax,
                    rt.offsetMin, rt.offsetMax,
                    rt.pivot,
                    new Vector2(rt.localScale.x, rt.localScale.y));
            }
            return current;
        }

        // ==================================================================
        //  Scene / canvas / control gathering
        // ==================================================================

        private static bool IsSceneLoaded(string sceneName)
        {
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (scene.IsValid() && scene.isLoaded && scene.name == sceneName)
                    return true;
            }
            return false;
        }

        private static IEnumerable<GameObject> EnumerateCanvasRoots(string sceneName, bool includeInactive)
        {
            var canvasType = ReflectionTypes.Canvas;
            if (canvasType == null)
                yield break;

            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (!scene.IsValid() || !scene.isLoaded) continue;
                if (!string.IsNullOrEmpty(sceneName) && scene.name != sceneName) continue;

                foreach (var root in scene.GetRootGameObjects())
                {
                    foreach (var go in DepthFirst(root, includeInactive))
                    {
                        var canvas = go.GetComponent(canvasType);
                        if (canvas == null) continue;
                        // Only treat this as a scan root when it is a top-level Canvas
                        // (no Canvas ancestor) to avoid double-scanning nested canvases.
                        if (!HasCanvasAncestor(go, canvasType))
                            yield return go;
                    }
                }
            }
        }

        private static bool HasCanvasAncestor(GameObject go, Type canvasType)
        {
            var t = go.transform.parent;
            while (t != null)
            {
                if (t.GetComponent(canvasType) != null)
                    return true;
                t = t.parent;
            }
            return false;
        }

        private static IEnumerable<GameObject> DepthFirst(GameObject go, bool includeInactive)
        {
            if (go == null) yield break;
            if (!includeInactive && !go.activeInHierarchy) yield break;
            yield return go;
            var t = go.transform;
            for (int i = 0; i < t.childCount; i++)
            {
                var child = t.GetChild(i);
                if (child == null) continue;
                foreach (var d in DepthFirst(child.gameObject, includeInactive))
                    yield return d;
            }
        }

        private static List<ControlInfo> CollectControls(GameObject canvasRoot, bool includeInactive)
        {
            var result = new List<ControlInfo>();
            // DepthFirst yields in uGUI paint order, so the list index is a stable
            // draw-order rank (later index = drawn on top within the same canvas).
            int order = 0;
            foreach (var go in DepthFirst(canvasRoot, includeInactive))
            {
                var info = ControlInfo.TryBuild(go, canvasRoot);
                if (info != null)
                {
                    info.HierarchyOrder = order;
                    result.Add(info);
                }
                order++;
            }
            return result;
        }

        private static bool HasActiveEventSystem()
        {
            var esType = ReflectionTypes.EventSystem;
            if (esType == null) return false;
            var current = esType.GetProperty("current", BindingFlags.Public | BindingFlags.Static);
            if (current != null)
            {
                var value = current.GetValue(null) as UnityEngine.Object;
                if (value != null) return true;
            }
            foreach (var es in UnityFindObjectsCompat.FindAll(esType))
            {
                if (es is Behaviour b && b.isActiveAndEnabled)
                    return true;
            }
            return false;
        }

        // ==================================================================
        //  Draw-order approximation (conservative)
        // ==================================================================

        private static bool IsDrawnAbove(ControlInfo cover, ControlInfo target)
        {
            // Higher canvas sortingOrder wins.
            if (cover.CanvasSortOrder != target.CanvasSortOrder)
                return cover.CanvasSortOrder > target.CanvasSortOrder;
            // Same canvas: later in the hierarchy draws on top (uGUI paints in sibling order).
            return cover.HierarchyOrder > target.HierarchyOrder;
        }

        // ==================================================================
        //  Finding + serialization
        // ==================================================================

        private static void Emit(
            List<Finding> findings, HashSet<string> emitted, ref bool truncated, int maxFindings, Finding f)
        {
            // Dedupe: one (check, object_path, resolution) per audit.
            string key = f.Check + "|" + f.ObjectPath + "|" + (f.Resolution ?? "");
            if (!emitted.Add(key))
                return;
            if (findings.Count >= maxFindings)
            {
                truncated = true;
                return;
            }
            findings.Add(f);
        }

        private static Dictionary<string, object> RectDict(Rect r)
        {
            return new Dictionary<string, object>
            {
                ["x"] = Round(r.x),
                ["y"] = Round(r.y),
                ["w"] = Round(r.width),
                ["h"] = Round(r.height),
            };
        }

        private static float Round(float v) => Mathf.Round(v * 100f) / 100f;

        internal sealed class Finding
        {
            public readonly string Check;
            public readonly string Severity;
            public readonly bool Advisory;
            public readonly string Scene;
            public readonly string ObjectPath;
            public readonly string Resolution;
            public readonly Dictionary<string, object> ScreenRect;
            public readonly string Reason;
            public readonly string SuggestedFix;

            public Finding(string check, string severity, bool advisory, string scene, string objectPath,
                string resolution, Dictionary<string, object> screenRect, string reason, string suggestedFix)
            {
                Check = check;
                Severity = severity;
                Advisory = advisory;
                Scene = scene;
                ObjectPath = objectPath;
                Resolution = resolution;
                ScreenRect = screenRect;
                Reason = reason;
                SuggestedFix = suggestedFix;
            }

            public Dictionary<string, object> ToDict()
            {
                return new Dictionary<string, object>
                {
                    ["check"] = Check,
                    ["severity"] = Severity,
                    ["advisory"] = Advisory,
                    ["scene"] = Scene,
                    ["object_path"] = ObjectPath,
                    ["resolution"] = Resolution,
                    ["screen_rect"] = ScreenRect,
                    ["reason"] = Reason,
                    ["suggested_fix"] = SuggestedFix,
                };
            }
        }
    }

    // ======================================================================
    //  CanvasScaler enums (mirrors of the uGUI package enums we read reflectively)
    // ======================================================================

    internal enum CanvasScaleMode { ConstantPixelSize = 0, ScaleWithScreenSize = 1, ConstantPhysicalSize = 2 }
    internal enum ScreenMatchMode { MatchWidthOrHeight = 0, Expand = 1, Shrink = 2 }

    // ======================================================================
    //  Reflection type cache — uGUI + TMP live in package assemblies that the
    //  Editor asmdef must NOT hard-reference, so every type is resolved by name
    //  across loaded assemblies (mirrors GameObjectLookup.FindComponentType).
    // ======================================================================

    internal static class ReflectionTypes
    {
        private static readonly Dictionary<string, Type> Cache = new(StringComparer.Ordinal);

        internal static readonly Type Canvas = Resolve("UnityEngine.Canvas");
        internal static readonly Type CanvasScaler = Resolve("UnityEngine.UI.CanvasScaler");
        internal static readonly Type GraphicRaycaster = Resolve("UnityEngine.UI.GraphicRaycaster");
        internal static readonly Type Selectable = Resolve("UnityEngine.UI.Selectable");
        internal static readonly Type Graphic = Resolve("UnityEngine.UI.Graphic");
        internal static readonly Type Button = Resolve("UnityEngine.UI.Button");
        internal static readonly Type Toggle = Resolve("UnityEngine.UI.Toggle");
        internal static readonly Type Dropdown = Resolve("UnityEngine.UI.Dropdown");
        internal static readonly Type LegacyText = Resolve("UnityEngine.UI.Text");
        internal static readonly Type LayoutGroup = Resolve("UnityEngine.UI.LayoutGroup");
        internal static readonly Type ContentSizeFitter = Resolve("UnityEngine.UI.ContentSizeFitter");
        internal static readonly Type AspectRatioFitter = Resolve("UnityEngine.UI.AspectRatioFitter");
        internal static readonly Type EventSystem = Resolve("UnityEngine.EventSystems.EventSystem");
        internal static readonly Type TmpText = Resolve("TMPro.TMP_Text");
        internal static readonly Type TmpDropdown = Resolve("TMPro.TMP_Dropdown");

        internal static Type Resolve(string fullName)
        {
            if (Cache.TryGetValue(fullName, out var cached))
                return cached;

            Type found = Type.GetType(fullName);
            if (found == null)
            {
                foreach (var asm in AppDomain.CurrentDomain.GetAssemblies())
                {
                    try { found = asm.GetType(fullName); }
                    catch { found = null; }
                    if (found != null) break;
                }
            }
            Cache[fullName] = found;
            return found;
        }
    }

    // ======================================================================
    //  CanvasInfo — reads Canvas + CanvasScaler serialized values reflectively.
    // ======================================================================

    internal sealed class CanvasInfo
    {
        public string Path;
        public bool IsWorldSpace;
        public bool HasCanvasScaler;
        public bool HasGraphicRaycaster;
        public CanvasScaleMode ScaleMode = CanvasScaleMode.ConstantPixelSize;
        public ScreenMatchMode ScreenMatchMode = ScreenMatchMode.MatchWidthOrHeight;
        public float ScaleFactor = 1f;
        public float MatchWidthOrHeight = 0.5f;
        public Vector2 ReferenceResolution = new Vector2(800f, 600f);
        public string ScalerModeAdvisory;

        public static CanvasInfo Build(GameObject canvasGo)
        {
            var info = new CanvasInfo { Path = GameObjectLookup.GetGameObjectPath(canvasGo) };

            var canvas = canvasGo.GetComponent(ReflectionTypes.Canvas);
            if (canvas != null)
            {
                object renderMode = GetProp(canvas, "renderMode");
                // RenderMode enum: 0=ScreenSpaceOverlay, 1=ScreenSpaceCamera, 2=WorldSpace.
                info.IsWorldSpace = renderMode != null && Convert.ToInt32(renderMode) == 2;
            }

            if (ReflectionTypes.GraphicRaycaster != null)
                info.HasGraphicRaycaster = canvasGo.GetComponent(ReflectionTypes.GraphicRaycaster) != null;

            var scaler = ReflectionTypes.CanvasScaler != null
                ? canvasGo.GetComponent(ReflectionTypes.CanvasScaler)
                : null;
            if (scaler != null)
            {
                info.HasCanvasScaler = true;
                object modeVal = GetProp(scaler, "uiScaleMode");
                if (modeVal != null)
                    info.ScaleMode = (CanvasScaleMode)Convert.ToInt32(modeVal);

                object matchModeVal = GetProp(scaler, "screenMatchMode");
                if (matchModeVal != null)
                    info.ScreenMatchMode = (ScreenMatchMode)Convert.ToInt32(matchModeVal);

                object sf = GetProp(scaler, "scaleFactor");
                if (sf != null) info.ScaleFactor = Convert.ToSingle(sf);

                object match = GetProp(scaler, "matchWidthOrHeight");
                if (match != null) info.MatchWidthOrHeight = Convert.ToSingle(match);

                object refRes = GetProp(scaler, "referenceResolution");
                if (refRes is Vector2 v) info.ReferenceResolution = v;

                if (info.ScaleMode == CanvasScaleMode.ConstantPhysicalSize)
                    info.ScalerModeAdvisory =
                        $"Canvas '{info.Path}' uses ConstantPhysicalSize; DPI is unknown in analytic mode, so its rects are approximate (advisory).";
            }

            return info;
        }

        private static object GetProp(object target, string name)
        {
            if (target == null) return null;
            var prop = target.GetType().GetProperty(name, BindingFlags.Public | BindingFlags.Instance);
            if (prop == null) return null;
            try { return prop.GetValue(target); }
            catch { return null; }
        }
    }

    // ======================================================================
    //  ControlInfo — one auditable UI node (a Selectable, or a raycast Graphic /
    //  text). All uGUI/TMP access is reflective.
    // ======================================================================

    internal sealed class ControlInfo
    {
        public GameObject Go;
        public string Path;
        public string KindName = "Control";

        public bool IsSelectable;
        public bool IsInteractable;
        public bool IsRaycastTarget;
        public bool SupportsListeners;
        public int PersistentListenerCount;
        public bool IsEffectivelyInvisible;
        public string InvisibleReason = "";

        public bool IsText;
        public bool TextHandlesOverflow;

        public bool LayoutDriven;
        public int CanvasSortOrder;
        public int HierarchyOrder;

        // Own RectTransform anchor data (for the suspicious_anchors structural check).
        public Vector2 AnchorMin;
        public Vector2 AnchorMax;
        public Vector2 AnchoredPosition;
        public Vector2 SizeDelta;

        // RectTransform chain from the first child of the canvas root down to this node.
        public List<RectTransform> RectChainFromCanvas = new();

        private object _textComponent; // reflective handle for GetPreferredValues

        public static ControlInfo TryBuild(GameObject go, GameObject canvasRoot)
        {
            var rt = go.GetComponent<RectTransform>();
            if (rt == null) return null;

            var selectable = ReflectionTypes.Selectable != null ? go.GetComponent(ReflectionTypes.Selectable) : null;
            object graphic = ReflectionTypes.Graphic != null ? go.GetComponent(ReflectionTypes.Graphic) : null;

            // Only nodes that are either a Selectable or a raycast-target graphic/text are auditable.
            bool isRaycast = graphic != null && GetBool(graphic, "raycastTarget", false);
            if (selectable == null && !isRaycast)
                return null;

            var info = new ControlInfo
            {
                Go = go,
                Path = GameObjectLookup.GetGameObjectPath(go),
                IsRaycastTarget = isRaycast,
                RectChainFromCanvas = BuildRectChain(go, canvasRoot),
                AnchorMin = rt.anchorMin,
                AnchorMax = rt.anchorMax,
                AnchoredPosition = rt.anchoredPosition,
                SizeDelta = rt.sizeDelta,
            };

            if (selectable != null)
            {
                info.IsSelectable = true;
                info.IsInteractable = GetBool(selectable, "interactable", true) && go.activeInHierarchy;
                info.KindName = selectable.GetType().Name;
                info.PersistentListenerCount = CountPersistentListeners(selectable);
                info.SupportsListeners = ControlSupportsListeners(selectable);
            }

            // CanvasGroup interactable/blocksRaycasts state can hide/disable a control.
            ApplyCanvasGroupState(go, info);

            // Invisibility: graphic alpha ~0 or a CanvasGroup alpha ~0 while still interactive.
            EvaluateVisibility(go, graphic, info);

            // Text (legacy + TMP) preferred-size handle.
            EvaluateText(go, info);

            // Layout-driven ancestry → geometric findings become advisory.
            info.LayoutDriven = HasLayoutDrivenAncestorOrSelf(go, canvasRoot);

            // Canvas sort order of the nearest enclosing Canvas (for draw-order approx).
            info.CanvasSortOrder = NearestCanvasSortOrder(go);

            return info;
        }

        public bool SharesHierarchyChainWith(ControlInfo other)
        {
            if (Go == null || other.Go == null) return false;
            return Go.transform.IsChildOf(other.Go.transform) || other.Go.transform.IsChildOf(Go.transform);
        }

        public bool TryGetPreferredSize(out Vector2 preferred)
        {
            preferred = default;
            if (_textComponent == null) return false;
            try
            {
                var method = _textComponent.GetType().GetMethod("GetPreferredValues", Type.EmptyTypes);
                if (method == null) return false;
                object result = method.Invoke(_textComponent, null);
                if (result is Vector2 v) { preferred = v; return true; }
            }
            catch { }
            return false;
        }

        // --- construction helpers ---

        private static List<RectTransform> BuildRectChain(GameObject go, GameObject canvasRoot)
        {
            var stack = new Stack<RectTransform>();
            var t = go.transform;
            while (t != null && t.gameObject != canvasRoot)
            {
                if (t is RectTransform rt)
                    stack.Push(rt);
                t = t.parent;
            }
            return stack.ToList();
        }

        private static bool GetBool(object component, string prop, bool fallback)
        {
            if (component == null) return fallback;
            var p = component.GetType().GetProperty(prop, BindingFlags.Public | BindingFlags.Instance);
            if (p == null) return fallback;
            try { return (bool)p.GetValue(component); }
            catch { return fallback; }
        }

        private static bool ControlSupportsListeners(object selectable)
        {
            var type = selectable.GetType();
            return IsA(type, ReflectionTypes.Button) || IsA(type, ReflectionTypes.Toggle)
                || IsA(type, ReflectionTypes.Dropdown) || IsA(type, ReflectionTypes.TmpDropdown);
        }

        private static bool IsA(Type actual, Type expected) => expected != null && expected.IsAssignableFrom(actual);

        private static int CountPersistentListeners(object selectable)
        {
            // The primary event is Button.onClick / Toggle.onValueChanged / Dropdown.onValueChanged.
            string eventProp = null;
            var type = selectable.GetType();
            if (IsA(type, ReflectionTypes.Button)) eventProp = "onClick";
            else if (IsA(type, ReflectionTypes.Toggle)) eventProp = "onValueChanged";
            else if (IsA(type, ReflectionTypes.Dropdown) || IsA(type, ReflectionTypes.TmpDropdown)) eventProp = "onValueChanged";
            if (eventProp == null) return -1;

            try
            {
                var prop = type.GetProperty(eventProp, BindingFlags.Public | BindingFlags.Instance);
                var unityEvent = prop?.GetValue(selectable);
                if (unityEvent == null) return 0;
                var count = unityEvent.GetType().GetMethod("GetPersistentEventCount");
                if (count == null) return 0;
                return Convert.ToInt32(count.Invoke(unityEvent, null));
            }
            catch { return 0; }
        }

        private static void ApplyCanvasGroupState(GameObject go, ControlInfo info)
        {
            var t = go.transform;
            while (t != null)
            {
                var cg = t.GetComponent<CanvasGroup>();
                if (cg != null)
                {
                    if (!cg.interactable && info.IsInteractable)
                        info.IsInteractable = false;
                    if (cg.ignoreParentGroups) break;
                }
                t = t.parent;
            }
        }

        private static void EvaluateVisibility(GameObject go, object graphic, ControlInfo info)
        {
            if (!info.IsInteractable) return;

            // Graphic color alpha ~ 0.
            if (graphic != null)
            {
                var colorProp = graphic.GetType().GetProperty("color", BindingFlags.Public | BindingFlags.Instance);
                if (colorProp != null)
                {
                    try
                    {
                        if (colorProp.GetValue(graphic) is Color col && col.a <= 0.01f)
                        {
                            info.IsEffectivelyInvisible = true;
                            info.InvisibleReason =
                                "Interactable control's Graphic alpha is ~0 (invisible) but it still receives input.";
                        }
                    }
                    catch { }
                }
            }

            // CanvasGroup alpha ~0 while still blocking raycasts / interactable.
            var t = go.transform;
            while (t != null)
            {
                var cg = t.GetComponent<CanvasGroup>();
                if (cg != null)
                {
                    if (cg.alpha <= 0.01f && (cg.blocksRaycasts || cg.interactable))
                    {
                        info.IsEffectivelyInvisible = true;
                        info.InvisibleReason =
                            "Control is under a CanvasGroup with alpha ~0 (invisible) but blocksRaycasts/interactable is still on.";
                    }
                    if (cg.ignoreParentGroups) break;
                }
                t = t.parent;
            }
        }

        private static void EvaluateText(GameObject go, ControlInfo info)
        {
            object tmp = ReflectionTypes.TmpText != null ? go.GetComponent(ReflectionTypes.TmpText) : null;
            object legacy = ReflectionTypes.LegacyText != null ? go.GetComponent(ReflectionTypes.LegacyText) : null;
            object text = tmp ?? legacy;
            if (text == null) return;

            info.IsText = true;
            info._textComponent = text;

            // Auto-size or an overflow mode that truncates/ellipsizes handles clipping.
            if (tmp != null)
            {
                bool autoSize = GetBool(tmp, "enableAutoSizing", false);
                info.TextHandlesOverflow = autoSize || TmpOverflowHandled(tmp);
            }
            else
            {
                // Legacy Text: resizeTextForBestFit handles overflow.
                info.TextHandlesOverflow = GetBool(legacy, "resizeTextForBestFit", false);
            }
        }

        private static bool TmpOverflowHandled(object tmp)
        {
            try
            {
                var prop = tmp.GetType().GetProperty("overflowMode", BindingFlags.Public | BindingFlags.Instance);
                if (prop == null) return false;
                int mode = Convert.ToInt32(prop.GetValue(tmp));
                // TextOverflowModes: 0=Overflow, 1=Ellipsis, 2=Masking, 3=Truncate, ...
                // Only "Overflow" (0) leaves clipping unhandled; anything else copes.
                return mode != 0;
            }
            catch { return false; }
        }

        private static bool HasLayoutDrivenAncestorOrSelf(GameObject go, GameObject canvasRoot)
        {
            var t = go.transform;
            while (t != null)
            {
                if (HasAny(t.gameObject, ReflectionTypes.LayoutGroup)
                    || HasAny(t.gameObject, ReflectionTypes.ContentSizeFitter)
                    || HasAny(t.gameObject, ReflectionTypes.AspectRatioFitter))
                    return true;
                if (t.gameObject == canvasRoot) break;
                t = t.parent;
            }
            return false;
        }

        private static bool HasAny(GameObject go, Type type) => type != null && go.GetComponent(type) != null;

        private static int NearestCanvasSortOrder(GameObject go)
        {
            var t = go.transform;
            while (t != null)
            {
                var canvas = ReflectionTypes.Canvas != null ? t.GetComponent(ReflectionTypes.Canvas) : null;
                if (canvas != null)
                {
                    var prop = canvas.GetType().GetProperty("sortingOrder", BindingFlags.Public | BindingFlags.Instance);
                    if (prop != null)
                    {
                        try { return Convert.ToInt32(prop.GetValue(canvas)); }
                        catch { return 0; }
                    }
                }
                t = t.parent;
            }
            return 0;
        }
    }
}
