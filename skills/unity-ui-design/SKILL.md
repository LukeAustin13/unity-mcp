---
name: unity-ui-design
description: Senior-level Unity UI design and implementation through MCP for Unity — uGUI (Canvas-based) AND UI Toolkit (UXML/USS), with a mobile-first / desktop-aware lens. Use this skill whenever the user is building, restyling, refactoring, or reviewing any user-facing screen in Unity — menus, HUDs, settings, inventories, dialogue boxes, popups, tabs, scroll views, toolbars, world-space UI, RectTransform work, Canvas setup, prefab structuring, TextMeshPro work, UXML/USS authoring, mobile safe-areas, touch-target sizing, design tokens, theming, accessibility passes, or input wiring. Trigger this even on indirect cues — a mockup screenshot, a Figma link, "make it look better", "the menu feels off", "responsive across phones and tablets", or any task that touches a Canvas, a UIDocument, a RectTransform, a Button, a Slider, a TMP_Text, a Mask, a Layout Group, or a ScrollRect. Strongly prefer this skill over ad-hoc UI generation; it enforces design tokens, atomic prefabs, anchored layouts, Canvas hygiene, screenshot-verified iteration, accessibility, and mobile safe-areas that one-shot generation routinely misses. Building or restyling UI is WRITE-class — it needs `write` safety mode; a read-only review (analytic audit + screenshots) runs in `review_only`, and the pure-math layout audit runs in `read_only`.
license: MIT
---

# Unity UI Design (MCP for Unity)

This skill drives Unity UI work through [MCP for Unity](https://github.com/LukeAustin13/unity-mcp) (an improved fork of Coplay's unity-mcp). The job is not to produce UI that "works" — it is to produce UI a shipping studio would accept on the first pass. That means design tokens before colours, prefabs before scene-bound nodes, anchors before pixels, screenshots before declarations of done.

Be direct. Be opinionated. Push back when the user asks for something that will create technical debt — propose the correct alternative, then proceed. Do not hedge. Do not pad. Single concrete recommendation over a list of options.

---

## First-call protocol (run on every UI task)

Do these in order before producing any UI. Skip steps only when you can prove they're already satisfied. Steps 0–1 are the orientation the `unity-mcp-operator` skill teaches in full — read it if a call is refused or the posture is unclear; do not re-derive it here.

0. **Check safety posture, then orient.** Call `safety_status()` (or read `mcpforunity://server/safety`). Building UI is WRITE-class — if the mode is `read_only` or `review_only`, you cannot create/modify GameObjects; say so and switch to a read-only review (audit + screenshots) or stop. Then read `mcpforunity://editor/context` once (editor state + selection + prefab stage + console counts). If it reports compiling, wait before acting.

1. **Detect the UI system in use.** Call `manage_packages(action="list_packages")`. If `com.unity.ui` / `com.unity.ui.builder` is present and the project has `.uxml` / `.uss` files, treat as **UI Toolkit project**. If only `com.unity.ugui` is present or the scene contains a `Canvas` GameObject, treat as **uGUI project**. If both — ask the user which surface this task targets; do not assume. Decision tree:

   ```
   Has UIDocument in scene OR .uxml in Assets?  ──► UI Toolkit
   Has Canvas in scene OR project standardises on Canvas? ──► uGUI
   Both present ──► ASK; do not silently mix systems on one screen
   New project, runtime gameplay HUD ──► uGUI (animation/world-space maturity)
   New project, editor tool or data-heavy menu ──► UI Toolkit
   ```

   **UI Toolkit authoring needs a tool group.** The `manage_ui` tool (UXML/USS/UIDocument/PanelSettings) lives in the non-core `ui` group, which is **disabled by default**. Before any UI Toolkit work run `manage_tools(action="activate", group="ui")` (discover groups with `manage_tools(action="list_groups")`). uGUI work needs no group toggle — it uses `manage_gameobject` / `manage_components`, which are core.

2. **Detect the target platform.** Read the `platform` field from `mcpforunity://project/info`. Mobile builds (Android/iOS) require safe-area handling, 48dp / 44pt minimum touch targets, and CanvasScaler `matchWidthOrHeight = 1` (height-match) for portrait. Desktop requires hover/focus states and keyboard navigation. Load `references/mobile-considerations.md` for mobile work.

3. **Inventory existing tokens / prefabs.** Search with `manage_asset(action="search", path="Assets", search_pattern="t:ScriptableObject")` (or `search_pattern="t:DesignTokens"` if you know the class) and `manage_asset(action="search", path="Assets/UI", search_pattern="t:Prefab")`. `action="search"` is READ-class — it works in every mode. If a design system already exists, you extend it — you do not invent a new one. If none exists and the task involves more than a single throwaway element, create one before authoring components (see `references/design-system.md`).

4. **Verify required packages.** TextMeshPro must be installed (`com.unity.textmeshpro` or built-in via `com.unity.ugui` ≥ Unity 6). If absent, install with `manage_packages(action="add_package", package="com.unity.textmeshpro", confirm=True)` — `add_package` is DESTRUCTIVE-class, so it needs `write` mode plus `confirm:true` — before authoring any text. Input System should be installed for new projects.

5. **Take a baseline screenshot** if the task is a refactor or audit — you cannot improve what you have not seen. For Screen Space - Overlay UI (95% of cases) call `manage_camera(action="screenshot", include_image=True, max_width=1024)` with **no `camera` argument** — passing a specific camera renders through that camera and EXCLUDES overlay canvases, so your UI would not appear. Screenshots are VALIDATE-class (need `review_only`+). The inline image is downscaled to `max_width`; the disk file is untouched.

---

## Hard rules (non-negotiable)

These exist because they are the failure modes that distinguish junior output from senior output. Violate one and the work will look amateur regardless of the rest.

**Typography**
- Use `TextMeshPro - Text (UI)` (`TMP_Text`) for every piece of text. The legacy `Text` component is forbidden. It blurs at scaled resolutions, has no rich styling, and locks you out of SDF effects.
- Reference fonts via a typography token asset, never by direct font reference on a component.

**Layout & anchoring**
- Set `RectTransform.anchorMin`, `anchorMax`, and `pivot` **before** `anchoredPosition` and `sizeDelta`. Anchoring after sizing produces snapping that looks correct in the editor and broken at other resolutions.
- Anchor to the logical parent region (e.g. a "TopBar" anchors to top-stretch with `anchorMin=(0,1) anchorMax=(1,1)`), never to centre with hardcoded offsets.
- Stretch panels along at least one axis. Hardcoded width AND height on a top-level panel is almost always wrong.

**Design tokens**
- No inline hex colours in components. All colour, font, size, radius, spacing values come from a token asset (`DesignTokens` ScriptableObject for uGUI; `:root` USS custom properties for UI Toolkit).
- Spacing must come from a scale (4, 8, 12, 16, 24, 32, 48, 64). Arbitrary values like 7, 13, 22 are rejected.
- Type scale is fixed: prefer 1.250 (minor third) or 1.333 (perfect fourth) ratio across at most 6 sizes.

**Prefabs**
- Every reusable UI element is a prefab. No orphan UI in scenes beyond the screen-root prefab itself.
- Use prefab variants for state/skin variations (e.g. `Button_Primary`, `Button_Secondary` are variants of `Button_Base`).

**Canvas hygiene (uGUI)**
- Static UI (background, frame, icons that don't change) and dynamic UI (counters, health bars, timers) go on **separate sub-canvases**. A single change on a unified canvas rebuilds the whole mesh.
- `RaycastTarget = false` on every Image and TMP_Text that doesn't receive input. This is the cheapest perf win in UI and it is almost always wrong by default.
- Prefer `RectMask2D` over `Mask` for axis-aligned clipping. `Mask` requires a separate draw call per mask.

**Performance & interaction**
- Do not use `Animator` for simple UI tweens. Use DOTween, LitMotion, or a coroutine. Animator has continuous overhead.
- Kill tweens on `OnDisable`; do not let them target a destroyed object.
- Never `GetComponent` in `Update`. Cache in `Awake` / `OnEnable`.
- Wire button callbacks via `UnityEvent` in the inspector for designer-facing wiring, or via `Button.onClick.AddListener` from code for system-facing wiring — pick one per project, don't mix.

**Accessibility**
- WCAG AA contrast minimum (4.5:1 for body text, 3:1 for large text and UI components). Verify with a no-`camera` overlay screenshot and visually inspect the result. Contrast judged from a screenshot is a human/eye check, not an automatic pass — report it as observed, not measured.
- Touch targets ≥ 48dp on Android, ≥ 44pt on iOS. This is per-element minimum, not pixel size — convert via DPI. `audit_ui_layout(min_target_px=44)` flags too-small interactive rects analytically across resolutions (advisory where a LayoutGroup drives the size).
- Provide focus visuals for keyboard/gamepad navigation on desktop and console.

**Verification**
- After every meaningful milestone, screenshot and visually verify: `manage_camera(action="screenshot", include_image=True, max_width=1024)` with **no `camera`** for Screen Space - Overlay UI (a camera-scoped capture drops overlay canvases). Use `crop_target="Path/To/Element"` to zoom the inline image onto one control. UI is a visual medium; declaring something done without looking is unprofessional.
- After building or restyling, run the analytic pass: `audit_ui_layout()` (READ-only, no pixels — off-screen / overlap / tiny-target / clipped-text / missing-EventSystem checks across a resolution matrix). It is the fast, deterministic complement to the screenshot; treat `advisory:true` findings as "worth checking", not confirmed defects. The dedicated deep-audit lane is the **`unity-ui-auditor`** skill — cross-reference it for a full pre-ship layout audit.
- **Do not call `refresh_unity` after a script edit.** `create_script` / `apply_text_edits` / `script_apply_edits` already trigger import + compilation. Wait by polling `mcpforunity://editor/state` until `compilation.is_compiling` is false, then `read_console(action="get", types=["error"], count=20)`. A new component/type is unusable until compilation succeeds.

---

## Standard workflow (the 7-step loop)

This is the order every non-trivial UI task follows. Deviations require justification.

1. **Understand & sketch.** Restate the goal in one sentence. List the screens/components and their states (default, hover, pressed, disabled, focused, loading, empty, error). If a mockup or screenshot is provided, load it. If not, sketch the hierarchy as a tree in your reply *before* calling any tool.

2. **Detect & inventory** (see First-call protocol).

3. **Establish tokens.** If no token asset exists, create one. See `references/design-system.md`. uGUI tokens = a `DesignTokens` ScriptableObject built with `create_script` (the class) then `manage_scriptable_object(action="create")` (the asset); UI Toolkit tokens = a `tokens.uss` `:root` block written with `manage_ui(action="create", path="…uss")`. Not optional.

4. **Compose atomically.** Build atoms (Button, Label, Icon) → molecules (ButtonRow, FormField) → organisms (SettingsTab, HUDCluster) → screens (SettingsScreen). Each atom and molecule becomes a prefab before being composed up. See `references/component-recipes.md`.

5. **Batch creation.** Use `batch_execute` for any operation creating ≥3 GameObjects or setting ≥3 properties. A 12-element form built one-call-at-a-time is 12× the round-trip latency.

6. **Wire logic.** Create C# scripts with `create_script` / edit with `apply_text_edits`. These auto-compile — do **not** call `refresh_unity`. Poll `mcpforunity://editor/state` for `compilation.is_compiling == false`, then `read_console(action="get", types=["error"], count=20)` → fix → repeat until clean.

7. **Screenshot, audit & critique.** `manage_camera(action="screenshot", include_image=True, max_width=1024)` (no `camera` for overlay UI) and `audit_ui_layout()`. Inspect the screenshot against the original intent and read the audit findings. Catch your own work as if you were code-reviewing a junior — anchoring, spacing rhythm, contrast, alignment, state coverage. Iterate.

---

## Communication style (when reporting to the user)

- Lead with what changed and what it looks like — attach screenshots inline when produced.
- State the design decisions you made and *why* in one sentence each.
- Flag any compromises or technical debt you introduced.
- Do not list "next steps you could take" unless asked. The user prefers single concrete recommendations.
- If you encountered an error and worked around it, say so explicitly.

---

## Reference index — load on demand

Read the relevant file before tackling a specialised sub-task. Do not pre-load everything.

| File | Load when |
|---|---|
| `references/ugui-patterns.md` | Working with Canvas / RectTransform / Anchors / CanvasScaler / EventSystem |
| `references/ui-toolkit-patterns.md` | Working with UXML, USS, UIDocument, runtime UIDocument bindings |
| `references/design-system.md` | Creating or extending design tokens, theming, dark/light variants |
| `references/component-recipes.md` | Building common components: button, modal, tabs, list, settings, HUD, inventory grid, dialogue, toast |
| `references/performance.md` | Audits, Canvas splitting, raycast hygiene, atlasing, layout-group cost, pooling, overdraw |
| `references/mobile-considerations.md` | Any task targeting Android/iOS — safe areas, touch targets, thumb zones, virtual keyboard, orientation, haptics |
| `references/mcp-cookbook.md` | When you need the exact MCP tool-call shape for a UI operation, batch patterns, screenshot verification loop |

---

## Anti-patterns to actively catch (audit mode)

When reviewing existing UI, scan for these. They are the recurring offenders.

| Smell | Why it matters | Fix |
|---|---|---|
| Legacy `Text` component | Blurs at scale, no SDF effects | `manage_components` remove `UnityEngine.UI.Text`, add `TMPro.TextMeshProUGUI`, re-set text/font/size |
| `RaycastTarget = true` on decorative `Image` / `TMP_Text` | Wasted raycast cost per frame on every pointer event | Toggle off; only interactive elements need it |
| Single mega-Canvas | One small change rebuilds the whole mesh | Split into static + dynamic sub-canvases |
| `Animator` on simple UI tweens | Continuous evaluation cost; lifecycle issues | DOTween / LitMotion / coroutine |
| `LayoutGroup` + `ContentSizeFitter` nested | Double rebuild per frame; "dirty layout" warnings | Compute size manually or use a single owner |
| Hardcoded English strings | Blocks localisation | Route through a localisation table or LocaleString token |
| Anchors at centre with large offsets | Breaks at every other resolution | Re-anchor to the logical parent region |
| Pivot != intended scaling origin | Tween or layout snaps from the wrong point | Set pivot to the natural growth origin |
| Sprite Mesh Type = Full Rect on transparent edges | Overdraw waste | Set to Tight |
| Missing `EventSystem` GameObject | UI is non-interactive and silently broken | Add an `EventSystem` with the input module matching the project's input system: `InputSystemUIInputModule` (New Input System) or `StandaloneInputModule` (legacy input only). Never mix the two. |
| Multiple `EventSystem`s across loaded scenes | Race condition; input routing breaks | Keep one in a persistent boot scene |
| `Mask` for axis-aligned clip | Extra draw call vs `RectMask2D` | Replace with `RectMask2D` |
| No safe-area handling on mobile | UI clipped by notch/home-indicator | Add a `SafeAreaPanel` script under top Canvas |
| Touch targets < 44pt / 48dp | Mis-taps on real devices | Resize or add transparent padding to interactive area |
| Missing disabled / loading / empty / error states | Looks broken in real use | Add the missing states |

---

## When you must do something the rules say not to

The rules above optimise for the common case. Real projects have constraints. If you genuinely need to violate one:

1. State which rule you are violating.
2. State why the constraint forces it.
3. State the cost (perf, maintenance, accessibility) you are accepting.
4. Proceed.

Example: "Violating the 'no Animator on UI tweens' rule because the project's existing animation pipeline is Timeline-based and this screen needs to integrate with cutscene callbacks. Cost: ~0.2ms/frame Animator update on this screen. Mitigated by disabling the Animator when the screen is hidden."

That is acceptable. Silent violation is not.
