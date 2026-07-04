# Fork Changelog

Changes made in this **hardened fork** of [MCP for Unity](https://github.com/CoplayDev/unity-mcp), on top of the upstream release it was branched from. Upstream release notes remain authoritative for upstream features; this file records only the fork's additions. Full detail and rationale: [`docs/FABLE_UNITY_MCP_FORK_REVIEW.md`](docs/FABLE_UNITY_MCP_FORK_REVIEW.md).

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

### Skills & docs

- New Claude Code skills: `unity-health-check` (read-only project pulse) and a generic `unity-scene-setup`; a Safety Mode Awareness section added to the shipped `unity-mcp-skill` (and its dangling reference links repaired).
- `SECURITY.md` fork safety section; `docs/FABLE_UNITY_MCP_FORK_REVIEW.md` end-to-end review.

> **C# verification:** the new C# handlers (`ManageProject`, `EditorContext`, `ValidateBuild`) are review-clean but require running the EditMode tests in Unity (`Window → General → Test Runner`) to confirm compilation on your Editor version. The Python layer is fully unit-tested.

### Attribution

Original work © CoplayDev, MIT — preserved in [`LICENSE`](LICENSE). This fork is not affiliated with or endorsed by CoplayDev or Unity Technologies.
