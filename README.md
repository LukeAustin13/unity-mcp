<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-header-dark.png">
    <img alt="MCP for Unity" src="docs/images/logo-header-light.png" width="400">
  </picture>
</p>

<div align="center">

[English](README.md) <img src="docs/images/connector.svg" alt="↔" height="14"> [简体中文](docs/i18n/README-zh.md) &nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp; [Discord](https://discord.gg/y4p8KfzrN4) <img src="docs/images/connector.svg" alt="↔" height="14"> [Wiki](https://coplaydev.github.io/unity-mcp/)

#### Proudly sponsored and maintained by [Aura](https://www.tryaura.dev/) — the AI assistant for Unreal & Unity.
##### And don't miss [Godot AI](https://github.com/hi-godot/godot-ai), the new open source project from the makers of MCP for Unity.

</div>

<p align="center"><b>Create your Unity apps with LLMs.</b> MCP for Unity bridges AI assistants — Claude, Codex, VS Code, local LLMs, and more — with your Unity Editor via <a href="https://modelcontextprotocol.io/introduction">Model Context Protocol</a>. Give your LLM the tools to manage assets, control scenes, edit scripts, run tests, and automate your game dev workflows.</p>

<p align="center">
  <img alt="MCP for Unity building a scene" src="docs/images/building_scene.gif">
</p>

---

<!-- recent-updates:start -->
<details>
<summary><strong>Recent Updates</strong></summary>

* **[v10.0.0](https://github.com/CoplayDev/unity-mcp/releases/tag/v10.0.0)** (2026-06-30)
* **[v9.7.3](https://github.com/CoplayDev/unity-mcp/releases/tag/v9.7.3)** (2026-06-15)
* **[v9.7.1](https://github.com/CoplayDev/unity-mcp/releases/tag/v9.7.1)** (2026-05-24)
* **[v9.7.0](https://github.com/CoplayDev/unity-mcp/releases/tag/v9.7.0)** (2026-05-22)
* **[v9.6.8](https://github.com/CoplayDev/unity-mcp/releases/tag/v9.6.8)** (2026-04-27)

Full history: [Release Notes](https://coplaydev.github.io/unity-mcp/releases).

</details>
<!-- recent-updates:end -->

---

## About this fork

This is a **hardened fork** of [MCP for Unity](https://github.com/CoplayDev/unity-mcp) by CoplayDev, used under the MIT License. The original copyright notice is preserved verbatim in [`LICENSE`](LICENSE). **This fork is not affiliated with or endorsed by CoplayDev or Unity Technologies.**

It exists to make AI-agent access to the Unity Editor **safe to run on real projects**. Letting an assistant delete assets, run builds, install packages, and execute code is a security-sensitive automation surface — a prompt-injected or misaligned agent can do real damage. This fork adds server-side guardrails on top of the upstream bridge without breaking MCP compatibility. See the full audit in [`docs/FORK_HARDENING_REVIEW.md`](docs/FORK_HARDENING_REVIEW.md).

It stays **platform-neutral**: PC, mobile, prototype, tool, and release-build workflows are all first-class. Android and other targets are options, not the identity of the project.

### Safety modes

Every MCP tool call is classified — `read` · `validate` · `write` · `destructive` — and checked against a configured safety mode **before it reaches Unity**:

| Mode | What the agent can do | Use it for |
|---|---|---|
| `read_only` | Inspect only. No mutation of any kind. | Q&A over a project; letting an agent explore without risk. |
| `review_only` | Inspect **plus** run tests, refresh, play mode, screenshots. Still no edits. | A "reviewer" agent that validates but never modifies. |
| `write` *(default)* | Everything. **Destructive actions require an acknowledgement flag.** | Normal agent-assisted development. |

Enforcement happens in one place that **every** ingress passes through — MCP tool calls *and* the local `/api/command` route the CLI uses — so there is no back door around the policy.

**Destructive** = deletes data, executes arbitrary code or menu items, runs builds, or installs/removes packages. In `write` mode these are refused unless the call includes `"confirm": true`. `batch_execute` inherits the most severe class among its commands, so a batch can't smuggle a delete past the gate.

> **What `confirm: true` is — and isn't.** It is **destructive-operation acknowledgement metadata**: a second, deliberate, *auditable* step an agent must take before a destructive call proceeds. It is **not** human confirmation and **not** a security boundary. It can never relax the mode — a destructive call in `read_only`/`review_only` is refused even with `confirm: true`. Real safety comes from the externally-configured safety mode and the guardrails below, not from the flag.

Set the mode (highest priority first):

```bash
# CLI flag
mcp-for-unity --safety-mode review_only

# or environment variable
UNITY_MCP_SAFETY_MODE=read_only
```

Default is `write` for backward compatibility. **`review_only` is recommended for untrusted or CI usage.** Unknown/future tools default to `write`-class, so a new tool is blocked outside write mode until it is explicitly classified.

An agent can read the current posture up front with the **`safety_status`** tool (or the `mcpforunity://server/safety` resource) — mode, allowed action classes, and which execution surfaces are enabled — so it picks actions the mode allows instead of discovering limits from a rejection.

### Hardened execution surfaces

The most dangerous tools have extra, config-gated guardrails that apply on top of the mode (and even with `confirm: true`):

| Surface | Default | Opt-in to relax |
|---|---|---|
| **`execute_code`** — compiles and runs arbitrary C# in the Editor; **not sandboxed**. | **Disabled.** | `UNITY_MCP_ALLOW_EXECUTE_CODE=1` (still needs `write` mode + `confirm`). |
| **`execute_menu_item`** — runs Editor menu paths. | **Allowlist-only** (a small set of window-opening items). | `UNITY_MCP_MENU_ITEM_ALLOWLIST="A/B,C/D"` to add paths, or `UNITY_MCP_ALLOW_ARBITRARY_MENU_ITEMS=1` for any (not recommended). |
| **`manage_build`** output path | **Project-relative only**; `..` traversal and absolute/external paths rejected. | `UNITY_MCP_ALLOW_EXTERNAL_BUILD_OUTPUT=1`. |

Leave `execute_code` disabled for normal use — writing a script with `manage_script` and letting Unity compile it is the safe path; `execute_code` exists only for trusted, interactive debugging.

### Audit log

Every tool call is recorded as one JSON line in `<log dir>/unity_mcp_audit.jsonl` — timestamp, tool, action, classification, mode, the allow/block/confirm decision, and a size-capped argument summary (never the full payload). Disable with `UNITY_MCP_AUDIT_LOG=0`; relocate with `UNITY_MCP_AUDIT_LOG_DIR`. Audit failures never break a tool call.

### Telemetry

Unlike upstream, this fork ships with **telemetry disabled by default** — no usage data is sent anywhere unless you explicitly opt in with `UNITY_MCP_TELEMETRY_ENABLED=1`. Upstream attribution is preserved; the fork simply does not phone home on your behalf.

### Read-only project intelligence

Beyond guardrails, the fork adds inspection tools that work even in `read_only` mode: project/prefab health scans, dependency X-ray, fast scene queries, mobile and UI-layout audits, scene contracts (declare in JSON what a scene must and must not contain, then validate), zero-pixel visual audits, and one-call play smoke tests. The full list with CLI equivalents lives in [`CHANGELOG.fork.md`](CHANGELOG.fork.md).

### Intended use

For developers who want AI-assisted Unity workflows they can trust on projects that matter. Run `review_only` when you want inspection and test-running without edit risk; run `write` for hands-on development with an acknowledgement gate and an audit trail on the operations that can lose work.

---

## What it does

Control the Unity Editor in natural language from any MCP client — create scenes & GameObjects, edit C# scripts, manage assets, run tests, profile, and build. 47 focused MCP tool entrypoints, any client, free & MIT.

**[Browse the full tool catalog →](https://coplaydev.github.io/unity-mcp/reference/tools/)**

---

## Quickstart

**Requirements:** Unity **2021.3 LTS → 6.x** · Python **3.10+** (via [`uv`](https://docs.astral.sh/uv/)). Works with **any MCP client** — Claude Desktop & Code, Cursor, VS Code, Windsurf, Cline, Gemini CLI, and more.

1. **Install** — Unity → Package Manager → Add from git URL:
   `https://github.com/CoplayDev/unity-mcp.git?path=/MCPForUnity#main` &nbsp;_(pin `#v10.0.0` for this release, or `openupm add com.coplaydev.unity-mcp`)_
2. **Configure** — `Window → MCP for Unity → Configure All Detected Clients`.
3. **Prompt** — *"Create a cube at the origin and add a Rigidbody."* The cube appears in seconds.

---

## Community

- [Discord](https://discord.gg/y4p8KfzrN4) — chat with maintainers and other contributors
- [Issues](https://github.com/CoplayDev/unity-mcp/issues) — bugs and feature requests
- [Discussions](https://github.com/CoplayDev/unity-mcp/discussions) — design ideas and broader questions
- Security: see [SECURITY.md](SECURITY.md) for private reporting

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Branch off `beta`, not `main`. The full dev setup, testing, and release process live in the [Contributing](https://coplaydev.github.io/unity-mcp/contributing/dev-setup) docs.

## Advanced

- **Multiple Unity instances** — [Multi-Instance Routing](https://coplaydev.github.io/unity-mcp/guides/multi-instance)
- **Tool groups (vfx / animation / ui / testing / etc.)** — [Tool Groups](https://coplaydev.github.io/unity-mcp/guides/tool-groups)
- **v10 asset generation and upgrade notes** — [v10 Migration](https://coplaydev.github.io/unity-mcp/migrations/v10)
- **Roslyn script validation** — [Roslyn Validation](https://coplaydev.github.io/unity-mcp/guides/roslyn)
- **Remote-hosted server with auth** — [Remote Server Auth](https://coplaydev.github.io/unity-mcp/guides/remote-server-auth)

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=CoplayDev/unity-mcp&type=Date)](https://www.star-history.com/#CoplayDev/unity-mcp&Date)

## Citation

If MCP for Unity helped your research, please cite it.

```bibtex
@inproceedings{wu2025mcpunity,
  author    = {Wu, Shutong and Barnett, Justin P.},
  title     = {{MCP-Unity}: {Protocol-Driven} Framework for Interactive {3D} Authoring},
  year      = {2025},
  isbn      = {9798400721366},
  publisher = {Association for Computing Machinery},
  address   = {New York, NY, USA},
  url       = {https://doi.org/10.1145/3757376.3771417},
  doi       = {10.1145/3757376.3771417},
  series    = {SA Technical Communications '25}
}
```

## Unity AI Tools by Aura

Aura offers 2 AI tools for Unity:
- **MCP for Unity** is available freely under the MIT license.
- **Aura for Unity** is a premium Unity/Unreal AI assistant built for game devs.

## Disclaimer

This project is a free and open-source tool for the Unity Editor, and is not affiliated with Unity Technologies.

---

**License:** MIT — see [LICENSE](LICENSE).
