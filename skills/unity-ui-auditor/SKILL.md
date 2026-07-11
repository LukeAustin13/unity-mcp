---
name: unity-ui-auditor
description: Audit uGUI layout across a resolution matrix through MCP for Unity — analytically detects off-screen elements, overlapping interactive controls, too-small touch targets, clipped text, invisible-but-interactable and blocked controls, suspicious anchors, CanvasScaler misconfiguration, listener-less buttons, and missing EventSystem/GraphicRaycaster. Use when asked "check the UI", "does the menu work at 21:9 / different resolutions?", "why can't I click this button?", or before shipping UI changes. Read-only and screenshot-free — pure math, works in read_only mode, never dirties the scene.
license: MIT
---

# Unity UI Auditor

`audit_ui_layout` simulates uGUI layout **by math** — CanvasScaler scale factors and RectTransform screen rects computed from anchors/pivot/offsets/scale — at each resolution in a matrix. No GameView switching, no play mode, no screenshots, nothing dirtied: it is READ-classified and safe in `read_only` mode. Facts first; pixels only if a human asks for them.

## Running the audit

```python
audit_ui_layout()   # default matrix: 1920x1080, 1280x720, 2560x1440, 3440x1440
```

Tune when the target platforms differ:

```python
audit_ui_layout(
    resolutions=["1920x1080", "3840x2160", "1280x800"],  # strict WxH, max 8
    scene="MainMenu",          # must already be LOADED — the tool never opens scenes
    include_inactive=False,
    min_target_px=44,          # touch-target floor (44 ≈ platform HIG minimum)
    max_findings=500,
)
```

Pick resolutions from the game's actual support matrix: add an ultrawide (`3440x1440`) and the smallest supported size — those two break the most UI. Steam-deck-ish `1280x800` catches 16:10 issues.

## Reading the findings

Each finding: `{check, severity, advisory, scene, object_path, resolution, screen_rect, reason, suggested_fix}`. Structural checks appear once; per-resolution checks name the resolution that breaks.

| Check | Kind | Meaning |
|---|---|---|
| `missing_event_system` | structural | Interactive UI exists but no EventSystem — nothing is clickable. |
| `missing_graphic_raycaster` | structural | Canvas with interactive children but no raycaster — its UI is dead. |
| `invisible_interactable` | structural | Interactable but invisible (alpha 0 / disabled graphic) — a ghost hitbox. |
| `missing_listeners` | structural, *advisory* | Button/Toggle with no persistent listener — may be wired at runtime. |
| `suspicious_anchors` | structural, *advisory* | Large element pinned to a single anchor point — resizes badly. |
| `canvas_scaler` | structural, *advisory* | Scaler misconfiguration (e.g. ConstantPixelSize on a scaling game). |
| `off_screen` | per-resolution | Element partially/fully outside the screen at that resolution. |
| `overlapping_interactive` | per-resolution | Two interactive controls' rects overlap — mis-tap risk. |
| `tiny_target` | per-resolution | Interactive rect below `min_target_px` after scaling. |
| `clipped_text` | per-resolution, *advisory* | Text likely truncated by its rect at that size. |
| `blocked_interactable` | per-resolution, *advisory* | A control likely covered by another raycast target (draw-order approximation). |

**Advisory means "verify, don't assert."** The math cannot see LayoutGroup/ContentSizeFitter-driven sizes at runtime, ConstantPhysicalSize DPI, or true raycast order — those findings are flagged instead of dropped. Report them as "worth checking", and check `caveats` in the result: WorldSpace canvases are skipped for geometry, and unresolvable uGUI/TMP types degrade to an explicit "check skipped", never a silent pass.

## Triage order

1. **Errors that kill input everywhere**: `missing_event_system`, `missing_graphic_raycaster`, `invisible_interactable`.
2. **Per-resolution errors**: `off_screen` and `overlapping_interactive` at supported resolutions — group by `object_path`; one bad anchor usually produces findings at several resolutions, which is one fix, not five bugs.
3. **`tiny_target`** on touch platforms (rank lower for mouse-only games).
4. **Advisories**, ordered by how many resolutions they recur at.

Use `summary.by_check` / `by_severity` for the headline and note `truncated` — a truncated audit is partial coverage, say so.

## Escalation and fixes

- Only if findings are ambiguous **and the user wants visual confirmation** (needs `review_only`+): `manage_camera(action="screenshot", include_image=True, max_resolution=512)`. The audit itself never needs pixels.
- Fixes (re-anchoring, resizing, adding an EventSystem) are `write`-mode work. Each finding carries a `suggested_fix` — propose them grouped per root cause, and after applying any fix, **re-run the audit** to confirm the finding disappeared and no new one appeared.
