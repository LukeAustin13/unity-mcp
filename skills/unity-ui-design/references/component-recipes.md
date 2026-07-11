# Component Recipes

Concrete, ready-to-apply patterns for the components every game has. Each recipe is opinionated — it picks one good way, not a list of alternatives. Recipes use uGUI by default; UI Toolkit variants are noted where the approach diverges.

For the exact MCP tool-call shapes (batch_execute structures, parameter names), see `mcp-cookbook.md`.

---

## Index

1. [Primary Button](#1-primary-button)
2. [Modal / Popup](#2-modal--popup)
3. [Tab strip with switching content](#3-tab-strip-with-switching-content)
4. [Settings screen](#4-settings-screen)
5. [HUD cluster](#5-hud-cluster)
6. [Inventory grid](#6-inventory-grid)
7. [Dialogue box](#7-dialogue-box)
8. [Toast / notification](#8-toast--notification)
9. [Loading screen](#9-loading-screen)
10. [Pause menu](#10-pause-menu)

---

## 1. Primary Button

**Atom.** The most-reused component in the project. Get this right; everything builds on it.

**Anatomy:**
```
Button_Primary (prefab)
├── RectTransform              minHeight: 48 (mobile) / 40 (desktop)
├── Image                       sliced sprite, color: tokens.primary
├── Button                      colors block populated from tokens
├── UIThemeApplier              role=Primary
└── Label
    ├── RectTransform           anchor: stretch (4,4,4,4 padding)
    └── TMP_Text                font: tokens.fontBody, size: tokens.fontSizeBody,
                                color: tokens.onSurface, alignment: center,
                                raycastTarget: false
```

**Required states:** default, hover (desktop), pressed, disabled, focused (gamepad/keyboard).

**Variants** (prefab variants, not duplicates):
- `Button_Secondary` — overrides Image color to `tokens.surfaceElevated`, Label color to `tokens.onSurface`.
- `Button_Destructive` — overrides Image color to `tokens.error`.
- `Button_Ghost` — overrides Image alpha to 0, adds border via `Outline` component or sliced ring sprite.
- `Button_Icon` — square aspect, smaller padding, no label, child Image as glyph.

**Press feedback:**
```csharp
// Attach to the prefab base.
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

public class ButtonPress : MonoBehaviour, IPointerDownHandler, IPointerUpHandler
{
    [SerializeField] float pressedScale = 0.96f;
    [SerializeField] float duration = 0.08f;
    RectTransform _rt;
    Vector3 _baseScale;

    void Awake() { _rt = (RectTransform)transform; _baseScale = _rt.localScale; }
    public void OnPointerDown(PointerEventData _) => _rt.localScale = _baseScale * pressedScale;
    public void OnPointerUp  (PointerEventData _) => _rt.localScale = _baseScale;
}
```

(For DOTween projects: tween scale with `easeOutQuad` instead of snap.)

**UI Toolkit equivalent:** `<ui:Button class="btn-primary">Label</ui:Button>`. States via `:hover`, `:active`, `:disabled`, `:focus` pseudo-classes in USS. Press scale via `transition: scale 80ms ease-out` + `:active { scale: 0.96; }`.

---

## 2. Modal / Popup

**Organism.** Centred content with a dimmed scrim behind that blocks underlying input.

**Anatomy:**
```
Modal_Generic (prefab)
└── Canvas (sub-canvas, sortingOrder: 100)
    ├── Scrim                       full-stretch Image, color: (0,0,0,0.5), RaycastTarget: true
    └── Panel                       anchor: centre, fixed max-width 480, padding from tokens.space6
        ├── Header
        │   ├── Title               TMP_Text, fontSizeTitle
        │   └── CloseButton         Button_Icon variant
        ├── Body                    flexible content slot (set via code or child injection)
        └── Footer
            ├── Spacer              flexible
            ├── Cancel              Button_Secondary
            └── Confirm             Button_Primary
```

**Rules:**
- Scrim is on the same sub-canvas as the panel, both higher sortingOrder than the underlying screen.
- Scrim must have `RaycastTarget = true` so clicks-outside don't leak to underlying buttons.
- Close button and clicking the scrim both dismiss; explicit Confirm/Cancel never auto-dismiss.
- Enter animation: scrim fades from 0 → 0.5 over 200ms; panel scales from 0.96 → 1.0 + fades in over 200ms. Pivot on panel = (0.5, 0.5).
- Exit reverses with `durationFast` (snappier).

**Controller skeleton:**
```csharp
public class Modal : MonoBehaviour
{
    [SerializeField] Button closeBtn, cancelBtn, confirmBtn;
    public System.Action OnConfirm, OnCancel;

    void OnEnable()
    {
        closeBtn .onClick.AddListener(Dismiss);
        cancelBtn.onClick.AddListener(() => { OnCancel?.Invoke(); Dismiss(); });
        confirmBtn.onClick.AddListener(() => { OnConfirm?.Invoke(); Dismiss(); });
    }
    void OnDisable() { closeBtn.onClick.RemoveAllListeners(); /* ... */ }
    void Dismiss() => gameObject.SetActive(false);
}
```

---

## 3. Tab strip with switching content

**Molecule.** Horizontal row of tab buttons; content panel swapped on selection.

**Anatomy:**
```
TabbedView (prefab)
├── TabStrip                    HorizontalLayoutGroup, spacing: tokens.space3
│   ├── Tab_Audio               Button + child Indicator (2px line at bottom)
│   ├── Tab_Graphics
│   └── Tab_Controls
└── ContentArea
    ├── Content_Audio           (initially active)
    ├── Content_Graphics        (initially inactive)
    └── Content_Controls        (initially inactive)
```

**Controller:**
```csharp
public class TabSwitcher : MonoBehaviour
{
    [System.Serializable] public class Tab { public Button button; public GameObject content; public GameObject indicator; }
    [SerializeField] Tab[] tabs;
    [SerializeField] int initialIndex = 0;

    void Awake()
    {
        for (int i = 0; i < tabs.Length; i++)
        {
            int idx = i; // capture
            tabs[i].button.onClick.AddListener(() => Select(idx));
        }
        Select(initialIndex);
    }

    void Select(int index)
    {
        for (int i = 0; i < tabs.Length; i++)
        {
            bool active = i == index;
            tabs[i].content.SetActive(active);
            tabs[i].indicator.SetActive(active);
            // optionally toggle text colour via UIThemeApplier swap
        }
    }
}
```

**Don't** instantiate / destroy content panels on tab change — just toggle SetActive. Destroying is more expensive and loses scroll/input state.

---

## 4. Settings screen

**Screen.** Composition of header, tabs, and form fields.

**Composition:**
```
SettingsScreen (prefab, root)
└── Canvas
    ├── StaticCanvas
    │   ├── Backdrop (full-screen scrim or solid)
    │   └── PanelFrame
    └── DynamicCanvas
        ├── Header (Title + CloseButton)
        ├── TabbedView (recipe #3)
        │   └── ContentArea
        │       ├── Content_Audio
        │       │   └── ScrollView
        │       │       ├── FormField_Slider     (Master volume)
        │       │       ├── FormField_Slider     (Music)
        │       │       ├── FormField_Slider     (SFX)
        │       │       └── FormField_Toggle     (Subtitles)
        │       ├── Content_Graphics
        │       │   └── ScrollView
        │       │       ├── FormField_Dropdown   (Quality preset)
        │       │       ├── FormField_Toggle     (V-Sync)
        │       │       └── FormField_Slider     (FPS cap)
        │       └── Content_Controls
        │           └── ScrollView
        │               └── (key rebind rows)
        └── Footer
            ├── Reset (Button_Secondary)
            └── Apply (Button_Primary)
```

**Form field atoms** to define first (as prefabs):
- `FormField_Slider` — Label (left, 40% width) + Slider (right, 50%) + Value readout (right edge).
- `FormField_Toggle` — Label (left) + Toggle (right, larger thumb on mobile).
- `FormField_Dropdown` — Label (left) + TMP_Dropdown (right).

All form fields share the same row height (token: `space7` = 32px or `space8` = 48px on mobile).

**Persistence:** use `PlayerPrefs` for solo settings (simple), or a `SettingsRepository` ScriptableObject for production. Never write to settings directly from the UI — go through a repository so other systems can react.

---

## 5. HUD cluster

**Screen overlay.** Persistent runtime UI: health, ammo, score, minimap, objective marker.

**Critical pattern: split static and dynamic.**

```
HUD (prefab, top-level)
└── Canvas (Screen Space - Overlay)
    ├── StaticCanvas (Canvas sub, sortingOrder: 0)
    │   ├── HealthFrame             (decorative chrome around the bar)
    │   ├── AmmoFrame
    │   └── MinimapFrame
    └── DynamicCanvas (Canvas sub, sortingOrder: 1)
        ├── HealthBar (fillable Image, type=Filled, fillMethod=Horizontal)
        ├── HealthLabel (TMP_Text)
        ├── AmmoLabel  (TMP_Text)
        ├── ScoreLabel (TMP_Text)
        └── ObjectiveMarker (anchored Image, world-to-screen positioned)
```

**Updating values without GC:**
```csharp
// BAD: allocates a string per frame
healthLabel.text = $"{currentHealth}/{maxHealth}";

// GOOD: cached + StringBuilder, or only update on change
if (currentHealth != _lastHealth)
{
    _sb.Clear().Append(currentHealth).Append('/').Append(maxHealth);
    healthLabel.SetText(_sb);
    _lastHealth = currentHealth;
}
```

**Anchor pattern for HUD elements:** corner-anchor every element to its natural corner.
- Health bar: top-left (anchor (0,1) (0,1))
- Ammo: bottom-right (anchor (1,0) (1,0))
- Score: top-centre (anchor (0.5,1) (0.5,1))
- Minimap: top-right (anchor (1,1) (1,1))

Corner-anchoring + safe-area handling = HUD survives every aspect ratio without code changes.

---

## 6. Inventory grid

**Organism with virtualisation.**

For ≤30 slots: a `GridLayoutGroup` is fine. For >30: virtualise.

**Small grid (≤30):**
```
InventoryGrid (prefab)
└── ScrollView (vertical)
    └── Content
        └── GridLayoutGroup (cellSize: 96×96, spacing: 8, constraint: FixedColumnCount)
            └── (InventorySlot prefab × N at runtime)
```

**Large grid (>30) — virtualised:**
- Use UI Toolkit's `ListView` with grid mode (preferred).
- Or uGUI: Implement a manual virtualised grid — maintain a pool of N visible slot prefabs; reposition them on scroll. Don't instantiate all items.

**Slot prefab:**
```
InventorySlot (prefab)
├── Image (background, sliced, slot frame)
├── Image (icon, child, anchor centre, fixed 64×64)
├── TMP_Text (count, bottom-right corner)
└── InventorySlotView : MonoBehaviour     (Bind(InventoryItem) method)
```

**Slot drag/drop:** implement `IBeginDragHandler`, `IDragHandler`, `IEndDragHandler`. Carry a separate "ghost" element on a top-most Canvas to follow the cursor; don't move the actual slot.

---

## 7. Dialogue box

**Screen overlay (gameplay-bound).**

**Anatomy:**
```
DialogueBox (prefab)
└── Canvas (Screen Space - Overlay, sortingOrder above HUD)
    └── Panel (anchor: bottom-stretch, fixed height, margin from tokens.space5)
        ├── PortraitFrame (left, optional)
        │   └── PortraitImage
        ├── ContentArea
        │   ├── SpeakerLabel (TMP_Text, fontSizeBody, primary colour)
        │   └── DialogueText (TMP_Text, fontSizeBody, max characters per line ~50–60)
        ├── ContinueIndicator (small chevron, animated bobbing)
        └── ChoicePanel (visible only on choice nodes)
            └── (Choice buttons spawned vertically)
```

**Typewriter effect:**
```csharp
public IEnumerator TypeOut(string text, float charsPerSecond)
{
    dialogueText.text = text;
    dialogueText.maxVisibleCharacters = 0;
    int totalChars = text.Length;
    float perChar = 1f / charsPerSecond;
    for (int i = 0; i <= totalChars; i++)
    {
        dialogueText.maxVisibleCharacters = i;
        yield return new WaitForSeconds(perChar);
    }
}
```

Using `maxVisibleCharacters` (TMP-specific) is far cheaper than mutating the text every frame. It only updates the mesh's visible character count.

**Skip-to-end** on input: set `maxVisibleCharacters = totalChars`, stop the coroutine.

---

## 8. Toast / notification

**Floating transient.** Appears, lives 2–4s, fades.

**Anatomy:**
```
Toast (prefab)
└── Canvas (sortingOrder: 200, highest)
    └── Container (anchor: top-centre, dynamic Y offset for stacking)
        └── ToastEntry (one per active toast)
            ├── Background (sliced, rounded, surfaceElevated colour)
            ├── Icon (optional)
            └── Message (TMP_Text)
```

**Manager** (singleton): maintains a pool of ToastEntry prefabs, queues messages, animates in/out, handles stacking.

**Lifecycle per toast:** fade+slide in (200ms) → hold (configurable) → fade+slide out (200ms) → return to pool.

**Don't** spawn / destroy toast GameObjects each time. Pool them.

---

## 9. Loading screen

**Full-screen screen.**

**Anatomy:**
```
LoadingScreen (prefab)
└── Canvas
    ├── Background (full-stretch, brand colour or gradient)
    ├── Logo (centred, optional)
    ├── Spinner (rotating Image — set RectTransform rotation in coroutine, not Animator)
    ├── ProgressBar (optional, fill Image)
    └── TipText (rotates between tip strings every few seconds)
```

**Rules:**
- Loading screens render at fixed cost. Don't put expensive shaders here — the GPU is busy with the load.
- Use additive scene loading + `AsyncOperation.progress` to drive the bar.
- The spinner uses a coroutine setting `transform.rotation`, not an Animator (Animator has fixed cost even when the value moves slowly).

---

## 10. Pause menu

**Modal overlay over gameplay.**

**Anatomy:**
```
PauseMenu (prefab)
└── Canvas (sortingOrder: 50, above HUD, below modals)
    ├── Scrim (semi-transparent, RaycastTarget: true to block HUD interaction)
    └── Panel (anchor: centre)
        ├── Title ("Paused")
        ├── ButtonStack
        │   ├── Resume       (Button_Primary)
        │   ├── Settings     (Button_Secondary)
        │   ├── MainMenu     (Button_Secondary, with confirm dialog)
        │   └── Quit         (Button_Destructive, with confirm dialog)
        └── (Optional) Stats panel (playtime, current objective)
```

**Required behaviour:**
- `Time.timeScale = 0` on open, restore on close. Side effects: any tween system using unscaled time is fine; scaled-time tweens freeze (intended).
- Disable gameplay input map (Input System action map switching) on open.
- The Resume button is auto-selected on open for gamepad/keyboard navigation. Call `EventSystem.current.SetSelectedGameObject(resumeBtn.gameObject)`.
- Pressing Cancel / B / Escape closes — wire via Input System action with priority.

---

## Cross-recipe principles

- **Every recipe above is a prefab.** Screen-level recipes are top-level prefabs; atoms and molecules are referenced via prefab nesting and variants.
- **No recipe contains hex colours.** All colours come from the token asset via `UIThemeApplier` (uGUI) or USS variables (UI Toolkit).
- **No recipe has more than 2 nested Layout Groups.** If a design seems to need 3, restructure.
- **Every recipe produces a screenshot before being declared done.** Verify the visual against the intent.
