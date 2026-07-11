# Design System & Tokens

A design system is not optional. Without one, every screen becomes a one-off and the project's UI collapses into inconsistency by the third menu. This file defines how to bootstrap and maintain tokens for both uGUI and UI Toolkit projects.

---

## Token categories

Every design system has at minimum these five categories. Don't ship without all of them.

| Category | Purpose | Example values |
|---|---|---|
| **Colour** | Surface, on-surface, primary, secondary, accent, success, warning, error, plus per-state variants (hover, pressed, disabled) | `surface = #1A1A1F`, `onSurface = #EDEDF2`, `primary = #5B8DEF`, `error = #E5484D` |
| **Typography** | Font family, weights, size scale, line-height, letter-spacing | Sizes: 12 / 14 / 16 / 20 / 24 / 32 (1.250 ratio). Weights: 400, 500, 700 |
| **Spacing** | Padding, gap, margin — always from a fixed scale | 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 |
| **Radius** | Corner radius scale | 4 / 8 / 12 / 9999 (pill) |
| **Elevation / motion** | Shadow / outline depths, animation durations & easing | Durations: 120ms / 200ms / 320ms. Easing: easeOutCubic (enter), easeInCubic (exit) |

---

## uGUI implementation — ScriptableObject tokens

uGUI has no native token system. Create one as a ScriptableObject.

### Step 1 — Create the token asset class

Use `create_script` to author `Assets/UI/DesignSystem/DesignTokens.cs`:

```csharp
using UnityEngine;
using TMPro;

namespace Project.UI
{
    [CreateAssetMenu(fileName = "DesignTokens", menuName = "Project/UI/Design Tokens")]
    public class DesignTokens : ScriptableObject
    {
        [Header("Colour")]
        public Color surface           = new(0.102f, 0.102f, 0.122f, 1f);
        public Color surfaceElevated   = new(0.149f, 0.149f, 0.172f, 1f);
        public Color onSurface         = new(0.929f, 0.929f, 0.949f, 1f);
        public Color onSurfaceMuted    = new(0.620f, 0.624f, 0.671f, 1f);
        public Color primary           = new(0.357f, 0.553f, 0.937f, 1f);
        public Color primaryHover      = new(0.439f, 0.624f, 0.965f, 1f);
        public Color primaryPressed    = new(0.290f, 0.471f, 0.847f, 1f);
        public Color primaryDisabled   = new(0.357f, 0.553f, 0.937f, 0.4f);
        public Color success           = new(0.290f, 0.792f, 0.490f, 1f);
        public Color warning           = new(0.969f, 0.694f, 0.227f, 1f);
        public Color error             = new(0.898f, 0.282f, 0.298f, 1f);

        [Header("Typography")]
        public TMP_FontAsset fontDisplay;
        public TMP_FontAsset fontBody;
        public float fontSizeCaption   = 12f;
        public float fontSizeBodySmall = 14f;
        public float fontSizeBody      = 16f;
        public float fontSizeTitle     = 20f;
        public float fontSizeHeading   = 24f;
        public float fontSizeDisplay   = 32f;

        [Header("Spacing (px)")]
        public float space2  = 4f;
        public float space3  = 8f;
        public float space4  = 12f;
        public float space5  = 16f;
        public float space6  = 24f;
        public float space7  = 32f;
        public float space8  = 48f;
        public float space9  = 64f;

        [Header("Radius (px)")]
        public float radiusSmall  = 4f;
        public float radiusMedium = 8f;
        public float radiusLarge  = 12f;
        public float radiusPill   = 9999f;

        [Header("Motion")]
        public float durationFast    = 0.12f;
        public float durationNormal  = 0.20f;
        public float durationSlow    = 0.32f;
    }
}
```

### Step 2 — Create the asset

`create_script` auto-compiles — **do not** call `refresh_unity`. Wait until `mcpforunity://editor/state` reports `compilation.is_compiling == false`, then `read_console(action="get", types=["error"])` to confirm the `DesignTokens` type compiled. Only then can you instantiate an asset of it.

ScriptableObject assets are created with `manage_scriptable_object` (not `manage_asset` — that tool has no `script_class`/`ScriptableObject` create path). This tool is in the `scripting_ext` group, disabled by default, so activate it first:

```python
manage_tools(action="activate", group="scripting_ext")

manage_scriptable_object(
    action="create",
    type_name="Project.UI.DesignTokens",          # namespace-qualified SO type
    folder_path="Assets/UI/DesignSystem",
    asset_name="DesignTokens"                      # no extension; .asset is added
)
```

Set field values with `action="modify"` (`target={"path": "Assets/UI/DesignSystem/DesignTokens.asset"}`, `patches=[...]`) — but for tokens, prefer setting sensible defaults in the C# field initialisers above so the fresh asset is already correct.

### Step 3 — Token consumer pattern

Components consume tokens via a small `UIThemeApplier` MonoBehaviour, not by direct hex values:

```csharp
using UnityEngine;
using UnityEngine.UI;
using TMPro;

namespace Project.UI
{
    public enum ColourRole { Surface, SurfaceElevated, OnSurface, OnSurfaceMuted, Primary, Success, Warning, Error }

    [ExecuteAlways]
    public class UIThemeApplier : MonoBehaviour
    {
        public DesignTokens tokens;
        public ColourRole role;
        public bool applyToImage = true;
        public bool applyToText  = false;

        void OnEnable()  => Apply();
        void OnValidate() => Apply();

        void Apply()
        {
            if (!tokens) return;
            var c = Resolve(role);
            if (applyToImage && TryGetComponent(out Image img)) img.color = c;
            if (applyToText  && TryGetComponent(out TMP_Text tmp)) tmp.color = c;
        }

        Color Resolve(ColourRole r) => r switch
        {
            ColourRole.Surface         => tokens.surface,
            ColourRole.SurfaceElevated => tokens.surfaceElevated,
            ColourRole.OnSurface       => tokens.onSurface,
            ColourRole.OnSurfaceMuted  => tokens.onSurfaceMuted,
            ColourRole.Primary         => tokens.primary,
            ColourRole.Success         => tokens.success,
            ColourRole.Warning         => tokens.warning,
            ColourRole.Error           => tokens.error,
            _ => Color.magenta
        };
    }
}
```

For more complex skinning (interactive state colours on a Button), use Unity's built-in `ColorBlock` populated from tokens at edit-time via a custom inspector, or a small `ButtonTheme` MonoBehaviour that sets `Button.colors` in `OnValidate`.

---

## UI Toolkit implementation — USS custom properties

UI Toolkit has first-class token support via USS variables. Put them in a single root stylesheet imported by every UIDocument.

### `Assets/UI/Styles/tokens.uss`

```css
:root {
    /* Colour */
    --color-surface:           #1A1A1F;
    --color-surface-elevated:  #26262C;
    --color-on-surface:        #EDEDF2;
    --color-on-surface-muted:  #9EA0AB;
    --color-primary:           #5B8DEF;
    --color-primary-hover:     #709FF6;
    --color-primary-pressed:   #4978D8;
    --color-primary-disabled:  rgba(91, 141, 239, 0.4);
    --color-success:           #4ACA7D;
    --color-warning:           #F7B13A;
    --color-error:             #E5484D;

    /* Typography (consumed via -unity-font-definition + font-size) */
    --font-size-caption:    12px;
    --font-size-body-small: 14px;
    --font-size-body:       16px;
    --font-size-title:      20px;
    --font-size-heading:    24px;
    --font-size-display:    32px;

    /* Spacing */
    --space-2: 4px;
    --space-3: 8px;
    --space-4: 12px;
    --space-5: 16px;
    --space-6: 24px;
    --space-7: 32px;
    --space-8: 48px;
    --space-9: 64px;

    /* Radius */
    --radius-sm:   4px;
    --radius-md:   8px;
    --radius-lg:   12px;
    --radius-pill: 9999px;

    /* Motion (USS transitions) */
    --duration-fast:   120ms;
    --duration-normal: 200ms;
    --duration-slow:   320ms;
}
```

Consume in component stylesheets:

```css
.btn-primary {
    background-color: var(--color-primary);
    color: var(--color-on-surface);
    padding: var(--space-3) var(--space-5);
    border-radius: var(--radius-md);
    font-size: var(--font-size-body);
    transition: background-color var(--duration-fast) ease-out;
}
.btn-primary:hover  { background-color: var(--color-primary-hover); }
.btn-primary:active { background-color: var(--color-primary-pressed); }
.btn-primary:disabled { background-color: var(--color-primary-disabled); }
```

---

## Dark / light theming

**uGUI**: Create a second token asset (`DesignTokens_Light.asset`) and a `ThemeRouter` singleton that swaps the active `DesignTokens` reference and broadcasts a change event. Themed components listen and re-apply.

**UI Toolkit**: Define both palettes as classes on the root element. `.theme-dark { --color-surface: #1A1A1F; ... }` and `.theme-light { --color-surface: #F5F5F7; ... }`. Toggle the class on the `rootVisualElement`. Variables cascade automatically.

---

## What "extending an existing system" looks like

If the project already has tokens, you **add** to them; you do not duplicate. Concretely:

- Need a new colour? Add a field to the existing `DesignTokens` ScriptableObject. Do not invent `MyScreenTokens.cs`.
- Need a new spacing value that isn't on the scale? Pause and check whether the design genuinely needs it, or whether the existing scale value will do. 90% of the time the existing value works.
- Need a new font? Add to the existing typography section. Do not import a fresh font for a single screen.

If a token feels missing, that's a signal the design system needs a deliberate extension — discussed with the user — not a workaround.
