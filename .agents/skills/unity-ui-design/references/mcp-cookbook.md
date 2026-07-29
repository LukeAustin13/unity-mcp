# MCP Cookbook (Coplay MCP for Unity)

Concrete tool-call patterns for UI work. The MCP for Unity exposes ~46 tools; this file covers the dozen that matter for UI and shows the *correct* shape, not memorised-but-wrong shapes. When in doubt about a tool's exact parameter names, call `tool_search` or the project's `unity_reflect` to verify against the live schema — do not guess.

---

## The UI-relevant tool surface

| Tool | UI use |
|---|---|
| `manage_ui` | UI-specific operations (canvas setup, hierarchy, common UI elements) |
| `manage_gameobject` | Create/modify any UI GameObject; the workhorse for hierarchy |
| `manage_components` | Add/remove/configure components (Image, Button, TMP_Text, RectMask2D, LayoutGroups) |
| `manage_prefabs` | Save UI as prefab, create prefab variants, modify instances |
| `manage_asset` | Find/create ScriptableObjects (DesignTokens), sprites, fonts |
| `manage_texture` | Sprite import settings (Mesh Type, 9-slice borders, atlas membership) |
| `manage_material` | UI materials (TMP material presets, custom UI shaders) |
| `manage_scene` | Add UI screens to scenes, save scenes |
| `manage_packages` | Install TextMeshPro, Input System, UI Toolkit |
| `create_script` / `script_apply_edits` / `apply_text_edits` | UI controller C# |
| `manage_camera` | `action="screenshot"` for visual verification |
| `find_gameobjects` | Locate existing UI nodes by name/tag/component |
| `read_console` | Catch compile errors, layout-rebuild warnings |
| `refresh_unity` | After any script change |
| `batch_execute` | The most important tool — multi-operation in one round-trip |
| `unity_docs` / `unity_reflect` | Verify Unity API at call time |

---

## Verifying the schema (do this first if unsure)

Don't guess parameter names. `manage_ui` and similar tools have evolved across MCP versions. Verify:

```python
# List actions for a given tool
unity_reflect(target="MCP_for_Unity.Tools.ManageUI", member="actions")

# Or fetch the docstring
tool_search(query="manage_ui actions parameters")
```

If a tool call fails with an unknown-parameter error, re-check via `tool_search` rather than guessing alternates.

---

## Pattern 1 — bootstrap a UI screen from scratch (uGUI)

Build a screen root, Canvas, CanvasScaler, EventSystem, SafeAreaPanel in one batch. This avoids 6 round-trips.

```python
batch_execute(commands=[
    # 1. Top-level screen root (will become the prefab)
    {"tool": "manage_gameobject", "params": {
        "action": "create",
        "name": "SettingsScreen",
        "position": [0, 0, 0]
    }},

    # 2. Canvas under it
    {"tool": "manage_gameobject", "params": {
        "action": "create",
        "name": "Canvas",
        "parent": "SettingsScreen",
        "components_to_add": ["Canvas", "CanvasScaler", "GraphicRaycaster"]
    }},

    # 3. Configure CanvasScaler for mobile portrait
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "Canvas",
        "component_type": "Canvas",
        "property": "renderMode",
        "value": "ScreenSpaceOverlay"
    }},
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "Canvas",
        "component_type": "CanvasScaler",
        "properties": {
            "uiScaleMode": "ScaleWithScreenSize",
            "referenceResolution": [1080, 1920],
            "screenMatchMode": "MatchWidthOrHeight",
            "matchWidthOrHeight": 1.0
        }
    }},

    # 4. SafeAreaPanel under the Canvas
    {"tool": "manage_gameobject", "params": {
        "action": "create",
        "name": "SafeArea",
        "parent": "Canvas",
        "components_to_add": ["RectTransform", "Project.UI.SafeAreaPanel"]
    }},

    # 5. EventSystem (skip if one already exists in the scene)
    {"tool": "manage_gameobject", "params": {
        "action": "create",
        "name": "EventSystem",
        "components_to_add": ["UnityEngine.EventSystems.EventSystem",
                              "UnityEngine.InputSystem.UI.InputSystemUIInputModule"]
    }}
])
```

**Always follow with a verification:**
```python
read_console(types=["error", "warning"], count=10)
manage_camera(action="screenshot", include_image=True, max_resolution=1024)
```

If the screenshot shows an empty Canvas (white/black void), check that `EventSystem` exists and `Canvas.renderMode` is set.

---

## Pattern 2 — anchoring a RectTransform correctly

The correct order: anchors → pivot → sizeDelta → anchoredPosition.

```python
manage_components(
    action="set_property",
    target="SettingsScreen/Canvas/SafeArea/Panel",
    component_type="RectTransform",
    properties={
        "anchorMin":        [0.5, 0.5],
        "anchorMax":        [0.5, 0.5],
        "pivot":            [0.5, 0.5],
        "sizeDelta":        [480, 720],
        "anchoredPosition": [0, 0]
    }
)
```

For a stretched panel (top bar):
```python
properties={
    "anchorMin":  [0, 1],
    "anchorMax":  [1, 1],
    "pivot":      [0.5, 1],
    "sizeDelta":  [0, 80],            # 0 width = stretch; 80 height
    "anchoredPosition": [0, 0]
}
```

For a corner-anchored element (close button, top-right):
```python
properties={
    "anchorMin":  [1, 1],
    "anchorMax":  [1, 1],
    "pivot":      [1, 1],
    "sizeDelta":  [48, 48],
    "anchoredPosition": [-16, -16]    # 16px inset from top-right
}
```

---

## Pattern 3 — building a button (batched)

A primary button as a child of a parent panel, with TMP label.

```python
batch_execute(commands=[
    # Container
    {"tool": "manage_gameobject", "params": {
        "action": "create",
        "name": "Button_Confirm",
        "parent": "Panel/Footer",
        "components_to_add": ["RectTransform", "Image", "Button", "Project.UI.UIThemeApplier"]
    }},
    # Size + anchoring
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "Panel/Footer/Button_Confirm",
        "component_type": "RectTransform",
        "properties": {
            "anchorMin": [0.5, 0.5], "anchorMax": [0.5, 0.5],
            "pivot": [0.5, 0.5],
            "sizeDelta": [200, 48]
        }
    }},
    # Theme role
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "Panel/Footer/Button_Confirm",
        "component_type": "Project.UI.UIThemeApplier",
        "properties": {"role": "Primary", "applyToImage": True}
    }},
    # Child label
    {"tool": "manage_gameobject", "params": {
        "action": "create",
        "name": "Label",
        "parent": "Panel/Footer/Button_Confirm",
        "components_to_add": ["RectTransform", "TMPro.TextMeshProUGUI"]
    }},
    # Label sizing (stretch full)
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "Panel/Footer/Button_Confirm/Label",
        "component_type": "RectTransform",
        "properties": {
            "anchorMin": [0, 0], "anchorMax": [1, 1],
            "offsetMin": [12, 8], "offsetMax": [-12, -8]
        }
    }},
    # Label content
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "Panel/Footer/Button_Confirm/Label",
        "component_type": "TMPro.TextMeshProUGUI",
        "properties": {
            "text": "Confirm",
            "fontSize": 16,
            "alignment": "Center",
            "raycastTarget": False
        }
    }}
])
```

One round-trip. Six in series would be ~6× slower.

---

## Pattern 4 — disable RaycastTarget on every decorative graphic

After building a screen, sweep:

```python
# Find every Image / TMP_Text under the screen root
gos = find_gameobjects(parent="SettingsScreen", has_component="UnityEngine.UI.Graphic")

# For each, check if it (or an ancestor) has a Selectable component
# If not, set raycastTarget = false
# Best done via a small helper script run via execute_custom_tool or directly via batch_execute
```

Or via an editor-only helper script invoked from `execute_menu_item` after dropping it into the project. The skill should know either path works.

---

## Pattern 5 — save as prefab

After the in-scene composition looks right and verifies via screenshot, save:

```python
manage_prefabs(
    action="create",
    source_gameobject="SettingsScreen",
    path="Assets/UI/Prefabs/Screens/SettingsScreen.prefab"
)
```

For variants:
```python
manage_prefabs(
    action="create_variant",
    source_prefab="Assets/UI/Prefabs/Atoms/Button_Base.prefab",
    path="Assets/UI/Prefabs/Atoms/Button_Primary.prefab"
)
```

Then modify the variant's overrides:
```python
manage_prefabs(
    action="modify_instance",
    target="Assets/UI/Prefabs/Atoms/Button_Primary.prefab",
    component_overrides=[
        {"component_type": "Project.UI.UIThemeApplier", "property": "role", "value": "Primary"}
    ]
)
```

---

## Pattern 6 — install TextMeshPro essentials (one-time, per project)

```python
# Check first
result = manage_packages(action="list")
# If com.unity.textmeshpro absent (or not folded into com.unity.ugui in newer Unity), install:
manage_packages(action="install", package_name="com.unity.textmeshpro")

# After install, import TMP Essentials via menu
execute_menu_item(menu_path="Window/TextMeshPro/Import TMP Essential Resources")
refresh_unity(mode="force", scope="scripts", compile="request", wait_for_ready=True)
read_console(types=["error"], count=10)
```

---

## Pattern 7 — the screenshot verification loop

After every meaningful change:

```python
# Force a refresh so any newly created/modified UI is laid out
refresh_unity(mode="force", scope="all", wait_for_ready=True)

# Read console for layout warnings or missing references
read_console(types=["warning", "error"], count=20)

# Capture
result = manage_camera(
    action="screenshot",
    include_image=True,
    max_resolution=1024
)
```

Then *look at the result*. If anchoring is wrong, panels overlap, text overflows, contrast is poor — the screenshot will show it. Verbally describe what you see, compare to intent, decide on the fix.

---

## Pattern 8 — auditing existing UI

For an audit task, do not build anything. Inventory and report.

```python
# 1. Find all Canvas GameObjects
canvases = find_gameobjects(has_component="UnityEngine.Canvas")

# 2. For each canvas, enumerate children with Graphics
for canvas in canvases:
    graphics = find_gameobjects(parent=canvas.name, has_component="UnityEngine.UI.Graphic")
    # Inspect each

# 3. Find legacy Text components (anti-pattern)
legacy_texts = find_gameobjects(has_component="UnityEngine.UI.Text")

# 4. Find Animator on UI prefabs (anti-pattern)
ui_animators = find_gameobjects(has_component="UnityEngine.Animator", parent="Canvas")

# 5. Find Mask vs RectMask2D usage
masks = find_gameobjects(has_component="UnityEngine.UI.Mask")
```

Compile findings into a structured report (the audit checklist in `performance.md`) and present to the user. Don't auto-fix without consent unless the task explicitly requested it.

---

## Pattern 9 — UI Toolkit screen bootstrap

For a UIDocument-based screen:

```python
# 1. Create the UXML file
create_script(
    path="Assets/UI/Screens/SettingsScreen.uxml",
    contents="<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<ui:UXML ...>...</ui:UXML>"
)

# 2. Create or verify PanelSettings exists
manage_asset(action="find", search="t:PanelSettings", path="Assets/UI/PanelSettings")

# 3. Add UIDocument GameObject to the scene
manage_gameobject(
    action="create",
    name="SettingsUIDoc",
    components_to_add=["UnityEngine.UIElements.UIDocument"]
)

# 4. Wire up the UIDocument
manage_components(
    action="set_property",
    target="SettingsUIDoc",
    component_type="UnityEngine.UIElements.UIDocument",
    properties={
        "panelSettings":   "Assets/UI/PanelSettings/PanelSettings_Main.asset",
        "visualTreeAsset": "Assets/UI/Screens/SettingsScreen.uxml"
    }
)

# 5. Create the controller C#
create_script(
    path="Assets/UI/Screens/SettingsScreen.cs",
    contents="// controller code from ui-toolkit-patterns.md"
)

refresh_unity(mode="force", scope="scripts", compile="request", wait_for_ready=True)
read_console(types=["error"], count=10)
```

---

## Common failure modes & fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| Tool call returns "unknown parameter" | Parameter name changed across MCP version | Run `tool_search` or `unity_reflect` to verify schema |
| `manage_gameobject` create succeeds but child not visible | Parent path wrong (case-sensitive); missing Canvas in chain; not under EventSystem-reachable hierarchy | Verify path via `find_gameobjects`; ensure Canvas exists; check RectTransform anchoring |
| Screenshot is blank/black | No camera; UI Camera with wrong culling mask; renderMode = ScreenSpaceCamera with no camera set | Switch to ScreenSpaceOverlay or assign UI camera |
| Buttons don't fire | Missing EventSystem; multiple EventSystems; RaycastTarget false on the Button's Image | Add EventSystem; check raycast |
| Compile error after script change | Namespace mismatch; missing using; assembly definition exclusion | `read_console(types=["error"])` — read the actual message, don't guess |
| TMP_Text shows "tofu" boxes | Font asset's character table doesn't include the glyph | Add character to font asset's dynamic atlas or import a font with the needed range |
| `LayoutGroup` rebuild loops | LayoutGroup + ContentSizeFitter on same chain | Remove one; manually size |

---

## Tool-call efficiency rules

- **`batch_execute` is mandatory** for ≥3 related operations. A 10-element UI built one-call-at-a-time wastes ~10× the time.
- **Refresh sparingly**. `refresh_unity` triggers domain reload and is *expensive*. Batch many script changes, then one refresh.
- **Read console only for errors/warnings** unless debugging. `read_console(types=["log"])` floods the response.
- **Limit screenshot resolution**. `max_resolution=1024` is plenty for verification; default 2048+ wastes payload.
- **Set `include_image=True` deliberately**. Without it the screenshot is saved to disk but not returned inline — useful for batch verification but not for the AI to "see" the result.
