# Performance

UI is where game performance silently dies. A Canvas rebuild is invisible in the profiler unless you look for it, and there's almost always a Canvas rebuild happening when there shouldn't be. This file is the playbook.

---

## The single most important rule

**Split static and dynamic content into separate Canvases.**

A `Canvas` rebuilds its entire batched mesh whenever any child `Graphic` becomes dirty (text changes, image colour changes, RectTransform moves, child added/removed). On a 100-element HUD with one health-bar update per frame, that's the cost of rebuilding all 100 elements per frame.

Fix: nested sub-Canvases (each child `Canvas` is its own batch root). Put unchanging elements on one sub-Canvas, frequently-updating elements on another. The cost on the static one drops to zero.

Trade-off: each Canvas is at least one draw call. 2–4 sub-canvases per screen is right. 10+ is overcorrecting.

**How to measure:** Profiler → UI Details (Window → Analysis → Profiler → UI Details). The `Canvas.SendWillRenderCanvases` and `Canvas.BuildBatch` rows are the rebuild cost. If they show meaningful time on a static-looking screen, your batching is wrong.

---

## RaycastTarget — the free win

Every `Graphic` with `RaycastTarget = true` is hit-tested against pointer events. The default is `true`. On a typical screen, 80% of those raycasts hit non-interactive elements (backgrounds, decorative frames, text labels).

**Rule:** `RaycastTarget = true` only on elements that handle input. Buttons, Toggles, Sliders, scrim overlays that intentionally block clicks-through. Everything else is `false`.

Audit pass: enumerate every `Image` and `TMP_Text` in a screen, switch off raycast on the decoratives. Single biggest free perf win in Unity UI.

```csharp
// Editor script to bulk-disable raycasts on non-interactive Graphics under a root.
[MenuItem("Tools/UI/Disable Decorative Raycasts")]
public static void DisableDecorativeRaycasts()
{
    foreach (var g in Selection.activeGameObject.GetComponentsInChildren<Graphic>(true))
    {
        bool isInteractive = g.GetComponent<Selectable>() != null
                          || g.GetComponentInParent<Selectable>() != null;
        if (!isInteractive && g.raycastTarget)
        {
            Undo.RecordObject(g, "Disable Raycast");
            g.raycastTarget = false;
        }
    }
}
```

---

## Mask vs RectMask2D

Both clip child rendering. They cost very different amounts.

| Mask | RectMask2D |
|---|---|
| Stencil-buffer based | Software rect clipping in the vertex shader |
| Works with arbitrary shapes (uses a Graphic as the mask) | Axis-aligned rectangles only |
| 2 extra draw calls (mask on, mask off) | No extra draw calls |
| Use for round masks, irregular shapes | Use for ScrollViews, list viewports, almost everything |

**Default to `RectMask2D`.** Reach for `Mask` only when you genuinely need a non-rectangular clip.

---

## Layout Group cost

`HorizontalLayoutGroup`, `VerticalLayoutGroup`, `GridLayoutGroup` walk children, measure, and reposition on every dirty event. Combined with `ContentSizeFitter` (which marks the layout dirty when sizes change), you can get into double-rebuild loops.

**Rules:**
- One Layout Group level per branch. Nesting Vertical → Horizontal → Vertical is a trap.
- Avoid `LayoutGroup + ContentSizeFitter` on the same chain when content is dynamic. Manually compute size or use a custom layout instead.
- For static layouts (a settings panel built once and never resized), Layout Groups are fine — they pay their cost once.
- For dynamic lists (chat log, leaderboard), do not stack hundreds of children under a `VerticalLayoutGroup`. Use virtualisation.

---

## Virtualisation — the right way to do long lists

A ScrollView with 500 children all instantiated is a memory hog, a Canvas-rebuild hog, and a startup hit.

**uGUI virtualisation pattern:**
- Maintain a pool of N visible item prefabs (N = visible items + 2 buffer).
- On scroll, reposition the top-most item to the bottom (or vice versa) and rebind its data.
- `Content` `RectTransform.sizeDelta.y` = totalItems * itemHeight (so the scrollbar shows correct extent).
- Use a third-party package (`OptimizedScrollView`, `EnhancedScroller`, `RecycleListView`) — virtualisation is hard to write correctly. Don't reinvent it for ad-hoc lists.

**UI Toolkit:** use `ListView`. It virtualises by default. Set `fixedItemHeight` for the cheapest path.

---

## Atlasing & overdraw

Every UI Image is one or more draw calls unless batched. Batching requires sharing a texture. Hence: sprite atlases.

**Sprite Atlas setup:**
- Group sprites by screen: one atlas per major screen (`HUD`, `Settings`, `Inventory`), not one atlas per project. Cross-atlas batching can break draw call merging.
- `Include in Build` on, `Allow Rotation` off (rotation breaks 9-slice).
- `Tight Packing` on for irregular shapes.
- `Read/Write` off unless you genuinely need CPU access.

**Sprite import settings (per source sprite):**
- `Mesh Type = Tight` — saves overdraw on transparent edges. Critical for shaped sprites (e.g. icon glyphs on transparent backgrounds).
- `Generate Physics Shape` off for UI sprites.

**Overdraw measurement:** Scene view → top-left dropdown → Overdraw mode. Bright red means many overlapping transparent fragments — the GPU is paying for each layer. Common offenders: stacked semi-transparent panels, decorative gradients over full-screen art, full-screen fade overlays that linger after fade.

---

## Text-heavy UI

TMP_Text is meshed once per text change. A chat log appending one line per second triggers a mesh rebuild for the appended element. Not the whole canvas — just the changed text — but it's still cost.

Optimisations:
- For frequently-changing labels (score, timer, FPS counter), use `TMP_Text.SetText(StringBuilder)` overload. Avoid `text = $"..."` which allocates.
- For very long static text (lore, credits), set `Auto Size` off (it's expensive) and pre-size manually.
- Avoid `Outline`, `Shadow` child components on TMP_Text — use TMP's built-in material outline/glow instead (cheaper, configured per material preset).
- Drop shadow on TMP — use the material's `Underlay` feature, not a duplicated `Shadow` text instance.

---

## Animator on UI — almost always wrong

`Animator` has continuous evaluation cost even when its values aren't changing meaningfully. It also serialises state into the prefab in ways that complicate prefab variants.

**Use Animator only when:** the animation needs to integrate with Timeline / cutscenes / state-machine logic external to the UI.

**Otherwise use:**
- DOTween / LitMotion / PrimeTween — for property tweens.
- Coroutines for one-shot interpolations.
- USS transitions (UI Toolkit) for hover/press/state changes.

**Kill tweens on disable:**
```csharp
void OnDisable()
{
    DOTween.Kill(this);   // or store and kill specific tweens
}
```

A tween targeting a destroyed transform throws null-refs. A tween still running when its panel is re-shown causes "snap" visual bugs.

---

## Canvas Render Mode and Camera

- `Screen Space - Overlay` skips the camera pipeline entirely. Cheapest. Default to this for HUDs and menus.
- `Screen Space - Camera` puts UI in the camera's render path — gets post-processing, depth-sorted with 3D. More expensive. Use only when the visual demands it (UI behind transparent 3D, post-FX on UI).
- `World Space` is for diegetic UI. Each world-space Canvas needs its own draw work and is sorted in the 3D scene.

---

## EventSystem & Input System cost

The `EventSystem` polls input modules every frame. The cost is bounded but real. Two pitfalls:

- **Multiple EventSystems** loaded from additive scenes — input gets routed nondeterministically. Keep exactly one persistent EventSystem in a boot scene.
- **GraphicRaycaster `Ignore Reversed Graphics`** off — raycasts hit graphics facing away from the camera (irrelevant for screen-space; matters for world-space). Leave on for screen-space, off for world-space.

---

## The audit checklist

When asked to perf-audit a UI screen, walk this in order:

1. **Open Profiler → UI Details** with the screen running.
2. Look at `Canvas.BuildBatch` time. If high on a visually-static screen → split static/dynamic.
3. Count `Canvas` components — should be 2–4 per screen, not 1 (no split) and not 12 (over-split).
4. Enumerate `Graphic` components with `raycastTarget == true`. Disable on every non-interactive one.
5. Count `Mask` components — replace with `RectMask2D` unless the clip is non-rectangular.
6. Look for nested `LayoutGroup` chains — if 3+ deep, restructure.
7. Look for `LayoutGroup + ContentSizeFitter` on the same chain — flag for refactor.
8. Look for `Animator` on UI prefabs — flag for tween replacement.
9. Open Frame Debugger — count draw calls on the screen. Look for atlas misses (sprites that should batch but don't).
10. Scene view Overdraw mode — flag bright-red zones.

Report findings as a numbered list with severity, file/prefab path, and a one-line fix. Don't fix automatically without consent unless explicitly told to.
