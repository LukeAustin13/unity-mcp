# Security Policy

> This is a **hardened fork** of MCP for Unity. Giving an AI assistant control of the Unity Editor is a security-sensitive automation surface — a prompt-injected agent (acting on malicious text in a project file, asset label, or console message) can do real damage. The section below documents the fork's safety controls; the full audit is in [`docs/FORK_HARDENING_REVIEW.md`](docs/FORK_HARDENING_REVIEW.md).

## Fork safety model (run it safely)

**Safety mode is the one control that matters.** Every tool call is classified `read`/`validate`/`write`/`destructive` and checked against a configured mode *before it reaches Unity*, at a single enforcement point every ingress passes through (MCP calls **and** the CLI's `/api/command` route):

| Mode | Agent can… | Use for |
|---|---|---|
| `read_only` | inspect only | untrusted exploration |
| `review_only` | inspect + tests/refresh/play/screenshots | reviewer / CI |
| `write` *(default)* | everything; destructive needs `confirm: true` | hands-on dev |

Set via `--safety-mode <mode>` or `UNITY_MCP_SAFETY_MODE`. Run `review_only` for anything you don't fully trust. An agent reads the live posture with the `safety_status` tool / `mcpforunity://server/safety` resource.

`confirm: true` is **acknowledgement metadata, not human confirmation** — it never relaxes the mode (a destructive call in `read_only`/`review_only` is refused even with it).

**Execution-surface guards** (all default off; on top of the mode, even with `confirm`):

| Surface | Default | Opt-in |
|---|---|---|
| `execute_code` (un-sandboxed C#) | disabled | `UNITY_MCP_ALLOW_EXECUTE_CODE=1` |
| `execute_menu_item` | allowlist-only | `UNITY_MCP_MENU_ITEM_ALLOWLIST=…` / `UNITY_MCP_ALLOW_ARBITRARY_MENU_ITEMS=1` |
| `manage_build` output path | project-relative only | `UNITY_MCP_ALLOW_EXTERNAL_BUILD_OUTPUT=1` |

**Audit log** — every call is recorded to `<log dir>/unity_mcp_audit.jsonl`. Review it with `unity-mcp audit tail --blocked-only` and `unity-mcp audit summary`. Disable with `UNITY_MCP_AUDIT_LOG=0`.

**Telemetry is OFF by default** in this fork (opt in with `UNITY_MCP_TELEMETRY_ENABLED=1`).

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, email **security@coplay.dev** with:

- A clear description of the issue
- Steps to reproduce or a proof-of-concept
- The version of MCP for Unity affected (UPM package + Python server)
- Your OS, Unity Editor version, and MCP client
- Optional: a suggested fix

We aim to acknowledge reports within **3 business days** and to share an initial assessment within **10 business days**. Critical fixes are released as patch versions on both `main` and the beta channel.

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest (`main`) | Yes |
| latest beta (`beta`) | Yes |
| older releases | No — please upgrade |

## Network Defaults (Safe by Default)

MCP for Unity is intentionally fail-closed:

- **HTTP Local** binds to loopback only by default (`127.0.0.1`, `localhost`, `::1`). LAN bind (`0.0.0.0`, `::`) requires explicit opt-in via **Allow LAN Bind (HTTP Local)** in Advanced Settings. In this fork, the **Python server also refuses to start** a non-remote-hosted HTTP transport bound to a non-loopback host unless `UNITY_MCP_ALLOW_INSECURE_HTTP=1` (or `--allow-insecure-http`) is set — an unauthenticated network-exposed bridge is a footgun and is fail-closed on both sides.
- **HTTP Remote** requires `https://` by default. Plaintext `http://` for remote endpoints requires explicit opt-in via **Allow Insecure Remote HTTP**.
- Remote-hosted mode requires API key authentication. See [Remote Server Auth](https://coplaydev.github.io/unity-mcp/guides/remote-server-auth).

If you find a way to bypass any of these guards, that qualifies as a security vulnerability and warrants a private report.

## What Counts as a Security Issue

- Remote code execution via crafted MCP messages
- Auth bypass on remote-hosted server
- Filesystem read/write outside the intended Unity project root
- Network requests that escape the configured allow-list
- Credential or API-key leakage in logs, telemetry, or error responses

## What Doesn't Count

- Tool actions that intentionally modify the Unity project (that's the product)
- Issues that require an attacker to already have shell access to the host
- Vulnerabilities in third-party dependencies — please report those upstream first; we'll bump our pins after the upstream fix lands

## Disclosure Timeline

Once a fix is shipped, we publish a security advisory on the GitHub Security tab and credit the reporter (unless they prefer anonymity).
