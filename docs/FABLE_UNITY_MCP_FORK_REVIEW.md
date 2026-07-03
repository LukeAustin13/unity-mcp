# Fork Review: Hardened Unity MCP

**Status:** Phase 1 review + Phase 2a (modes/classification/audit) + Phase 2b (bypass closure + surface hardening) landed — see §9
**Upstream:** [CoplayDev/unity-mcp](https://github.com/CoplayDev/unity-mcp) (MIT)
**Scope:** End-to-end audit of the Python MCP server and the Unity C# editor package, a threat model, and a guardrail/reliability plan for turning this into a Unity MCP that is safe to run against real projects across PC, mobile, prototype, tool, and release-build workflows.

This document is deliberately blunt. Where the current implementation is unsafe, it says so and why. Where a "hardening" idea is over-engineered, it is rejected. The goal is a bridge you can trust with delete, build, and package-install power — not a demo.

---

## 0. TL;DR — what matters most

The upstream project is a capable, well-structured bridge with more surface area than a trustworthy agent target should expose by default. The three things that made it risky on a real project — **all now addressed** (Phase 2a/2b, §9):

1. **The agent had delete/build/package-install/arbitrary-code power with no server-side gate.** Classification hints existed (`destructiveHint=True`) but nothing *acted* on them. → Now every ingress (MCP tools **and** the CLI's `/api/command` route) passes through one enforcement layer with safety modes, destructive classification, acknowledgement gating, and an audit log.
2. **`execute_code` and `execute_menu_item` were unbounded execution surfaces.** → `execute_code` is now default-denied (opt-in config + `write` + acknowledgement); `execute_menu_item` is allowlist-only.
3. **`manage_build` accepted an unvalidated absolute output path.** → Output paths are now sandboxed to the project (traversal + absolute/external rejected) unless explicitly allowed.

The architecture is sound and the fork built on it, not rewrote it. Remaining residual risks are listed honestly in Appendix B (chiefly: a C#-side path canonicalisation for defence-in-depth, HTTP bearer-token auth, and keeping the classification table current).

---

## 1. Architecture map

### 1.1 Two codebases, one system

```
AI assistant (Claude / Cursor / Windsurf)
      │  MCP protocol (stdio or HTTP)
      ▼
Python MCP server  ── Server/src/
      │  WebSocket hub (/hub/plugin)  ── HTTP transport
      │  legacy TCP framed bridge     ── stdio transport
      ▼
Unity Editor package  ── MCPForUnity/
      │  Unity Editor API (main thread)
      ▼
Scenes · Assets · Scripts · Build pipeline · Package manager
```

### 1.2 Python MCP server (`Server/src/`)

| Concern | Location |
|---|---|
| Entry point / arg parsing / transport selection | [`main.py`](../Server/src/main.py) `main()` |
| FastMCP app construction, middleware, route mounting | [`main.py`](../Server/src/main.py) `create_mcp_server()` |
| Global config (dataclass) | [`core/config.py`](../Server/src/core/config.py) |
| Tool registration decorator + group table | [`services/registry/tool_registry.py`](../Server/src/services/registry/tool_registry.py) |
| Tool auto-discovery + decorator stack | [`services/tools/__init__.py`](../Server/src/services/tools/__init__.py) `register_all_tools()` |
| Resource registration | [`services/resources/__init__.py`](../Server/src/services/resources/__init__.py) |
| MCP tools (one file per domain) | [`services/tools/`](../Server/src/services/tools/) (~44 tools) |
| Resources (read-only state) | [`services/resources/`](../Server/src/services/resources/) (~25 resources) |
| CLI (Click) | [`cli/`](../Server/src/cli/) |
| Instance-routing middleware | [`transport/unity_instance_middleware.py`](../Server/src/transport/unity_instance_middleware.py) |
| HTTP WebSocket hub | [`transport/plugin_hub.py`](../Server/src/transport/plugin_hub.py) |
| Legacy TCP bridge | [`transport/legacy/unity_connection.py`](../Server/src/transport/legacy/unity_connection.py) |
| Telemetry | [`core/telemetry.py`](../Server/src/core/telemetry.py) |

The server is **three layers that are not generated from each other** (per `CLAUDE.md`): FastMCP tools, Click CLI commands, and FastMCP resources. All three ultimately reach the same C# `HandleCommand` methods, but through different transports (tools/resources via WebSocket or legacy TCP; CLI via the local HTTP `/api/command` route).

### 1.3 Unity C# editor package (`MCPForUnity/`)

| Concern | Location |
|---|---|
| Command discovery + dispatch | `MCPForUnity/Editor/Tools/CommandRegistry.cs` |
| Main-thread marshalling / per-frame queue | `MCPForUnity/Editor/Services/Transport/TransportCommandDispatcher.cs` |
| WebSocket client to Python | `MCPForUnity/Editor/Services/Transport/Transports/WebSocketTransportClient.cs` |
| Port registry (`~/.unity-mcp/…`) | `MCPForUnity/Editor/Helpers/PortManager.cs` |
| Tools (one class per domain) | `MCPForUnity/Editor/Tools/Manage*.cs`, `Execute*.cs` |
| Resources | `MCPForUnity/Editor/Resources/` |
| Path sandbox helpers | `MCPForUnity/Editor/Helpers/AssetPathUtility.cs`, `ManageScript.TryResolveUnderAssets()` |
| Secure key store (OS credential vault) | `MCPForUnity/Editor/Security/SecureKeyStore/` |
| EditorPrefs config keys | `MCPForUnity/Editor/Constants/EditorPrefKeys.cs` |
| Response shapes | `MCPForUnity/Editor/Helpers/Response.cs` |

C# tools are discovered by reflection over `[McpForUnityTool]` (all `AutoRegister = false`, i.e. opt-in). Handlers are `public static HandleCommand(JObject)`; a `Task` return type switches the dispatcher to an async `EditorApplication.update`-polled path with a `TaskCompletionSource`. Every command runs on the Unity main thread.

### 1.4 Transport model

- **stdio** — one Python process per client. Legacy framed TCP to Unity on port 6400 (per-project port file at `~/.unity-mcp/unity-mcp-port-{hash}.json`). Single-agent; new connections stomp old ones. No auth (localhost, single user assumed).
- **HTTP** — one shared Python server, FastMCP HTTP transport, Unity plugins connect out to a WebSocket hub at `/hub/plugin`. Multi-agent capable; sessions isolated by `client_id` (or `user_id` in remote-hosted mode). Default bind `127.0.0.1`. Remote-hosted mode requires an API-key validation URL and enforces per-user instance isolation.

### 1.5 Command / resource registration

- **Python tools**: `@mcp_for_unity_tool(description=…, group=…, unity_target=…)` appends a dict to a module-global registry ([`tool_registry.py:99`](../Server/src/services/registry/tool_registry.py)). At startup `register_all_tools()` imports every module under `tools/`, then wraps each function `log_execution → telemetry_tool → mcp.tool`. `group` becomes a FastMCP tag `group:<name>`; only `core` is enabled by default, the rest are toggled via `manage_tools`.
- **C# tools/resources**: reflection over assemblies for `[McpForUnityTool]` / `[McpForUnityResource]`, PascalCase→snake_case name mapping, per-tool/-resource enable flags in EditorPrefs enforced in the dispatcher.

### 1.6 Middleware chain (interception points)

FastMCP middleware runs in registration order. **After this PR** the order is:

1. `SafetyMiddleware` (new) — classify, enforce mode, strip `confirm`, audit.
2. `UnityInstanceMiddleware` — resolve/route `unity_instance`, strip it from args, filter tool listing.
3. Per-tool decorator stack — `log_execution` (full arg logging), `telemetry_tool`.

This chain is the single most important architectural fact for hardening: **it is the one place every tool call funnels through**, so guardrails belong here, not sprinkled across 44 tool files.

### 1.7 CLI overlap

The Click CLI in `cli/commands/` mirrors the tools but is a **separate implementation** that talks to Unity through the local HTTP route `/api/command` in [`main.py`](../Server/src/main.py). **This route bypasses the FastMCP middleware chain entirely.** Any guardrail added as middleware does *not* cover the CLI path. See §4.9.

### 1.8 Current trust boundaries

| Boundary | Trust assumption today | Reality |
|---|---|---|
| Agent → Python server | Agent is fully trusted | Agent is influenced by project content it reads → prompt injection is in scope |
| Python → Unity (localhost) | Loopback = safe | True for stdio single-user; the bridge itself has no per-message auth |
| Unity plugin → Python (HTTP) | API key in remote-hosted mode only | Local HTTP mode has no plugin auth; `0.0.0.0` bind is a footgun |
| Tool → filesystem | `Assets/`-relative sandbox | Enforced for scripts/assets; **not** for `manage_build` output path |
| `execute_code` → Editor process | Opt-out blocklist | Not a sandbox; reflection bypass is trivial |

The load-bearing assumption is **"the agent is trusted."** That is exactly the assumption a production Unity MCP cannot make, because the agent reads project files, asset labels, and console text that an attacker can author.

---

## 2. Licence and fork hygiene

### 2.1 MIT licence — preserved ✓

Root [`LICENSE`](../LICENSE) and [`Server/LICENSE`](../Server/LICENSE) are both MIT with the verbatim line:

```
Copyright (c) 2025 CoplayDev
```

**Do not alter or remove this line.** MIT requires the copyright notice and permission text be retained in all copies and substantial portions. A fork adds attribution; it never replaces the upstream notice.

### 2.2 Copyright notice — preserved ✓

No copyright headers in individual source files to worry about; the notice lives only in the two `LICENSE` files. History is intact (1,578 commits from 2025-03-18, `upstream` remote points at CoplayDev). This is a genuine fork, not a re-clone — preserve it that way.

### 2.3 Third-party dependencies

**Python** ([`Server/pyproject.toml`](../Server/pyproject.toml)): `httpx`, `fastmcp (>=3.0.2,<4)`, `mcp`, `pydantic`, `tomli`, `fastapi`, `uvicorn`, `click` — all permissively licensed (MIT/BSD/Apache-2.0). Dev: `pytest`, `pytest-asyncio`, `pytest-cov`.

**Unity** (`MCPForUnity/package.json`): Unity built-in modules + `com.unity.nuget.newtonsoft-json` (3.0.2, MIT) + `com.unity.test-framework`. No vendored third-party source outside the UPM package graph.

No copyleft (GPL/LGPL) dependencies. No licence conflict with re-licensing the fork under MIT.

### 2.4 Branding / trademark risks

| Item | Current value | Risk |
|---|---|---|
| UPM package id | `com.coplaydev.unity-mcp` | Renaming changes the install identity and breaks upstream sync. **Keep it** unless you intend a hard split. |
| Display name | `MCP for Unity` | Fine; generic-descriptive. |
| Telemetry endpoint | `https://api-prod.coplay.dev/telemetry/events` | **Phones home to Coplay by default.** A fork that repositions on trust must not silently send tool-usage data to a third party. Change the default, or default telemetry off. |
| Disclaimer | README: "not affiliated with Unity Technologies" | Good — keep it. |

**Unity trademark:** "Unity" is a trademark of Unity Technologies. Using it descriptively ("MCP for Unity", "Unity Editor") is nominative fair use. **Do not** adopt a name that implies endorsement (e.g. "Unity Official MCP", "Unity-certified"). The existing disclaimer covers this; keep it verbatim in any README you ship.

### 2.5 Recommended README attribution wording

> This project is a hardened fork of [MCP for Unity](https://github.com/CoplayDev/unity-mcp) by CoplayDev, used under the MIT License. The original copyright notice is preserved in [`LICENSE`](LICENSE). This fork is not affiliated with or endorsed by CoplayDev or Unity Technologies.

(Landed in the README under "About this fork" in this PR.)

### 2.6 Recommended neutral fork name

Pick a name that does **not** contain "Unity" as the leading noun and does not imply official status. Candidates:

- **`sentinel-mcp-for-unity`** — signals the hardening/guardrail identity.
- **`warden-unity-mcp`** — same, shorter.
- **`unity-agent-bridge (hardened)`** — descriptive, no vendor implication.

Recommendation: keep the technical package id (`com.coplaydev.unity-mcp`) for sync compatibility, but present the fork as **"Hardened Unity MCP"** in docs/README. Do not rename the world; rename the *positioning*. (Matches the "do not mass-rename" rule.)

---

## 3. Security threat model

Primary adversary: **a prompt-injected or misaligned agent** driving otherwise-legitimate tools. Secondary: **a network attacker** reaching the bridge. The agent is *not* assumed hostile by intent, but *is* assumed to act on untrusted project content.

| # | Surface | Vector | Current state | Severity |
|---|---|---|---|---|
| 3.1 | **Filesystem writes** | `manage_script`, `manage_asset`, `manage_texture`, `manage_shader` create/modify files | Sandboxed to `Assets/` via `AssetPathUtility.SanitizeAssetPath` + `TryResolveUnderAssets` (rejects `..` and symlinks). Solid. | Low |
| 3.2 | **Script generation/modification** | Agent writes arbitrary C# that compiles and runs in-Editor on next reload | No content review; any code the agent writes executes. This is inherent to the tool's purpose but means "write a script" ≈ "run code". | High |
| 3.3 | **Scene mutation** | `manage_scene` save/close, `manage_gameobject` delete | No undo guarantee, no confirmation. Deleting a root GameObject and saving is unrecoverable. | Medium |
| 3.4 | **Prefab mutation** | `manage_prefabs` modify/save | Same as scenes; overwrites are silent (`allow_overwrite`). | Medium |
| 3.5 | **Build execution** | `manage_build` action=`build`/`batch` | Runs the full build pipeline → executes `IPreprocessBuild` hooks (arbitrary project code) and writes to an **unvalidated absolute `output_path`**. | High |
| 3.6 | **Package installation** | `manage_packages` add/embed/registry | Installing a package runs its code on import; adding a scoped registry can pull arbitrary packages. No allowlist. | High |
| 3.7 | **Arbitrary command execution** | `execute_code` (compile+run C#), `execute_menu_item` (any menu path) | `execute_code` blocklist is opt-out and reflection-bypassable; `execute_menu_item` blocks only `File/Quit`. | Critical |
| 3.8 | **Prompt injection via project content** | Malicious text in asset labels, script comments, console output that the agent reads via resources/tools, then acts on | No mitigation; the agent treats read content as instructions. This is the *root* reason guardrails are needed. | High |
| 3.9 | **Remote access** | HTTP transport bound to `0.0.0.0`; no TLS in local HTTP mode | Local HTTP has no plugin auth; a `--http-host 0.0.0.0` exposes every enabled tool on the LAN. | High if misconfigured |
| 3.10 | **Multi-client** | Multiple agents on the HTTP hub | Session isolation by `client_id` exists; but all sessions share one safety posture and one Unity Editor — a destructive call from any client hits the shared project. | Medium |
| 3.11 | **Destructive Unity operations** | `delete` actions across asset/gameobject/script/scene, `deploy_package`/`restore_package` | No server-side gate before this PR. | High |

### Notable specifics (verified in source)

- **`execute_code`** ([`execute_code.py:48`](../Server/src/services/tools/execute_code.py)): `safety_checks` defaults true but is a caller-supplied boolean — the agent can pass `safety_checks=false`. Even when true, the C# side's blocklist is pattern-based (`File.Delete`, `Process.Start`, …) and its own docstring admits "Not a full sandbox — advanced bypass is possible." Reflection (`typeof(File).GetMethod("Delete")`) sidesteps it.
- **`execute_menu_item`**: only `File/Quit` is blocked in C#. `File/Build And Run`, asset deletion menus, and third-party tool menus are all reachable.
- **`manage_build`** free-form `output_path` with no sandbox check is the single unguarded file-write path.
- **`manage_editor` deploy_package/restore_package** overwrite the *installed MCP package files* and trigger a recompile, with "no confirmation dialog" per its own description.

---

## 4. Guardrail design

Design principle: **one enforcement point, explicit classification, fail-closed on the unknown.** Guardrails live in the middleware chain (§1.6), not in individual tools, because the tool set changes and per-tool checks rot.

### 4.1 Safety modes (implemented in this PR)

Three modes, config-driven, default `write` (backward-compatible):

| Mode | Allows | Use case |
|---|---|---|
| `read_only` | READ only | Let an agent inspect/answer questions about a project with zero mutation risk. |
| `review_only` | READ + VALIDATE | Inspection **plus** running tests, refresh, play-mode, screenshots — a "CI reviewer" that can validate but not edit. |
| `write` | everything; **DESTRUCTIVE requires `confirm: true`** | Normal agent-assisted development. |

Set via (highest priority first): `--safety-mode` CLI flag → `UNITY_MCP_SAFETY_MODE` env → `config.safety_mode` (default `write`). Invalid values fail startup (CLI/env) or fail closed to `read_only` at runtime.

### 4.2 Destructive-action classification (implemented)

Four classes in [`core/safety.py`](../Server/src/core/safety.py), keyed by tool **and** by the `action` sub-argument:

- **READ** — no state change (`find_gameobjects`, `manage_asset action=search`, `manage_scene action=get_hierarchy`, all resources by nature).
- **VALIDATE** — transient editor state only (`run_tests`, `refresh_unity`, `manage_editor action=play`, screenshots).
- **WRITE** — persistent, ordinary edits (`create_script`, `manage_asset action=create`, `manage_scene action=save`).
- **DESTRUCTIVE** — deletes data / runs code / builds / installs packages (`*.delete`, `execute_code`, `execute_menu_item`, `manage_build action=build`, `manage_packages action=add_package`, `deploy_package`).

`batch_execute` is classified as the **most severe class among its nested commands**, so a batch cannot smuggle a `delete` past the gate. Unknown tools and unknown actions fall back to **WRITE** (blocked outside `write` mode, but no confirm friction) — new tools are safe-by-default until explicitly classified.

### 4.3 Read-only / review-only / write enforcement (implemented)

`SafetyMiddleware.on_call_tool` ([`transport/safety_middleware.py`](../Server/src/transport/safety_middleware.py)) classifies every call and blocks anything outside the mode's allowed classes with a clear `ToolError` naming the class and how the operator can change the mode. Blocked calls never reach instance discovery or Unity.

### 4.4 Destructive-action confirmation policy (implemented)

In `write` mode, a DESTRUCTIVE call must carry `"confirm": true` in its arguments. Without it the call is refused with a message instructing the agent to re-send with confirmation. This turns "delete my scene" from a silent action into a two-step, auditable intent. The reserved `confirm` key is **stripped before FastMCP validates the tool schema**, so no tool signature changes and no schema bloat (same technique the codebase already uses for `unity_instance`).

Rationale for *metadata* confirmation rather than an interactive dialog: the agent operates headless; a blocking Editor dialog would deadlock automation. Requiring an explicit second call with `confirm: true` gives the same "are you sure" gate in a machine-driven way and leaves an audit trail of the confirmed intent.

### 4.5 Tool allowlist / denylist (recommended, Phase 2 follow-up)

The mode system is coarse. Add an optional per-tool/per-action allowlist so an operator can, e.g., run `write` mode but deny `execute_code`, `execute_menu_item`, and `manage_packages` entirely. Recommended shape: `UNITY_MCP_TOOL_DENY="execute_code,execute_menu_item,manage_build:build"`. Enforce in the same middleware, before mode classification. **Ship `execute_code`/`execute_menu_item` in the deny list by default.**

### 4.6 Project-root sandbox enforcement (recommended)

Close the `manage_build` gap: validate `output_path` resolves under the project root or a configured builds directory before dispatch. This is a C#-side fix (`ManageBuild.cs`) plus a server-side pre-check. Until then, `manage_build action=build` is correctly classified DESTRUCTIVE so it is at least gated and audited.

### 4.7 Audit logging (implemented)

Every tool call writes one JSONL record ([`core/audit.py`](../Server/src/core/audit.py)) to `<log dir>/unity_mcp_audit.jsonl`: timestamp, tool, action, classification, known-tool flag, mode, decision (allow/block/confirm_required), client key, an **argument summary with values truncated to 200 chars** (so the log is not a second copy of every script edit), and outcome (ok/blocked/error). Disable with `UNITY_MCP_AUDIT_LOG=0`; relocate with `UNITY_MCP_AUDIT_LOG_DIR`. Audit failures never break a tool call.

### 4.8 Structured command receipts (recommended)

The audit record *is* the receipt for the operator. For the *agent*, add an optional `receipt` block on destructive responses (tool, action, target, undo-availability) so the agent can report exactly what it changed. Low priority; the audit log covers the trust need.

### 4.9 Optional auth token for non-local transports (recommended)

Two gaps:
1. **Local HTTP `/api/command`** (the CLI path) bypasses the middleware chain — guardrails do **not** apply. Either route it through the same classification, or restrict it to loopback and document that CLI = full trust.
2. **HTTP transport** should support a static bearer token even in non-remote-hosted local mode, so binding beyond loopback is not wide open. Refuse to bind non-loopback without a token.

### 4.10 Safe defaults (recommendations)

- Telemetry **off** by default in the fork (§2.4).
- `execute_code`, `execute_menu_item` in the default **deny list**.
- Refuse non-loopback HTTP bind without an auth token.
- Keep the default mode `write` (compatibility) **but** document `review_only` as the recommended mode for untrusted/CI usage, and make the mode visible in startup logs (done).

---

## 5. Reliability plan

| Concern | Current state | Recommendation |
|---|---|---|
| **Command timeouts** | Fast-fail commands (`read_console`, `ping`) at ~2s; regular at ~30s; `reload_max_retries` ≈10s window; builds poll to 30 min | Keep; expose per-call timeout override for long asset imports. |
| **Cancellation** | `run_tests`/`manage_build` have `cancel` actions; profiler/bake cancellable | Good. Ensure cancel is itself classified VALIDATE (it is, via defaults) so it works in `review_only`. |
| **Reconnect** | C# client: exponential backoff (0/1/3/5/10/30s), keep-alive 15s | Solid. |
| **Unity instance routing** | Middleware resolves `Name@hash`/hash-prefix/port; auto-selects sole instance | Solid; the multi-instance ambiguity errors are clear. |
| **Command queue** | Main-thread `EditorApplication.update` queue with re-entry guard | Sound; single-threaded by design. |
| **Compile-error handling** | `preflight()` can wait for no-compile; agent instructed to poll `editor_state.isCompiling` and `read_console` | Good pattern; keep enforcing "read_console after script writes". |
| **Editor-state awareness** | `editor_state` resource + server-side `preflight` busy/retry | Solid. |
| **Deterministic test execution** | `e2e-bridge.yml` boots a headless Editor and runs fixed sequences without an LLM | Excellent; the safety slice adds no non-determinism (middleware is pure given mode+args). |
| **Structured errors** | `{success, error, message, data, hint}`; `hint="retry"` on reload | Consistent. Safety blocks reuse `ToolError` so clients see a normal tool error. |

No reliability regressions from the Phase 2 slice: the middleware is synchronous, allocation-light, and short-circuits before any I/O on a block. Full suite: **1349 passed, 2 skipped**.

---

## 6. Unity workflow improvements

### 6.1 Platform-neutral capabilities (mostly present; classification lets them shine in `review_only`)

| Capability | Tool/Resource today | Gap / action |
|---|---|---|
| Run EditMode tests | `run_tests(mode=EditMode)` | Present. VALIDATE-classed → available in `review_only`. |
| Run PlayMode tests | `run_tests(mode=PlayMode)` | Present. |
| Read console errors | `read_console` | Present (READ; `clear`=WRITE). |
| Inspect current scene | `manage_scene get_hierarchy`, `editor_state`, `selection` resources | Present (READ). |
| List build scenes | `manage_scene get_build_settings` | Present (READ). |
| Validate build settings | partial (`manage_build settings`) | Add a dedicated **validate-only** build-settings check (no build) → VALIDATE. |
| Inspect packages | `manage_packages list/get_info/search` | Present (READ). |
| Find missing references | — | **New validator (Phase 5).** READ-only scan for missing `MonoScript`/broken component refs. |
| Find broken serialized fields | — | **New validator (Phase 5).** |
| Validate prefabs | `manage_prefabs get_info` + new check | Add a READ-only prefab integrity pass. |
| Validate materials/shaders | `manage_material get_material_info` | Add a READ-only shader-error/missing-property scan. |
| Inspect assets by type/path/GUID | `manage_asset search/get_info` | Present (READ). |
| **Project health report** | — | **New aggregate resource (Phase 3):** missing refs + console errors + broken prefabs + failing tests, machine-readable. All READ → runs in `read_only`. |

The pattern for all new validators: **READ-classified, structured JSON output, paged.** They become the backbone of a trustworthy "review" agent that runs in `review_only` and cannot mutate anything.

### 6.2 Build-target support (platform-neutral, not mobile-specific)

`manage_build` already maps `windows64, osx, linux64, android, ios, webgl, uwp, tvos, visionos`. Treat every target as one option among many:

| Target | Status | Notes |
|---|---|---|
| Windows | Supported | Standard. |
| Linux | Supported | Standard. |
| macOS | Supported | Standard. |
| Android | Supported | SDK/NDK must be configured; keep as *a* target, not the identity. |
| WebGL | Supported | Long build; ensure timeout override. |
| iOS | **Document as a later phase** | Signing/provisioning is out of scope for automated agent builds; document the manual Xcode step rather than pretend to automate it. |

All build actions are DESTRUCTIVE-classed (they run build hooks and write artifacts), so agent-driven builds require `confirm: true` and are audited — appropriate for release-build workflows.

---

## 7. Agent interface improvements

| Improvement | Finding | Action |
|---|---|---|
| **Smaller tool schemas** | Several tools are 10–20 parameter mega-signatures (`manage_gameobject`, `manage_texture`, `manage_ui`). | Don't split gratuitously (violates domain symmetry), but move rarely-used params into a `properties` dict where the pattern already exists. |
| **Stricter parameter validation** | `manage_build`, `manage_packages`, `manage_graphics`, `manage_profiler`, `manage_camera`, `manage_vfx`, `manage_probuilder` take **free-string `action`**. | ✅ **Done (§9.6):** all seven already validate against `ALL_ACTIONS` and reject unknown/empty actions before dispatch; now covered by tests. Kept runtime validation over a `Literal` schema to avoid rejecting valid actions if the Python list drifts from C#; `Literal` migration gated on a superset-check CI job. |
| **Better resources** | Resources are good and READ-only. | Add the project-health resource (§6.1). |
| **Clearer tool descriptions** | Descriptions are thorough but bury destructive semantics in prose (`deploy_package` "no confirmation dialog"). | Lead each destructive action's description with its classification, now that classification is authoritative. |
| **Fewer "do anything" tools** | `execute_code`, `execute_menu_item`, `batch_execute` are the ambiguous-power tools. | ✅ **Done (§9.3–9.5):** `execute_code` default-denied, `execute_menu_item` allowlist-only, `batch_execute` guards recurse into members. |
| **Better error messages** | Safety blocks now say exactly *why* and *how to proceed*. | Keep this style for future validators. |
| **Stable machine-readable outputs** | Consistent `{success, …}` envelope. | Keep JSON as the source of truth. |
| **Markdown as presentation only** | Tools return dicts, not markdown. | Preserve; never make a human-readable string the authoritative field. |

The single highest-leverage interface fix is **constraining `action` to `Literal` on the seven free-string tools** — it improves both agent reliability and the precision of the safety classifier.

---

## 8. Implementation roadmap

| Phase | Goal | Status |
|---|---|---|
| **1** | Audit, tests baseline, docs, architecture map | ✅ This document; full suite green (1349 passed). |
| **2a** | Safety modes + destructive classification + confirmation + audit logging | ✅ Landed (`core/safety.py`, `core/audit.py`, `transport/safety_middleware.py`, tests). |
| **2b** | Close bypasses + harden execution surfaces (this pass) | ✅ **Landed** — CLI/API bypass closed via shared `core/enforcement.py`; `manage_build` path sandbox; `execute_code` default-denied; `execute_menu_item` allowlist; free-string `action` validation confirmed + tested; telemetry default OFF. Full suite green (**1416 passed, 2 skipped**). See §9. |
| **3** | Structured resources + project health report | ⬜ New READ-only validators + aggregate `project-health` resource. |
| **4** | Test/build automation | ⬜ First-class EditMode/PlayMode/build workflows across Windows/Linux/macOS/Android/WebGL; iOS documented as manual. |
| **5** | Advanced validators | ⬜ Missing references, broken serialized fields, prefab/material/shader integrity. |
| **6** | Packaging + release docs | ⬜ Fork README/positioning, safe-defaults doc, versioning, release process. |

---

## 9. Phase-2b completion notes (this pass)

This pass closed the bypasses and hardened the most dangerous execution surfaces identified in the prior residual-risk list. Design rule throughout: **one enforcement point, no duplicated policy, fail-closed defaults, non-breaking to valid usage.**

### 9.1 Single enforcement layer — the CLI/API bypass is closed

The prior implementation gated only FastMCP tool calls (via `SafetyMiddleware.on_call_tool`). The local **`/api/command`** HTTP route — the path the Click CLI uses — dispatched straight to `PluginHub.send_command`, **bypassing every guardrail**. That was the single most important hole.

Fix: policy now lives in one place, [`core/enforcement.py`](../Server/src/core/enforcement.py) `enforce_tool_call()`, called from **both** ingress paths:
- `SafetyMiddleware` (MCP tools) — [`transport/safety_middleware.py`](../Server/src/transport/safety_middleware.py) is now a thin adapter.
- The `/api/command` route — [`main.py`](../Server/src/main.py) calls `enforce_tool_call(...)` before session resolution or dispatch, covering both the normal and `execute_custom_tool` branches, and returns **HTTP 403** on a `PolicyViolation`.

I mapped every command ingress to confirm this is complete: all MCP tools funnel through the middleware; resources are read-only (`on_read_resource` is not a mutation surface); the only non-middleware dispatcher to Unity is `/api/command`, now gated. Proven end-to-end by [`test_api_command_enforcement.py`](../Server/tests/test_api_command_enforcement.py), which drives the real FastMCP HTTP app in an isolated subprocess and asserts a `read_only` delete is refused with 403 *before* the route looks for Unity, while an allowed read passes the gate and only then 503s.

### 9.2 `manage_build` output-path sandbox

`build_output_guard` (in [`core/safety.py`](../Server/src/core/safety.py)) rejects, for `build`/`batch` actions:
- **Path traversal** — any `..` segment (checked even when external output is allowed).
- **Absolute/external paths** — POSIX roots, Windows drive letters, UNC, and `~` expansion, host-independently (a Windows path is rejected even on a Linux server).

Relative, in-project paths pass. Escaping is opt-in via `allow_external_build_output` (`UNITY_MCP_ALLOW_EXTERNAL_BUILD_OUTPUT=1`). Enforced centrally, so the CLI path is covered too. This is a syntactic sandbox that needs no Unity round-trip; a deeper C#-side project-root check remains a follow-up.

### 9.3 `execute_code` — default-denied

`execute_code` is DESTRUCTIVE-classed (blocked in `read_only`/`review_only`) **and** additionally gated by `execute_code_guard`: its code-running actions (`execute`, `replay`, or a missing action) are refused unless `allow_execute_code` (`UNITY_MCP_ALLOW_EXECUTE_CODE=1`) is set — **even with `confirm: true`**. In `write` mode it therefore requires *both* explicit config enablement *and* the destructive acknowledgement. History reads/clears are ungated (harmless). The block message states plainly that it is not sandboxed.

### 9.4 `execute_menu_item` — allowlist model

`execute_menu_item_guard` denies arbitrary menu execution by default. Only paths on a conservative built-in allowlist (`DEFAULT_MENU_ITEM_ALLOWLIST` — window-opening items that cannot mutate data) or on the operator's `menu_item_allowlist` are permitted; everything else is refused regardless of `confirm`. `allow_arbitrary_menu_items` (`UNITY_MCP_ALLOW_ARBITRARY_MENU_ITEMS=1`) restores the old behaviour for those who need it. It remains DESTRUCTIVE-classed, so allowlisted items still require acknowledgement in `write` and are blocked in inspect modes.

### 9.5 Batch guard recursion

`batch_execute` cannot smuggle a guarded call: `_guard_reason` recurses into nested batch members, so a batch containing `execute_code`, an external build path, or a non-allowlisted menu item is refused with a message naming the offending nested tool. (Classification already escalated a batch to its worst member; this extends the *guards* the same way.)

### 9.6 Free-string `action` validation

The seven tools with free-string `action` (`manage_build`, `manage_packages`, `manage_graphics`, `manage_profiler`, `manage_camera`, `manage_vfx`, `manage_probuilder`) **already validate the action against their `ALL_ACTIONS` list** and return a structured error before any Unity dispatch. Rather than convert the schema to `Literal[...]` — which would risk rejecting valid actions whenever the Python list drifts from the C# side — this pass keeps the non-breaking runtime validation and adds explicit tests ([`test_action_validation.py`](../Server/tests/test_action_validation.py)) proving unknown/empty actions are rejected. **Migration path to `Literal`:** once a CI check asserts the Python `ALL_ACTIONS` lists are a superset of the C# handler's accepted actions, the `action` annotations can be tightened to `Literal` for client-side enum hints with no compatibility risk.

### 9.7 Telemetry OFF by default

The fork default is now `telemetry_enabled = False`. `TelemetryConfig` reads the authoritative `core.config` instance (the same one `main()` mutates), defaults off when config is absent, and enables only on explicit opt-in (`UNITY_MCP_TELEMETRY_ENABLED=1`) or config; any `DISABLE_*` opt-out still wins. Upstream attribution and the (now dormant) endpoint are preserved, but **no usage is sent to Coplay infrastructure unless the operator opts in.**

### 9.8 On the meaning of `confirm: true`

`confirm: true` is **destructive-operation acknowledgement metadata**, not human confirmation. It is a second deliberate step an agent must take, and it is auditable — but it is *not* a person clicking "yes." It can **never** relax the safety mode: a destructive call in `read_only`/`review_only` is refused even with `confirm: true` (verified by tests). Real safety comes from the externally-configured safety mode and the guards; `confirm` only gates destructive calls *within* a mode that already permits them.

### 9.9 Test counts

Full Python suite: **1416 passed, 2 skipped** (was 1349). New/changed tests: 47 added to `test_safety_mode.py` (guards, shared enforcement incl. API source, batch recursion, confirm coercion, telemetry default); `test_action_validation.py` (14); `test_api_command_enforcement.py` (6, subprocess-isolated end-to-end). No existing tests regressed; the two updated (`test_config_telemetry_defaults`, `test_config_preferred_then_env_override`) were adjusted to the deliberate telemetry-default change.

---

## Appendix A — What this PR changed

**Phase-2a (prior pass) new files:** `core/safety.py`, `core/audit.py`, `transport/safety_middleware.py`, `tests/test_safety_mode.py`, this review.

**Phase-2b (this pass) new files**
- `Server/src/core/enforcement.py` — shared `enforce_tool_call` used by both ingress paths; `PolicyViolation`, `coerce_confirm`, batch-recursive guards.
- `Server/tests/test_action_validation.py` — invalid-action rejection for the 7 free-string tools (14 tests).
- `Server/tests/test_api_command_enforcement.py` — subprocess-isolated end-to-end proof the `/api/command` route is gated (6 tests).

**Phase-2b edited**
- `Server/src/core/safety.py` — tool guards (`build_output_guard`, `execute_code_guard`, `execute_menu_item_guard`, `DEFAULT_MENU_ITEM_ALLOWLIST`, `action_of`); `ping`/`get_tool_states` READ policies.
- `Server/src/transport/safety_middleware.py` — refactored to delegate to `core/enforcement.py`.
- `Server/src/main.py` — `/api/command` now calls `enforce_tool_call` (403 on block); env parsing + help for the new guard flags.
- `Server/src/core/config.py` — `allow_execute_code`, `allow_arbitrary_menu_items`, `menu_item_allowlist`, `allow_external_build_output`; `telemetry_enabled` default **False**.
- `Server/src/core/telemetry.py` — reads `core.config`; default-off; `UNITY_MCP_TELEMETRY_ENABLED` opt-in.
- `Server/tests/conftest.py` — restore new config fields between tests.
- `Server/tests/test_safety_mode.py` — +47 tests (guards, enforcement incl. API source, batch recursion, confirm coercion, telemetry default).
- `Server/tests/test_core_infrastructure_characterization.py`, `Server/tests/integration/test_telemetry_endpoint_validation.py` — updated for the deliberate telemetry-default change.
- `README.md` — accurate safety semantics (confirm = acknowledgement metadata), new config/env docs.

**Deliberately *not* changed** (rejected as out-of-scope or over-engineering)
- No tool renames, no `action` → `Literal` schema rewrite (runtime validation is non-breaking and already present), no C#-side changes — keeps upstream sync clean and the diff reviewable.
- No interactive confirmation dialogs — would deadlock headless automation; `confirm: true` acknowledgement metadata is the correct headless analogue.
- No per-tool guard code — enforcement stays in the one shared chokepoint.

## Appendix C — Adversarial review round (this pass)

After the Phase-2b fixes landed, a multi-agent adversarial review (5 independent probes → per-finding verification) was run against the enforcement layer. It surfaced **8 confirmed bypasses**, all now fixed and regression-tested:

| # | Class | Bypass | Fix |
|---|---|---|---|
| 1, 8 | Classification | A nested `batch_execute` was short-circuited to WRITE, so a `delete` wrapped one batch layer deep escaped the destructive-confirm gate in `write` mode. | `_classify_batch` now recurses into nested batches (depth-capped, fail-closed to DESTRUCTIVE). |
| 2–5 | Classification | `manage_build` `settings`/`platform`/`scenes`/`profiles` mutate when given a payload but were classed READ, so they executed in `read_only`. | `_classify_manage_build` is payload-aware: mutating-arg-present → WRITE, else READ. |
| 6 | Guard/handler key mismatch | The menu guard read `menuPath` first while C# reads `menu_path` first — an attacker put an allowlisted value in one key and a dangerous value in the other. | `execute_menu_item_guard` now validates **every** provided spelling. |
| 7 | Guard/handler key mismatch | The build-path guard checked only snake_case keys, but C# `ToolParams` also honors `outputPath`/`outputDir`. | `BUILD_PATH_KEYS` now includes the camelCase spellings. |

The review's own scope caveats were accurate and are worth recording: findings 1–8 never let a call **escape its mode** (read_only/review_only still blocked the WRITE/DESTRUCTIVE class) — they defeated the *confirm acknowledgement* (write mode) or misclassified a write as READ. The mode boundary held throughout; the failures were within-mode. This is exactly why the mode is the real safety control and `confirm` is only acknowledgement metadata.

**Re-verification round.** A second focused pass confirmed fixes 1–7 hold and hunted for the same bug classes elsewhere. It found **one more confirmed sibling**: `manage_scene` `action=validate` with `auto_repair=true` removes missing-script components and dirties the scene (a WRITE) but was classified READ — the same dual-mode pattern as `manage_build`, and its flag has both `auto_repair`/`autoRepair` spellings. Two fixes landed:

- **`_classify_manage_scene`** makes `validate` WRITE when `auto_repair` (either spelling) is truthy, else READ.
- **Systemic key-canonicalization** — `core/enforcement.py:canonicalize_keys` now folds every argument key to snake_case (recursively, including batch-member params) before classification and guards, and **refuses** a call that sends the same logical key under two spellings with differing values. This makes the guard/handler key-parity bug class (findings 6, 7, and the `manage_scene` flag) structurally impossible going forward, rather than fixing it one guard at a time. The per-guard both-spelling checks are retained as defense-in-depth.

A third focused pass verified the `manage_scene` fix and the key-canonicalization hold, and — sweeping for the *same dual-mode class* across all tools — found three more READ/VALIDATE actions with hidden write branches, all now reclassified with regression tests:

- **`manage_ui render_ui`** (was READ) → **WRITE** — it persists a RenderTexture asset and can create/dirty a PanelSettings asset.
- **`manage_graphics bake_get_settings`** (was READ) → **WRITE** — it creates+assigns a `LightingSettings` asset when the scene has none.
- **`manage_prefabs close_prefab_stage`** (was VALIDATE) → **payload-aware**: WRITE when `save_before_close` is set (it saves the prefab), else VALIDATE (discard-close).

The pass also flagged one latent (not-yet-exploitable) item that was fixed proactively: the Python key-folding regex didn't exactly match Unity's `StringCaseUtility.ToSnakeCase`, which would reopen the key-parity class for a future acronym/digit-boundary key. `_to_snake` now mirrors the C# regex exactly (`([a-z0-9])([A-Z])` → `$1_$2`, lowercased), locked by a parity test. A fourth exhaustive sweep cross-checked **every** remaining READ/VALIDATE action across all tools against its C# handler and came back **clean (0 findings)** — the dual-mode class is closed. The adversarial-review loop converged over four rounds: 8 → 1 → 3 → 0.

## Appendix B — Residual risks after this pass

**Closed this pass:** CLI/API bypass, `manage_build` path traversal/external escape, `execute_code`/`execute_menu_item` default-open, telemetry-on-by-default, nested-batch confirm downgrade, dual-mode read_only/review_only escapes in `manage_build`/`manage_scene`/`manage_ui`/`manage_graphics`/`manage_prefabs`, menu/build guard key-precedence bypasses, and the guard/handler key-parity bug class as a whole (via key canonicalization matched exactly to Unity's `ToSnakeCase`). All previously-listed Phase-2 residuals are addressed.

**Still open (honest list):**
1. **`manage_build` C#-side path check** — the Python guard is syntactic (rejects `..`, absolute/external). A symlinked in-project directory pointing outside the project is not caught server-side; a Unity/C# canonicalised project-root check would be defence-in-depth.
2. **`execute_custom_tool`** — project-authored custom tools are classified WRITE (blocked in inspect modes, allowed in `write`). Their bodies are arbitrary; they are lower-risk than injection (authored by the project owner) but are not individually classified. Treat with the same trust as the project's own editor scripts.
3. **HTTP transport auth** — no bearer token for non-remote-hosted local HTTP; binding beyond loopback is still a footgun (the CLI already warns on non-localhost hosts). Refusing non-loopback bind without a token remains a follow-up.
4. **Classification is a maintained table** — a new destructive action added to an existing CRUD tool defaults to that tool's default class (WRITE), not DESTRUCTIVE, until listed. `TestPolicyTableIntegrity` guards the known-dangerous tools; extend it as tools evolve. A CI check asserting Python `ALL_ACTIONS` ⊇ C# accepted actions would also unlock the `action` → `Literal` migration.
5. **Defense-in-depth is Python-only** — all enforcement is server-side. Mirroring the most dangerous checks (delete, build output path, menu allowlist) on the C# side would protect against a future non-Python ingress and against `ToolParams` key-resolution drift. Not required today (all ingress is Python, and argument keys are now canonicalised so a guard cannot diverge from what C# resolves).
6. **Dual-mode-action classification is per-tool** — `manage_build` and `manage_scene` needed payload-aware classifiers because one action string covers both a read and a mutating sub-mode. A CI check that flags any `ToolPolicy` READ/VALIDATE action whose C# handler has a write branch would catch a future sibling automatically; today `TestPolicyTableIntegrity` plus the adversarial-review regression tests are the guard.
5. **Guards live in Python** — enforcement is server-side. A defence-in-depth mirror of the most dangerous checks (delete/build/menu) on the C# side would protect against a future non-Python ingress. Not required today (all ingress is Python), but worth noting.

---

## 10. Capability roadmap (Phase 3+) — making the agent smarter, not just safer

The hardening above makes the bridge *trustworthy*; this phase makes it *more capable*. Four directions, each grounded in a parallel design pass, built wave-by-wave (cheapest testable wins first):

**Wave 1 — server-side quick wins (landed, Unity-free, fully unit-tested):**
- **`safety_status` tool + `mcpforunity://server/safety` resource** — the agent can read the active mode, allowed classes, and execution-surface flags *before* acting. The fork's own hardening had created a blind spot: an agent could only learn the mode by having a call refused. READ-classified. (`core/safety.py:describe_safety_posture`, `services/tools/safety_status.py`, `services/resources/server_safety.py`)
- **`run_tests_and_summarize`** — one call runs tests, waits (bounded), and returns a compact pass/fail + capped failing-test summary instead of start-then-poll-then-parse. VALIDATE-classed (runs in `review_only`). (`services/tools/run_tests.py`)
- **Agent-instruction fix** — `_build_instructions` told agents to poll `editor_state.isCompiling`, a field that does not exist; the real path is `compilation.is_compiling`. Fixed, plus added situational-awareness guidance (read context once; check `safety_status`).

**Wave 2 — flagship project intelligence (`manage_project`, READ, C#-backed):** whole-project validators (missing references, broken prefabs, dangling serialized fields, missing/error shaders, asset inventory) and an aggregate **`project_health`** report. A key design finding gates the shape: **the safety layer gates tools, not resources**, so these must be tools classified READ (a resource would bypass the gate and be invisible to the safety table). Today only the *active scene's root objects* are checked — nothing scans assets on disk.

**Wave 3 — efficiency:** `get_editor_context` (one read = scene + selection + compile/reload state + prefab stage + console-error count, ~75% fewer task-start round-trips); `validate_build` READ pre-flight; additive `properties` bags to slim the mega-schemas without breaking compatibility.

**Wave 4 — orchestration skills:** a lightweight `unity-health-check`, a generic `unity-scene-setup`, a safety-mode-awareness section in the shipped `unity-mcp-skill`, and dedup of a stale skill copy.

Waves 2–3 are C#-heavy and need a live Unity Editor to verify end-to-end (Python tests cover the plumbing/paging/classification); they will ship with EditMode tests under `TestProjects/UnityMCPTests`.
