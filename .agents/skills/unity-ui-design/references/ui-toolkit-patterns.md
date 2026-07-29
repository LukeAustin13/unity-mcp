# UI Toolkit Patterns

Load this when the target is UXML/USS-based UI. UI Toolkit is Unity's newer retained-mode system, modelled on HTML/CSS. It's faster than uGUI for data-heavy and editor UI, but its runtime story (animation, world-space, complex pointer events) is still maturing. Pick it deliberately.

---

## When to choose UI Toolkit over uGUI

| Scenario | Pick |
|---|---|
| Runtime gameplay HUD with frequent updates | uGUI (better animation tooling, more middleware) |
| Settings, menus, inventory, data-heavy lists | UI Toolkit (cleaner styling, faster lists) |
| Editor tools, custom inspectors, custom windows | UI Toolkit (built for this) |
| World-space UI (in-world signage, VR menus) | uGUI (UI Toolkit world-space is limited) |
| You need DOTween / complex tweens | uGUI (UI Toolkit transitions are CSS-style only) |
| Team comes from web background | UI Toolkit (familiar mental model) |
| Project already invested in uGUI | uGUI (don't mix systems on one screen) |

---

## The trio: UXML, USS, UIDocument

- **UXML** — the layout (like HTML). Structure and content.
- **USS** — the styling (like CSS). Visuals.
- **UIDocument** — the MonoBehaviour that mounts a UXML tree onto a runtime Canvas-equivalent (the `PanelSettings`).

A screen is: one `UIDocument` GameObject + a `PanelSettings` asset + a root `.uxml` + linked `.uss` files.

---

## UXML — structural authoring

A clean UXML file uses `<ui:VisualElement>` as the base block (think `<div>`) and named UXML elements for typed widgets.

```xml
<?xml version="1.0" encoding="utf-8"?>
<ui:UXML xmlns:ui="UnityEngine.UIElements" xmlns:uie="UnityEditor.UIElements"
         editor-extension-mode="False">

    <Style src="project://database/Assets/UI/Styles/tokens.uss" />
    <Style src="project://database/Assets/UI/Styles/components.uss" />

    <ui:VisualElement name="settings-root" class="screen">

        <ui:VisualElement name="header" class="screen__header">
            <ui:Label text="Settings" class="text-heading" />
            <ui:Button name="close-btn" text="✕" class="btn-icon" />
        </ui:VisualElement>

        <ui:VisualElement name="tabs" class="tab-strip">
            <ui:Button name="tab-audio"    text="Audio"    class="tab tab--active" />
            <ui:Button name="tab-graphics" text="Graphics" class="tab" />
            <ui:Button name="tab-controls" text="Controls" class="tab" />
        </ui:VisualElement>

        <ui:VisualElement name="content" class="screen__content">
            <ui:ScrollView mode="Vertical" class="scroll">
                <!-- tab content swapped at runtime -->
            </ui:ScrollView>
        </ui:VisualElement>

    </ui:VisualElement>
</ui:UXML>
```

**Rules:**
- Names (`name="..."`) are for runtime queries (`root.Q<Button>("close-btn")`). Use kebab-case.
- Classes (`class="..."`) are for styling. Use BEM-ish: `block`, `block__element`, `block--modifier`.
- Never style inline (`style="..."`) — always via classes. Inline style is the equivalent of a hex literal in uGUI.

---

## USS — styling

USS is CSS without selectors-beyond-class, IDs, and pseudo-classes (`:hover`, `:active`, `:focus`, `:disabled`, `:checked`). No descendant selectors (`.a .b`) — only direct (`.a > .b`). No `!important`. Limited units (`px`, `%`, `em`-equivalent via percent).

Pattern: a `tokens.uss` defines `:root` variables; a `components.uss` defines reusable component classes that consume those variables.

```css
/* components.uss */

.screen {
    flex-direction: column;
    flex-grow: 1;
    background-color: var(--color-surface);
    padding: var(--space-5);
}

.screen__header {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
    margin-bottom: var(--space-5);
}

.text-heading {
    color: var(--color-on-surface);
    font-size: var(--font-size-heading);
    -unity-font-style: bold;
}

.tab-strip {
    flex-direction: row;
    border-bottom-width: 1px;
    border-bottom-color: var(--color-surface-elevated);
    margin-bottom: var(--space-5);
}

.tab {
    background-color: rgba(0, 0, 0, 0);
    color: var(--color-on-surface-muted);
    padding: var(--space-3) var(--space-5);
    border-width: 0;
    border-bottom-width: 2px;
    border-bottom-color: rgba(0, 0, 0, 0);
    transition: color var(--duration-fast) ease-out,
                border-bottom-color var(--duration-fast) ease-out;
}
.tab:hover  { color: var(--color-on-surface); }
.tab--active {
    color: var(--color-on-surface);
    border-bottom-color: var(--color-primary);
}

.btn-icon {
    width: 36px;
    height: 36px;
    background-color: rgba(0, 0, 0, 0);
    color: var(--color-on-surface-muted);
    border-width: 0;
    border-radius: var(--radius-pill);
    transition: background-color var(--duration-fast) ease-out;
}
.btn-icon:hover { background-color: var(--color-surface-elevated); }
```

---

## UIDocument — the runtime bridge

A screen needs:

1. A `PanelSettings` asset (`Assets/UI/PanelSettings_Main.asset`) configured for the target screen (reference resolution, scale mode, sort order).
2. A scene `GameObject` with a `UIDocument` component pointing at the panel settings + source `.uxml`.
3. A C# controller that queries elements and wires events.

Controller pattern:

```csharp
using UnityEngine;
using UnityEngine.UIElements;

namespace Project.UI
{
    [RequireComponent(typeof(UIDocument))]
    public class SettingsScreen : MonoBehaviour
    {
        Button _closeBtn;
        Button _tabAudio, _tabGraphics, _tabControls;
        VisualElement _content;

        void OnEnable()
        {
            var root = GetComponent<UIDocument>().rootVisualElement;

            _closeBtn     = root.Q<Button>("close-btn");
            _tabAudio     = root.Q<Button>("tab-audio");
            _tabGraphics  = root.Q<Button>("tab-graphics");
            _tabControls  = root.Q<Button>("tab-controls");
            _content      = root.Q<VisualElement>("content");

            _closeBtn.clicked    += Close;
            _tabAudio.clicked    += () => SelectTab(_tabAudio,    "audio");
            _tabGraphics.clicked += () => SelectTab(_tabGraphics, "graphics");
            _tabControls.clicked += () => SelectTab(_tabControls, "controls");
        }

        void OnDisable()
        {
            _closeBtn.clicked    -= Close;
            // unsubscribe lambdas requires storing them; for brevity not shown
        }

        void SelectTab(Button activated, string key)
        {
            foreach (var t in new[] { _tabAudio, _tabGraphics, _tabControls })
                t.RemoveFromClassList("tab--active");
            activated.AddToClassList("tab--active");
            // swap content
        }

        void Close() => gameObject.SetActive(false);
    }
}
```

---

## PanelSettings — the responsiveness lever

This is UI Toolkit's equivalent of `CanvasScaler`. Configure on the asset:

| Setting | Mobile portrait | Mobile landscape | Desktop |
|---|---|---|---|
| Scale Mode | Scale With Screen Size | Scale With Screen Size | Scale With Screen Size |
| Reference Resolution | 1080 × 1920 | 1920 × 1080 | 1920 × 1080 |
| Screen Match Mode | Match Width Or Height | Match Width Or Height | Match Width Or Height |
| Match | 1 (height) | 0 (width) | 0.5 |

Same logic as CanvasScaler — height-match for portrait, width-match for landscape, balanced for ambiguous.

---

## Lists at scale — the ListView pattern

UI Toolkit's `ListView` is virtualised by default. For lists ≥20 items this is the right choice over a ScrollView with stacked children.

```csharp
var list = root.Q<ListView>("inventory-list");
list.itemsSource = inventoryItems;          // your IList<T>
list.fixedItemHeight = 64;
list.makeItem  = () => itemTemplate.Instantiate();   // VisualTreeAsset
list.bindItem  = (element, i) => {
    element.Q<Label>("name").text  = inventoryItems[i].Name;
    element.Q<Label>("count").text = inventoryItems[i].Count.ToString();
};
list.Rebuild();
```

UI Toolkit handles the pool. Don't reimplement virtualisation.

---

## Animation in UI Toolkit

USS transitions cover most needs (hover, press, enter, exit). For complex sequences, use `schedule.Execute(...).Every(...)` for time-based callbacks or write directly to `style` properties from C#.

Do not reach for DOTween in UI Toolkit — its hooks are not designed for VisualElement. If a complex tween is needed, animate via C# coroutine setting `style.opacity`, `style.translate`, etc.

---

## Things UI Toolkit does badly (know the limits)

- **World-space UI**: not first-class. For VR menus, signage, nameplates — use uGUI.
- **Multi-camera composition**: a UIDocument renders to one panel; layering across cameras needs careful PanelSettings sort order management.
- **Custom shaders on UI elements**: limited to built-in tinting; for complex effects use a `MeshGenerationContext` callback (advanced).
- **Animator integration**: UI Toolkit elements aren't GameObjects; the Animator timeline doesn't reach them. Animate from code.
- **Pointer events through transparent areas**: requires `pickingMode = PickingMode.Ignore` on transparent overlays; default is `Position` which absorbs clicks.

---

## Project layout for UI Toolkit

```
Assets/UI/
├── Styles/
│   ├── tokens.uss               (:root variables)
│   ├── components.uss           (reusable component classes)
│   └── screens.uss              (screen-specific overrides, sparingly)
├── Screens/
│   ├── SettingsScreen.uxml
│   ├── SettingsScreen.cs
│   └── ...
├── Components/
│   ├── ListItem.uxml            (VisualTreeAsset templates)
│   └── ...
└── PanelSettings/
    ├── PanelSettings_Main.asset
    └── PanelSettings_World.asset (if any world-space)
```

Tokens load first, components next, screen-specific last. Cascade order matters in USS.
