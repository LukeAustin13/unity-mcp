# uGUI Patterns

Load this when the target is Canvas-based UI. This covers the mental model and the recurring patterns; the MCP-specific call syntax lives in `mcp-cookbook.md`.

---

## The mental model

uGUI is a retained-mode UI built on `Canvas` → `RectTransform` → `Graphic`. Three facts you must internalise:

1. **The Canvas is a draw batcher.** Every dirty change inside a Canvas re-batches the whole Canvas. This is why static and dynamic content go on separate Canvases.
2. **RectTransform is a special Transform.** It has anchors (a 0–1 rectangle inside the parent), pivot (where the local origin sits inside this rect), and `sizeDelta` / `anchoredPosition` (which mean different things depending on anchor mode).
3. **The EventSystem routes input.** No EventSystem GameObject = no interaction. One per persistent scene.

---

## RectTransform: the anchor rules

Get this right and layouts are responsive. Get it wrong and every device size breaks.

### Anchor presets — what they actually do

| anchorMin | anchorMax | Behaviour |
|---|---|---|
| (0.5, 0.5) (0.5, 0.5) | Fixed size, positioned relative to parent centre |
| (0, 0) (1, 1) | Stretch fully to parent (sizeDelta now means margin from edges) |
| (0, 1) (1, 1) | Stretch horizontally, anchored to top — classic "top bar" |
| (0, 0) (1, 0) | Stretch horizontally, anchored to bottom — classic "bottom bar" |
| (0, 0) (0, 1) | Stretch vertically, anchored to left edge |
| (1, 0) (1, 1) | Stretch vertically, anchored to right edge |
| (1, 1) (1, 1) | Fixed size, anchored to top-right corner (e.g. close button) |

**Always set anchors before sizeDelta.** When anchors are equal (both at 0.5, 0.5), `sizeDelta` is the rect's width and height. When anchors stretch, `sizeDelta` is the offset from those anchor edges. Setting size first then changing anchor mode produces unpredictable results.

### Pivot

Pivot is where (0,0) of the local space sits inside the rect. It is also the origin of scaling and rotation. Default is (0.5, 0.5).

- Set pivot to the natural growth origin. A health bar that depletes left-to-right has pivot.x = 0. A counter that grows downward from a header has pivot.y = 1.
- Tweening scale from 0 with pivot (0.5, 0.5) grows from the centre. With (0, 1) it grows from the top-left corner. Pick deliberately.

---

## Canvas setup

### Render mode

| Mode | When to use |
|---|---|
| Screen Space - Overlay | 95% of UI. HUDs, menus, popups. Drawn last, ignores cameras. |
| Screen Space - Camera | When UI needs to be affected by post-processing or sit between 3D layers. Set a dedicated UI camera. |
| World Space | Diegetic UI (UI living in the game world: nameplates, in-world signs, VR menus). |

### CanvasScaler — the responsiveness lever

This component is the difference between "works on my dev machine" and "works on every device".

Required configuration:

| Setting | Mobile portrait | Mobile landscape | Desktop |
|---|---|---|---|
| UI Scale Mode | Scale With Screen Size | Scale With Screen Size | Scale With Screen Size |
| Reference Resolution | 1080 × 1920 | 1920 × 1080 | 1920 × 1080 |
| Screen Match Mode | Match Width Or Height | Match Width Or Height | Match Width Or Height |
| Match | 1 (height) | 0 (width) | 0.5 (balanced) |

The Match value picks which dimension scales 1:1 with the reference. On a portrait phone, height is the meaningful axis (UI must fit top-to-bottom). On landscape and desktop, width is more critical. 0.5 splits the difference for ambiguous cases.

### Graphic Raycaster

Auto-added with a Canvas. The setting to remember: **Blocking Mask**. If your UI camera is configured to ignore certain layers, raycasts from this canvas will also ignore them. Misconfiguring this is a common "buttons don't click" cause.

---

## The Canvas-split pattern (perf-critical)

A single Canvas with both static and dynamic content rebuilds the whole mesh on every dynamic change. The fix is structural:

```
WorldRoot
└── UIRoot (Canvas, Screen Space - Overlay)
    ├── StaticCanvas (Canvas — overrideSorting=true, sortingOrder=0)
    │   ├── BackgroundFrame
    │   ├── HeaderArt
    │   └── DecorativeBorder
    └── DynamicCanvas (Canvas — overrideSorting=true, sortingOrder=1)
        ├── HealthBar       (updates every frame)
        ├── ScoreCounter    (updates frequently)
        └── TimerLabel      (updates every second)
```

Each sub-Canvas batches independently. The static canvas builds once and never rebuilds. The dynamic canvas absorbs all the cost — which is now isolated and cheap.

**Trade-off**: each Canvas is a separate draw call. Don't split into dozens — 2–4 sub-canvases per screen is the sweet spot.

---

## Layout Groups — use with restraint

`HorizontalLayoutGroup`, `VerticalLayoutGroup`, and `GridLayoutGroup` save authoring time. They also rebuild on every child change and stack badly when nested.

**Rules:**
- One layer of Layout Group per branch. Nesting Vertical inside Horizontal inside Vertical is a perf trap.
- Never combine `LayoutGroup` + `ContentSizeFitter` on the same chain unless you accept double rebuilds and "dirty layout" warnings.
- For static content, prefer manual sizing — set `sizeDelta` once at author time and remove the Layout Group.
- For dynamic lists, use a pooled `ScrollRect` with manual item placement, not a `VerticalLayoutGroup` with hundreds of children.

---

## ScrollRect — the standard pattern

```
ScrollContainer (RectTransform sized to the scroll viewport)
└── ScrollRect (component) + Image (with RectMask2D, NOT Mask)
    └── Viewport (RectTransform, masked)
        └── Content (RectTransform, pivot.y=1, anchor top-stretch)
            ├── Item_0
            ├── Item_1
            └── Item_N
```

Configuration:
- `ScrollRect.viewport` → Viewport
- `ScrollRect.content` → Content
- `ScrollRect.horizontal` / `vertical` per need
- `ScrollRect.movementType` = `Elastic` (mobile) or `Clamped` (desktop)
- **Use `RectMask2D` on Viewport, not `Mask`** — half the draw cost for axis-aligned clips.

For lists with >50 items, do not stack 50 prefabs into Content. Implement virtualisation (pool, reuse N visible items, reposition on scroll). See `performance.md`.

---

## EventSystem & input

A scene without an `EventSystem` GameObject has non-interactive UI. The system is silent about this — buttons just don't fire.

Create once per persistent scene:

```python
manage_gameobject(
    action="create",
    name="EventSystem",
    components_to_add=["UnityEngine.EventSystems.EventSystem",
                       "UnityEngine.InputSystem.UI.InputSystemUIInputModule"]
)
```

(If the project uses the legacy input manager, use `StandaloneInputModule` instead. If the project has both, `InputSystemUIInputModule` wins.)

For multi-scene setups, keep the EventSystem in a `Boot` or persistent scene loaded with `DontDestroyOnLoad`. Multiple EventSystems = race condition.

---

## Common component patterns

### Button — the proper anatomy

A senior-grade button is not just a Button component on an Image. It's:

```
Button_Primary (prefab variant of Button_Base)
├── RectTransform              (anchor, size from token: ≥48 pt height)
├── Image                       (background, sliced sprite, color from tokens.primary)
├── Button                      (Transition: Color Tint OR Animation; colors from tokens)
├── UIThemeApplier              (role=Primary)
└── Label (child)
    ├── TMP_Text                (text, font from tokens.fontBody, size token, color tokens.onSurface)
    └── RectTransform (anchor: full stretch with small padding)
```

Why prefab variant: `Button_Secondary`, `Button_Destructive`, `Button_Ghost` are all variants of `Button_Base`. Change the base padding, every variant inherits.

### TMP_Text — defaults that matter

- `Auto Size` off by default. It's expensive and produces inconsistent type.
- `Raycast Target` off unless the text is clickable.
- `Material Preset` from a token-aligned material asset, not the default font material.
- For HUD text that updates frequently, set `Extra Padding = true` and shadow / outline via TMP material (cheaper than child Shadow component).

### Sliced sprite — for resizable backgrounds

In sprite import settings:
- Mesh Type: **Tight** (saves overdraw)
- Border: set in the Sprite Editor for 9-slice
- On the Image component: `Image Type` = Sliced, `Pixels Per Unit Multiplier` to control border scaling

---

## Hierarchy template — a clean screen

```
SettingsScreen (prefab, top-level)
├── Canvas                       (Screen Space - Overlay)
│   ├── CanvasScaler             (Scale With Screen Size, 1080×1920, match=1)
│   └── GraphicRaycaster
├── StaticCanvas (Canvas sub)
│   ├── Backdrop                  (full-stretch Image, dim colour, RaycastTarget=true to block underlying clicks)
│   └── PanelFrame               (anchored centre, fixed max width)
│       ├── Header
│       │   ├── Title             (TMP_Text)
│       │   └── CloseButton       (Button)
│       └── Footer (optional)
└── DynamicCanvas (Canvas sub)
    └── ContentScrollView
        └── (tabs / form fields)
```

This structure compiles down to two batches at runtime, supports safe-area inset via a SafeAreaPanel under Canvas (mobile), and isolates dynamic cost.
