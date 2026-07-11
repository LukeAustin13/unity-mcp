# Fork Changelog

Changes made in this **hardened fork** of [MCP for Unity](https://github.com/CoplayDev/unity-mcp), on top of the upstream release it was branched from. Upstream release notes remain authoritative for upstream features; this file records only the fork's additions. Full detail and rationale: [`docs/FORK_HARDENING_REVIEW.md`](docs/FORK_HARDENING_REVIEW.md).

## Unreleased (fork)

### Security & safety (Phase 2)

- **Safety modes** — every MCP tool call is classified (`read`/`validate`/`write`/`destructive`) and enforced against a configured mode (`read_only` / `review_only` / `write`, default `write`) at a single choke point. Set via `--safety-mode` / `UNITY_MCP_SAFETY_MODE`.
- **Closed the CLI/API bypass** — the local `/api/command` route (used by the CLI) now passes through the same enforcement layer (`core/enforcement.py`) as MCP tool calls; no ingress reaches Unity ungated.
- **Destructive-action acknowledgement** — destructive calls require `confirm: true` metadata in `write` mode (never relaxes the mode).
- **Execution-surface guards** (default off): `execute_code` default-denied (`UNITY_MCP_ALLOW_EXECUTE_CODE`), `execute_menu_item` allowlist-only (`UNITY_MCP_MENU_ITEM_ALLOWLIST` / `UNITY_MCP_ALLOW_ARBITRARY_MENU_ITEMS`), `manage_build` output-path sandbox (`UNITY_MCP_ALLOW_EXTERNAL_BUILD_OUTPUT`).
- **Key canonicalization** — argument keys are folded to snake_case (matched exactly to Unity's `ToSnakeCase`) before classification/guards, and ambiguous duplicate spellings are refused, closing a guard/handler key-parity bug class.
- **Payload-aware classification** for dual-mode actions (`manage_build` settings/platform/scenes/profiles, `manage_scene` validate+auto_repair, `manage_ui` render_ui, `manage_graphics` bake_get_settings, `manage_prefabs` close_prefab_stage+save) that mutate only when given a payload.
- **HTTP bind hardening** — non-remote-hosted HTTP refuses a non-loopback bind unless `UNITY_MCP_ALLOW_INSECURE_HTTP=1`.
- **Audit log** — every tool call recorded to `<log dir>/unity_mcp_audit.jsonl` (redacted argument summaries). Inspect with `unity-mcp audit tail` / `audit summary`.
- **Telemetry OFF by default** (opt in with `UNITY_MCP_TELEMETRY_ENABLED=1`).
- **Self-policing CI checks** — every registered tool must be explicitly classified; C#/Python action-list drift is caught in tests.
- Hardened via a 4-round adversarial review (12 confirmed bypasses fixed).

### Capabilities

- **`safety_status`** tool + `mcpforunity://server/safety` resource — an agent can read the active mode/guards before acting.
- **`run_tests_and_summarize`** — one call runs tests, waits, and returns a compact pass/fail + failing-test summary.
- **`manage_project`** (READ) — whole-project intelligence: `validate_assets` (missing scripts, broken prefabs, dangling serialized refs), `validate_materials` (missing/error/"pink" shaders), `asset_inventory`, and an aggregate `project_health` report. CLI: `unity-mcp project …`.
- **`get_editor_context`** resource (`mcpforunity://editor/context`) — one read unions editor state + selection + prefab stage + console error counts (fewer task-start round-trips).
- **`validate_build`** — READ-only build pre-flight (target support, build scenes, compile state, PlayerSettings) before a real build. CLI: `unity-mcp build validate`.
- Optional additive `properties` bags on `manage_gameobject` / `manage_ui` (backward-compatible).
- Fixed an agent-instruction bug (agents were told to poll `editor_state.isCompiling`; the real field is `compilation.is_compiling`).

### Agentic loop & perception (Phase 3)

- **`manage_checkpoint`** — named scene-state snapshots: `create`/`list`/`restore`/`delete` under `Library/McpCheckpoints/`. Snapshot the open scenes, experiment, roll back. Restore overwrites the original scene files with the checkpointed state and is classified **destructive** (requires `confirm: true` in `write` mode; blocked below it). Checkpoint ids are strictly validated — user input can never become a filesystem path. CLI: `unity-mcp checkpoint …`.
- **`play_smoke_test`** (validate) — one call: enter play mode, run N seconds, collect errors/exceptions (job survives the domain reload via SessionState + `[InitializeOnLoad]`), exit play, return a pass/fail verdict. CLI: `unity-mcp playtest run`.
- **`query_scene`** (read) — filter + projection over all loaded scenes in one paged call: name/tag/layer/component/root-path filters; `transform`, `world_bounds` (shape dimensions), `components`, `materials`, `mesh_stats` projections. CLI: `unity-mcp query scene`.
- **Dependency X-ray on `manage_project`** (read) — `get_dependencies`, `find_references` (reverse lookup), advisory `unused_assets` with explicit caveats (static analysis can't see Addressables/bundles/reflection; never deletes). CLI: `unity-mcp project deps/refs/unused`.
- **`audit_mobile` on `manage_project`** (read) — 18 data-driven mobile-performance rules (texture/audio/model import settings, IL2CPP/ARM64/graphics APIs, quality settings, scene red flags), each finding with severity + concrete fix. CLI: `unity-mcp project audit-mobile`.
- **Tiered visual audit on `manage_camera`** — `visibility_report` (read): frustum/screen-rect/coverage/occlusion facts with zero pixels; `screenshot_compare` (validate): numeric pixel-diff vs a baseline (changed %, diff bounding box) — no images shipped; inline screenshots right-sized to 1280 px by default (full-res file on disk unchanged; JPEG + object-crop options). Capture/report actions are payload-aware classified so they work outside `write` mode.
- **`read_console` `format="summary"`** — deduped/grouped console output (counts + sample + file:line), cutting token burn on error floods.
- **Test triage on `run_tests_and_summarize`** — optional failing-line source snippets (`include_source_context`) and flaky-vs-deterministic classification (`retry_failed`, re-runs only the failed tests).

### Content validation (Phase 3)

All three are READ-classified, usable in `read_only` mode, strictly non-mutating, and fail closed on unknown actions/keys:

- **`audit_ui_layout`** (read) — audits uGUI layout across a configurable resolution matrix (default 720p/1080p/1440p/ultrawide) using pure analytic math — no GameView switching, nothing dirtied. Detects off-screen/overlapping/tiny/clipped/invisible/blocked interactive UI, suspicious anchors, CanvasScaler issues, listeners-less buttons, missing EventSystem/GraphicRaycaster. Layout-group-driven or heuristic findings are marked `advisory`. Facts only, no screenshots. CLI: `unity-mcp ui audit-layout`.
- **`validate_scene_contracts`** (read) — validates a loaded scene against a JSON contract (required/forbidden objects, required components, max cameras/lights, missing-reference ban, tags/layers, build-settings inclusion, UI requirements). Unknown contract keys are refused — typo'd rules can't silently pass. Never opens scenes. CLI: `unity-mcp scene validate-contracts`.
- **`prefab_health` on `manage_project`** (read) — deep prefab validation: missing scripts/references, broken variants, duplicate components, undefined tags/unnamed layers, material/shader issues, disabled colliders/renderers, nested-prefab depth, oversized bounds, dependencies (single-prefab mode). GUID-cursor paged, per-check fault isolation. CLI: `unity-mcp project prefab-health`.

### Skills & docs

- **Portable agent skills** — a drag-and-drop [`skills/`](skills/README.md) folder for projects that consume this MCP, all built/verified against the current tool surface. `unity-mcp-operator` is the base operating layer (posture-first planning, cheapest-evidence-first, verify-then-claim); domain skills cover diagnosis (`unity-project-doctor`), senior audit (`unity-review`), planning (`unity-plan`), scene contracts (`unity-scene-contracts`), UI audit (`unity-ui-auditor`), debugging (`unity-debug`), the edit-and-verify loop (`unity-edit-verify`), checkpoint-based safe iteration (`unity-safe-iteration`), UI design/build (`unity-ui-design`), and testing (`unity-test-run`, `unity-test-pilot`), plus two adapt-before-use project-specific examples (`sw-rules-guard`, `unity-scene-rebuild`). Copy any folder into your project's `.claude/skills/`; plain markdown, client-agnostic. Legacy skills were audited and modernized against the live tool signatures (every tool name / action / parameter / safety class verified, then adversarially re-checked) — removing stale calls (`refresh_unity`-after-edit, `tool_search`, `find_in_file` globs, non-existent actions/params) and adding the fork's safety-posture and tool-group-activation discipline.
- New Claude Code skills: `unity-health-check` (read-only project pulse) and a generic `unity-scene-setup`; a Safety Mode Awareness section added to the shipped `unity-mcp-skill` (and its dangling reference links repaired).
- `SECURITY.md` fork safety section; `docs/FORK_HARDENING_REVIEW.md` end-to-end review.

> **C# verification:** the new/extended C# handlers (`ManageProject`, `EditorContext`, `ValidateBuild`, `ManageCheckpoint`, `PlaySmokeTest`, `QueryScene`, `ScreenshotCompare`/`VisibilityReport` + `ScreenshotUtility` changes, `AuditUiLayout`, `ValidateSceneContracts`) are review-clean but require running the EditMode tests in Unity (`Window → General → Test Runner`) to confirm compilation on your Editor version. The Python layer is fully unit-tested (1798 passing).

### Attribution

Original work © CoplayDev, MIT — preserved in [`LICENSE`](LICENSE). This fork is not affiliated with or endorsed by CoplayDev or Unity Technologies.
