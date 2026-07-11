# MCP Cookbook (MCP for Unity)

Concrete tool-call patterns for UI work, verified against the current server signatures. The shapes below are the *correct* ones — do not fall back to memorised-but-wrong parameter names. If a call is refused, it tripped the safety classifier (read the class it names, re-check `safety_status()`); if a param is rejected, the error names the bad key — fix that key, don't shop for a different tool. The base operating loop (orient → posture → cheapest tier → verify → report) lives in the **`unity-mcp-operator`** skill; this file is the UI-specific layer on top of it.

**Safety class, at a glance:** building/restyling UI (`manage_gameobject` create/modify, `manage_components`, `create_script`/`apply_text_edits`, `manage_ui` create, `manage_scriptable_object` create, `manage_prefabs` create) is **WRITE** — needs `write` mode. Screenshots and prefab-stage open/close are **VALIDATE** (`review_only`+). Reads — `audit_ui_layout`, `find_gameobjects`, `manage_asset` search, every `mcpforunity://` resource — are **READ** (any mode). Deletes **and package installs** are **DESTRUCTIVE** (`write` + `confirm:true`): `manage_packages(action="add_package"/"remove_package")` for TextMeshPro / Input System is destructive-class, so it is refused without `confirm:true` even in `write` mode — expect a CONFIRM_REQUIRED block otherwise.

---

## The UI-relevant tool surface

| Tool | UI use | Notes |
|---|---|---|
| `manage_gameobject` | Create/modify/delete/duplicate any GameObject; the workhorse for uGUI hierarchy | core; `delete` is DESTRUCTIVE |
| `manage_components` | Add/remove/set properties (Image, Button, TMP_Text, RectMask2D, LayoutGroups, RectTransform) | core |
| `manage_ui` | **UI Toolkit only** — author `.uxml`/`.uss`, attach `UIDocument`, create `PanelSettings`, inspect the visual tree, `render_ui` preview | group `ui`, **disabled by default** — `manage_tools(action="activate", group="ui")` |
| `manage_prefabs` | Create prefab from a scene object, edit prefab contents (headless or via prefab stage) | core; no one-call "create variant" action — see Pattern 5 |
| `manage_asset` | Search/import/create/move assets (sprites, fonts, folders) | core; `search`/`get_info` are READ |
| `manage_scriptable_object` | Create/modify `DesignTokens` and other ScriptableObject assets | group `scripting_ext`, **disabled by default** — activate first |
| `manage_texture` | Sprite import settings (Mesh Type, 9-slice borders) | core |
| `manage_material` | UI materials (TMP presets, custom UI shaders) | core |
| `manage_scene` | Save/open scenes that host UI (replaces the open scene — check dirty state) | core |
| `manage_packages` | Install TextMeshPro / Input System / UI Toolkit | core; `list_packages`, `add_package` |
| `create_script` / `apply_text_edits` / `script_apply_edits` | UI controller C#. `.cs` only — **not** for `.uxml`/`.uss` (use `manage_ui`) | auto-compile; do not `refresh_unity` after |
| `manage_camera` | `action="screenshot"` for visual verification; `visibility_report` / `screenshot_compare` for zero/low-token facts | screenshots are VALIDATE |
| `find_gameobjects` | Locate UI nodes by name/tag/layer/component/path — returns **instance IDs only** | READ; read full data via the gameobject resource |
| `audit_ui_layout` | Analytic uGUI layout audit across a resolution matrix (off-screen, overlap, tiny targets, missing EventSystem …) | READ; see the `unity-ui-auditor` skill |
| `read_console` | Catch compile errors and layout-rebuild warnings; `format="summary"` to dedupe | READ |
| `unity_reflect` / `unity_docs` | Verify the **Unity C# API** before writing controller code (not MCP tool schemas) | group `docs`, disabled by default |
| `batch_execute` | Multi-operation in one round-trip — up to 25 commands | classified by its most severe command |

uGUI hierarchy is built with `manage_gameobject` + `manage_components`. `manage_ui` does **not** create Canvases or uGUI elements — it is exclusively the UI Toolkit surface (UXML/USS/UIDocument/PanelSettings).

---

## Verifying a call before you make it

Your MCP client already exposes each tool's parameter schema and description — that is the source of truth, not memory. Two habits:

- **Wrong tool param?** The rejection names the offending key. Re-read that tool's schema and fix the key. Do not guess alternates.
- **Missing tool?** It is probably in a deactivated group, not gone. Run `manage_tools(action="list_groups")`; activate what you need (`ui` for UI Toolkit, `scripting_ext` for `manage_scriptable_object`, `docs` for `unity_reflect`).

`unity_reflect` verifies **Unity's live C# API** (`action="get_type"` / `"get_member"` / `"search"`, e.g. "does `TMP_Text` have `SetText(StringBuilder)`?") before you write controller code — it does not describe MCP tools. There is no `tool_search` Unity tool.

---

## Pattern 1 — bootstrap a UI screen from scratch (uGUI)

Build a screen root, Canvas, CanvasScaler, EventSystem, SafeAreaPanel in one batch. WRITE-class — needs `write` mode.

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

    # 3. Configure Canvas + CanvasScaler for mobile portrait
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "SettingsScreen/Canvas",
        "component_type": "Canvas",
        "property": "renderMode",
        "value": "ScreenSpaceOverlay"
    }},
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "SettingsScreen/Canvas",
        "component_type": "CanvasScaler",
        "properties": {
            "uiScaleMode": "ScaleWithScreenSize",
            "referenceResolution": [1080, 1920],
            "screenMatchMode": "MatchWidthOrHeight",
            "matchWidthOrHeight": 1.0
        }
    }},

    # 4. SafeAreaPanel under the Canvas (Project.UI.SafeAreaPanel from mobile-considerations.md)
    {"tool": "manage_gameobject", "params": {
        "action": "create",
        "name": "SafeArea",
        "parent": "SettingsScreen/Canvas",
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

`target` uses the full hierarchy path (case-sensitive). A custom component (`Project.UI.SafeAreaPanel`) only adds cleanly once its script has compiled — author it first and wait on compilation (see Pattern 6 workflow), or the add silently no-ops.

**Always follow with a verification:**
```python
read_console(action="get", types=["error", "warning"], count=10)
manage_camera(action="screenshot", include_image=True, max_width=1024)   # no camera → captures the overlay Canvas
```

If the screenshot shows an empty Canvas (white/black void), check that `EventSystem` exists and `Canvas.renderMode` is `ScreenSpaceOverlay`.

---

## Pattern 2 — anchoring a RectTransform correctly

The correct order: anchors → pivot → sizeDelta → anchoredPosition. One `set_property` with a `properties` dict applies them together.

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

A primary button as a child of a parent panel, with TMP label. One round-trip.

```python
batch_execute(commands=[
    # Container
    {"tool": "manage_gameobject", "params": {
        "action": "create",
        "name": "Button_Confirm",
        "parent": "SettingsScreen/Canvas/SafeArea/Panel/Footer",
        "components_to_add": ["RectTransform", "Image", "Button", "Project.UI.UIThemeApplier"]
    }},
    # Size + anchoring
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "SettingsScreen/Canvas/SafeArea/Panel/Footer/Button_Confirm",
        "component_type": "RectTransform",
        "properties": {
            "anchorMin": [0.5, 0.5], "anchorMax": [0.5, 0.5],
            "pivot": [0.5, 0.5],
            "sizeDelta": [200, 48]
        }
    }},
    # Theme role (token-driven colour, no inline hex)
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "SettingsScreen/Canvas/SafeArea/Panel/Footer/Button_Confirm",
        "component_type": "Project.UI.UIThemeApplier",
        "properties": {"role": "Primary", "applyToImage": True}
    }},
    # Child label
    {"tool": "manage_gameobject", "params": {
        "action": "create",
        "name": "Label",
        "parent": "SettingsScreen/Canvas/SafeArea/Panel/Footer/Button_Confirm",
        "components_to_add": ["RectTransform", "TMPro.TextMeshProUGUI"]
    }},
    # Label sizing (stretch full, small padding)
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "SettingsScreen/Canvas/SafeArea/Panel/Footer/Button_Confirm/Label",
        "component_type": "RectTransform",
        "properties": {
            "anchorMin": [0, 0], "anchorMax": [1, 1],
            "offsetMin": [12, 8], "offsetMax": [-12, -8]
        }
    }},
    # Label content
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": "SettingsScreen/Canvas/SafeArea/Panel/Footer/Button_Confirm/Label",
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

Six calls in series would be ~6× slower. A `batch_execute` is classified by its most severe command; all of the above are WRITE, so the batch needs `write` mode (no destructive command here, so no `confirm`).

---

## Pattern 4 — disable RaycastTarget on decorative graphics

Only interactive graphics need to be raycast targets. Sweep the decoratives off after building a screen.

```python
# 1. Find every Image and every TMP_Text under the scene (IDs only).
images = find_gameobjects(search_term="UnityEngine.UI.Image", search_method="by_component")
texts  = find_gameobjects(search_term="TMPro.TextMeshProUGUI", search_method="by_component")

# 2. For each, decide interactive-or-not. This is a JUDGEMENT, not an automatic check:
#    a graphic is interactive if it (or an ancestor) carries a Selectable
#    (Button/Toggle/Slider) or is an intentional click-blocking scrim.
#    Read components via mcpforunity://scene/gameobject/{id}/components to check.

# 3. Turn raycastTarget off on the decoratives, batched:
batch_execute(commands=[
    {"tool": "manage_components", "params": {
        "action": "set_property",
        "target": 12345,                       # instance ID from step 1
        "component_type": "UnityEngine.UI.Image",
        "property": "raycastTarget",
        "value": False
    }},
    # ... one command per decorative graphic (≤25 per batch; page the rest)
])
```

Deciding which graphics are decorative is a heuristic — state it as one when you report ("disabled raycast on N graphics I judged non-interactive"). Do **not** try to script this sweep through `execute_code` (disabled by default) or `execute_menu_item` (allowlist-only in this fork); the batched `manage_components` path above is the supported one.

---

## Pattern 5 — save as prefab (and the truth about variants)

After the in-scene composition looks right and verifies via screenshot, promote the root to a prefab:

```python
manage_prefabs(
    action="create_from_gameobject",
    target="SettingsScreen",
    prefab_path="Assets/UI/Prefabs/Screens/SettingsScreen.prefab",
    allow_overwrite=False
)
```

Edit a prefab's contents headlessly (no scene instance needed):

```python
manage_prefabs(
    action="modify_contents",
    prefab_path="Assets/UI/Prefabs/Atoms/Button_Base.prefab",
    target="Label",                                     # object within the prefab
    component_properties={
        "Project.UI.UIThemeApplier": {"role": "Primary"}
    }
)
```

**Prefab variants — read this.** The server has **no** one-call `create_variant` action. Two honest paths:

1. **Preferred (token-driven, no true variant needed):** author each "variant" as its own small prefab whose only difference is the `UIThemeApplier` role (uGUI) or USS class (UI Toolkit). `Button_Primary` / `Button_Secondary` / `Button_Destructive` differ by *role*, not by hand-painted colour overrides — which is exactly what the design system wants. Create each via `create_from_gameobject` from a themed scene instance.
2. **True Unity prefab-variant links** (base→variant inheritance) cannot be minted in one MCP call today. If the project genuinely needs the variant link, create it in the Editor UI, or open the base with `manage_prefabs(action="open_prefab_stage")`, edit inside the stage with `manage_gameobject`/`manage_components`, and `save_prefab_stage`.

Do not claim you created a "prefab variant" when you created a sibling prefab — describe what you actually made.

---

## Pattern 6 — install TextMeshPro essentials (one-time, per project)

```python
# Check first (READ)
manage_packages(action="list_packages")

# If com.unity.textmeshpro is absent (and not folded into com.unity.ugui on newer Unity), install.
# add_package is DESTRUCTIVE — needs write mode AND confirm:true (acknowledgement metadata, not human sign-off):
manage_packages(action="add_package", package="com.unity.textmeshpro", confirm=True)
```

Importing the **TMP Essential Resources** normally runs the menu item `Window/TextMeshPro/Import TMP Essential Resources`. In this fork `execute_menu_item` is **allowlist-only** — that path is unlikely to be on the allowlist, so do not build the workflow on it succeeding. If it is not allowlisted, tell the user to run the import once from the Editor menu (or ask them to add the path to the allowlist). Either way, TMP text renders as "tofu" boxes until the essentials exist.

Package installs trigger their own resolve/compile. Wait on it the same way as a script edit — poll `mcpforunity://editor/state` for `compilation.is_compiling == false`, then `read_console(action="get", types=["error"], count=10)`. Do **not** call `refresh_unity`.

---

## Pattern 7 — the screenshot verification loop

After every meaningful change, verify — but do **not** force a domain reload. UI created via `manage_gameobject`/`manage_components` is live immediately; `refresh_unity` is a heavy, unnecessary round-trip here.

```python
# 1. Console first — cheapest signal.
read_console(action="get", types=["warning", "error"], count=20)

# 2. Analytic layout audit — deterministic, no pixels, works even in read_only.
audit_ui_layout()          # default matrix; add resolutions=[...] for the game's real targets

# 3. Pixels last, right-sized, and WITHOUT a camera for overlay UI.
manage_camera(
    action="screenshot",
    include_image=True,
    max_width=1024          # inline image downscaled to this; disk file untouched
)
```

Then *look at the result*. If anchoring is wrong, panels overlap, text overflows, or contrast is poor, the screenshot shows it and `audit_ui_layout` names it by object path. Describe what you actually see, compare to intent, decide the fix. Keep `max_width` modest (512–1024) — enough to read labels and judge contrast without shipping a multi-MB payload. Use `crop_target="Path/To/Element"` (+ `crop_padding_px`) to zoom the inline image onto one control, or `manage_camera(action="screenshot_compare", baseline_path=...)` for a numeric before/after diff with no image at all.

---

## Pattern 8 — auditing existing UI (build nothing)

For an audit, inventory and report — do not mutate. Lead with the analytic tool; it is READ-class and runs in `read_only`.

```python
# 1. Analytic layout audit across the game's resolution matrix. This is the backbone.
audit_ui_layout(
    resolutions=["1920x1080", "3440x1440", "1280x800"],   # add ultrawide + smallest supported
    min_target_px=44
)
# → findings[{check, severity, advisory, object_path, resolution, reason, suggested_fix}]

# 2. Enumerate specific anti-patterns by component type (IDs only):
find_gameobjects(search_term="UnityEngine.Canvas",        search_method="by_component")
find_gameobjects(search_term="UnityEngine.UI.Text",       search_method="by_component")  # legacy Text smell
find_gameobjects(search_term="UnityEngine.Animator",      search_method="by_component")  # Animator-on-UI smell
find_gameobjects(search_term="UnityEngine.UI.Mask",       search_method="by_component")  # Mask vs RectMask2D

# 3. For any hit, read details via the resource (not a mutation tool):
#    mcpforunity://scene/gameobject/{id}/components
```

`find_gameobjects` searches by a **single** `search_method` + `search_term` and returns instance IDs — there is no `parent=` or `has_component=` filter. Scope to a subtree by pathing the term with `search_method="by_path"`, or filter the returned IDs against the object resource.

Compile findings into a structured report grouped by severity (the audit checklist in `performance.md`). `advisory:true` findings from `audit_ui_layout` are heuristics — report them as "worth checking", not defects. Don't auto-fix without consent unless the task explicitly asked for it. For a deeper, dedicated layout audit, hand off to the **`unity-ui-auditor`** skill.

---

## Pattern 9 — UI Toolkit screen bootstrap

`manage_ui` owns UXML/USS/UIDocument/PanelSettings and lives in the `ui` group — **activate it first**. `.uxml`/`.uss` are authored with `manage_ui(action="create")`, **not** `create_script` (which only accepts `.cs`).

```python
# 0. Enable the UI Toolkit tool group (once per session).
manage_tools(action="activate", group="ui")

# 1. Create the UXML (structure). Use the ui: namespace on <ui:Style>, not bare <Style>.
manage_ui(
    action="create",
    path="Assets/UI/Screens/SettingsScreen.uxml",
    contents="<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<ui:UXML xmlns:ui=\"UnityEngine.UIElements\">...</ui:UXML>"
)

# 2. Create the USS (styling).
manage_ui(
    action="create",
    path="Assets/UI/Styles/settings.uss",
    contents=".screen { flex-grow: 1; background-color: var(--color-surface); }"
)

# 3. Link the stylesheet into the UXML.
manage_ui(action="link_stylesheet",
          path="Assets/UI/Screens/SettingsScreen.uxml",
          stylesheet="Assets/UI/Styles/settings.uss")

# 4. Ensure a PanelSettings asset exists (create if none). scale_mode / settings dict configure it.
manage_ui(
    action="create_panel_settings",
    path="Assets/UI/PanelSettings/PanelSettings_Main.asset",
    settings={
        "scaleMode": "ScaleWithScreenSize",
        "referenceResolution": {"width": 1080, "height": 1920},
        "screenMatchMode": "MatchWidthOrHeight",
        "match": 1.0
    }
)

# 5. Create the host GameObject, then attach + wire a UIDocument to it.
manage_gameobject(action="create", name="SettingsUIDoc")
manage_ui(
    action="attach_ui_document",
    target="SettingsUIDoc",
    source_asset="Assets/UI/Screens/SettingsScreen.uxml",
    panel_settings="Assets/UI/PanelSettings/PanelSettings_Main.asset"   # omit to auto-create a default
)

# 6. Controller C# (this IS a .cs file → create_script). It auto-compiles.
create_script(
    path="Assets/UI/Screens/SettingsScreen.cs",
    contents="// controller from ui-toolkit-patterns.md"
)

# 7. Wait for compile, then check console. Do NOT refresh_unity.
#    Poll mcpforunity://editor/state until compilation.is_compiling == false, then:
read_console(action="get", types=["error"], count=10)

# 8. Preview the panel (optional): render_ui captures the UI Toolkit panel to a PNG.
manage_ui(action="render_ui", target="SettingsUIDoc", include_image=True, max_resolution=512)
```

`render_ui` is the UI Toolkit screenshot (its own tool path, WRITE-class — it can persist a RenderTexture); `manage_camera` screenshots do not see UI Toolkit panels the same way, so prefer `render_ui` for UXML previews. Inspect a live tree with `manage_ui(action="get_visual_tree", target="SettingsUIDoc")`.

---

## Common failure modes & fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| Tool call returns "unknown parameter" | Wrong param key for that tool | The error names the bad key — re-read that tool's schema and correct it; don't guess an alternate |
| Tool "does not exist" / not listed | Its group is deactivated | `manage_tools(action="list_groups")`; activate `ui` (UI Toolkit), `scripting_ext` (ScriptableObjects), `docs` (`unity_reflect`) |
| Call refused with a class name | Safety mode forbids that class | Re-check `safety_status()`; switch to an allowed action or tell the user the required mode plainly |
| `manage_gameobject` create succeeds but child not visible | Parent path wrong (case-sensitive); no Canvas in the chain | Verify path via `find_gameobjects`; ensure a Canvas ancestor exists; check RectTransform anchoring |
| Screenshot is blank/black | Passed a `camera` for overlay UI, or no camera in scene | Omit `camera` so ScreenCapture grabs overlay canvases; for a camera viewpoint switch the Canvas off overlay |
| UI Toolkit panel absent from a `manage_camera` shot | Camera capture path doesn't composite UITK the same way | Use `manage_ui(action="render_ui")` for UXML previews |
| Buttons don't fire | Missing/duplicate EventSystem; `raycastTarget=false` on the Button's Image | Add one EventSystem; re-enable raycast on the interactive graphic |
| Compile error after script change | Namespace mismatch; missing using; asmdef exclusion | `read_console(action="get", types=["error"])` — read the message, don't guess |
| Custom component won't add | Its script hasn't compiled yet | Author the script, wait for `compilation.is_compiling == false`, then add the component |
| TMP_Text shows "tofu" boxes | TMP Essentials not imported, or glyph missing from the font atlas | Import TMP Essentials (Editor menu); add the glyph range to the font asset |
| `LayoutGroup` rebuild loops | `LayoutGroup` + `ContentSizeFitter` on the same chain | Remove one; size manually |

---

## Tool-call efficiency rules

- **`batch_execute` for ≥3 related operations.** Up to 25 commands per batch. A 10-element UI built one call at a time wastes ~10× the time.
- **Never `refresh_unity` after a script/UXML/USS edit.** `create_script` / `apply_text_edits` / `script_apply_edits` and package installs already trigger import + compilation. Wait by polling `mcpforunity://editor/state` for `compilation.is_compiling == false`; then read the console. `refresh_unity` forces an extra domain reload for nothing.
- **Read errors, not the whole console.** `read_console(action="get", types=["error"])`; add `format="summary"` when errors flood (deduped signatures with counts).
- **Right-size screenshots.** `max_width` (single image, default 1280 → set 512–1024 for UI) downscales the inline payload; the disk file is unchanged. `max_resolution` governs batch/contact-sheet tiles. Omit `camera` for overlay UI. Reach for `visibility_report` / `screenshot_compare` when a geometric or numeric answer beats an image.
- **Read via resources, mutate via tools.** GameObject/component/selection reads come from `mcpforunity://…` resources (READ, free in any mode); reserve the `manage_*` tools for actual changes.
