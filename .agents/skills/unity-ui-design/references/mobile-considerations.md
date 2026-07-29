# Mobile Considerations

This is the file to load for any UI targeting Android or iOS. The PC-equivalent UI without these adjustments will be unusable on a real device — clipped by notches, too small to tap, ignoring system UI.

---

## Safe Area — the non-negotiable

Modern phones have notches, punch-hole cameras, rounded corners, and home indicators that overlap UI. `Screen.safeArea` returns the usable rectangle.

**Drop this script on a panel under your top-level Canvas:**

```csharp
using UnityEngine;

[RequireComponent(typeof(RectTransform))]
[ExecuteAlways]
public class SafeAreaPanel : MonoBehaviour
{
    RectTransform _rt;
    Rect _lastSafeArea;
    Vector2Int _lastScreen;

    void Awake()  => _rt = (RectTransform)transform;
    void Update() => Apply();

    void Apply()
    {
        var sa = Screen.safeArea;
        var screen = new Vector2Int(Screen.width, Screen.height);
        if (sa == _lastSafeArea && screen == _lastScreen) return;

        _lastSafeArea = sa;
        _lastScreen   = screen;

        Vector2 anchorMin = sa.position;
        Vector2 anchorMax = sa.position + sa.size;
        anchorMin.x /= screen.x; anchorMin.y /= screen.y;
        anchorMax.x /= screen.x; anchorMax.y /= screen.y;

        _rt.anchorMin = anchorMin;
        _rt.anchorMax = anchorMax;
        _rt.offsetMin = Vector2.zero;
        _rt.offsetMax = Vector2.zero;
    }
}
```

**Where it goes:** as a child of the top-level Canvas, full-stretched. All other UI panels go inside it. This insets your entire UI into the safe rectangle.

**Common mistake:** applying SafeArea to every panel individually. Don't — apply it once at the root, everything else flows from there.

**Edge case:** rotating from portrait to landscape changes `Screen.safeArea`. The script's Update handles this. Don't bake safeArea values at Awake.

---

## Touch target sizes

| Platform | Minimum (per Apple / Google) | Comfortable |
|---|---|---|
| iOS | 44pt × 44pt | 48pt × 48pt |
| Android | 48dp × 48dp | 56dp × 56dp |

"pt" / "dp" are density-independent units. With a CanvasScaler set to Scale With Screen Size and reference resolution 1080×1920 on a typical phone, **1 reference pixel ≈ 1 dp**. So a 48dp target ≈ 48 reference pixels in your CanvasScaler space — *if* you've matched height (Match = 1) on portrait.

**Rules:**
- Every interactive element has a minimum hit area of 48 reference px (mobile). The visible icon can be smaller; pad the interactive area with a transparent extended bounds.
- Two adjacent touch targets must have ≥ 8 reference px of space between hit areas. Touching targets cause mis-taps.
- Top corners of the screen are uncomfortable to reach on phones >5". Push frequently-used controls to the bottom 60% of the screen.

**Extending hit area without resizing the visual:**
```csharp
// Add a transparent Image with RaycastTarget=true around an icon button.
// The Image's RectTransform is the hit area; the visual icon is a child.
```

Or use an `ExtendedHitArea` component:
```csharp
public class ExtendedHitArea : MonoBehaviour, ICanvasRaycastFilter
{
    [SerializeField] Vector2 extraPadding = new(16, 16);
    RectTransform _rt;
    void Awake() => _rt = (RectTransform)transform;

    public bool IsRaycastLocationValid(Vector2 sp, Camera eventCamera)
    {
        var expanded = _rt.rect;
        expanded.xMin -= extraPadding.x; expanded.xMax += extraPadding.x;
        expanded.yMin -= extraPadding.y; expanded.yMax += extraPadding.y;
        Vector2 local = _rt.InverseTransformPoint(sp);
        return expanded.Contains(local);
    }
}
```

Attach to an `Image` with `raycastTarget = true`. The hit zone now extends past the visual bounds without changing the layout.

---

## Thumb zones (Scott Hurff / Steven Hoober reach maps)

Hold a phone in one hand. The thumb naturally reaches the bottom half easily, the middle with effort, the top corners barely.

| Zone | One-handed reach | Use for |
|---|---|---|
| Bottom centre + bottom edges | Easy | Primary actions, tab bars, FABs |
| Middle horizontal band | Medium | Secondary controls, content |
| Top corners | Hard | System UI, status info, dismissive close buttons |

**Design implications:**
- Tab bar at bottom, not top (unlike old iOS pattern; mobile design has moved).
- Confirm / primary CTA in the bottom-third when possible.
- Close / X buttons can live top-corner — they're dismissive, lower frequency, and users will reach for them deliberately.

---

## Virtual keyboard handling

When a `TMP_InputField` is focused on mobile, the OS keyboard slides up and covers part of the screen. If the input field is in the lower half, it gets covered.

**Pattern:** listen for `TouchScreenKeyboard.visible` (or `Keyboard.current.IsVisible` on Input System) and shift content up.

```csharp
void Update()
{
    if (TouchScreenKeyboard.visible)
    {
        // Slide the content panel up by the keyboard's apparent height.
        // Approximation: keyboard ≈ 40% of screen height on phones.
        float kbHeight = Screen.height * 0.4f;
        contentPanel.anchoredPosition = new Vector2(0, kbHeight * 0.5f);
    }
    else
    {
        contentPanel.anchoredPosition = Vector2.zero;
    }
}
```

For accurate keyboard height on Android, use a native plugin or the Input System's keyboard height callback. For most cases the 40% approximation is acceptable.

---

## Orientation

Pick one of:
- **Portrait-only** — set in Player Settings. Simplest. Most casual mobile games.
- **Landscape-only** — set in Player Settings. Most "premium" mobile games.
- **Both** — requires UI that responds to orientation changes. Significantly more authoring.

If supporting both:
- Two `CanvasScaler` setups in code, swapped on `OnRectTransformDimensionsChange`.
- Anchor reflows handled per-panel.
- Test on actual hardware — Editor's orientation simulator misses real cases.

Don't ship "both" unless the game truly needs it. The cost is high.

---

## DPI awareness

`Screen.dpi` returns the device DPI. Useful for converting physical units to pixels, but unreliable on many Android devices (returns 0). Prefer the CanvasScaler reference resolution + Match approach over per-device DPI calculations.

If you must use DPI for sizing (e.g. a physical-units button "must be 1cm wide"):
```csharp
float onePixelInCm = 2.54f / Screen.dpi;   // dpi can be 0 — guard
float buttonWidthPx = 1f / onePixelInCm;
```

---

## Performance on mobile

Mobile GPUs are weaker. UI cost compounds. Add to the standard performance rules:

- **Overdraw budget is tighter.** Avoid full-screen translucent overlays unless they're the only thing on screen.
- **Atlas count matters more.** Each atlas swap is a draw call on tiled GPUs (PowerVR, Adreno) — and mobile GPUs are mostly tiled.
- **TMP_Text with shaders / glow / outline** is more expensive on mobile. Use the simplest material that achieves the look.
- **Avoid large `Mask`** — stencil ops are particularly slow on mobile tilers.
- **Cap UI frame rate**: `Application.targetFrameRate = 60` (or 30 for low-tier devices). Higher = drained battery for no visible gain on most phones.

---

## Haptic feedback hooks

Subtle vibration on button press dramatically improves perceived responsiveness — but only if used sparingly.

```csharp
// Cross-platform light tap
public static class Haptics
{
    public static void Light()
    {
#if UNITY_IOS && !UNITY_EDITOR
        // iOS impact feedback via native plugin (Lofelt Nice Vibrations or similar)
        // UnityEngine.iOS.OnDemandResources or a haptic plugin
#elif UNITY_ANDROID && !UNITY_EDITOR
        Handheld.Vibrate(); // crude — replace with proper haptic plugin for nuanced feedback
#endif
    }
}
```

For production, use a dedicated haptics package (Lofelt Nice Vibrations is the standard) — `Handheld.Vibrate()` is binary and old-fashioned.

**Hook into the primary button's `onClick`**, not every button. Over-haptic feels noisy.

---

## Notch / cutout testing checklist

Test on at least:
- **iPhone with notch** (12 / 13 / 14 / 15 — notch + home indicator)
- **iPhone with Dynamic Island** (14 Pro / 15 Pro — larger top cutout)
- **iPhone SE / older iPad** (no notch — verify SafeArea doesn't add weird padding)
- **Android punch-hole** (Pixel 6+, Galaxy S21+)
- **Android with gesture navigation** vs **navigation bar**
- **Tablet** (very different aspect ratio than phone)

The Unity Editor's Device Simulator (Window → General → Device Simulator) provides these out-of-box. Use it.

---

## Mobile-specific component adjustments

Recipes in `component-recipes.md` are baseline. Mobile-specific overrides:

- **Buttons:** minimum height 48 reference px (already in recipe).
- **Sliders:** thumb size 32–40 reference px wide (vs 16–20 on desktop).
- **Toggles:** thumb diameter 28+ reference px.
- **Dropdowns:** the open list items must be 48+ reference px tall each.
- **Text input:** font size minimum 16 reference px (iOS auto-zooms on smaller text in some webviews, but native input still benefits).
- **Modals:** width = `min(parent.width - 32, 480px)`. Don't pin to a fixed pixel width — narrow phones clip it.
