# Drop-in agent skills for MCP for Unity (hardened fork)

Portable skills that teach an AI agent to drive the Unity Editor through this fork of MCP for Unity — safely, cheaply, and with verification discipline. Each skill is a self-contained folder of plain markdown: drag it into a project that uses this MCP and the agent picks up the thinking, not just the tool names.

## Install

Copy any skill folder into your Unity project's `.claude/skills/` directory:

```text
your-game/
├── .claude/
│   └── skills/
│       ├── unity-mcp-operator/       ← copied from this repo's skills/
│       └── unity-project-doctor/
├── Assets/
└── ...
```

Claude Code discovers them automatically. They are plain markdown with YAML frontmatter, so they also work as system-prompt material for any other MCP client that supports skill/instruction files.

## The set

Start with **unity-mcp-operator** — it is the base operating layer; every other skill assumes its habits (check the posture first, cheapest evidence that answers the question, verify before claiming).

**Foundation**

| Skill | What it teaches | Minimum safety mode |
|---|---|---|
| [`unity-mcp-operator`](unity-mcp-operator/SKILL.md) | The operating layer: posture-first planning, a five-tier evidence ladder (resources → `query_scene` → audit tools → `visibility_report` → pixels last), batching/paging discipline, refusal handling, honest reporting. | `read_only` |

**Understand & plan** — read-only, produce findings/plans, never mutate

| Skill | What it teaches | Minimum safety mode |
|---|---|---|
| [`unity-project-doctor`](unity-project-doctor/SKILL.md) | Fast whole-project diagnosis: asset/material/prefab health, build pre-flight, dependency X-ray. | `read_only` |
| [`unity-review`](unity-review/SKILL.md) | Senior-level audit — architecture, code quality, Unity patterns, engineering health — using the doctor-style scans as its evidence base. | `read_only` (test leg `review_only`) |
| [`unity-plan`](unity-plan/SKILL.md) | Turn a request into a staged plan that fits the active safety mode; investigate the live project before committing to an approach; route each phase to the right skill. | `read_only` |

**Inspect & validate**

| Skill | What it teaches | Minimum safety mode |
|---|---|---|
| [`unity-scene-contracts`](unity-scene-contracts/SKILL.md) | Author and validate JSON scene contracts — "this scene must (not) contain…" — including CI usage. | `read_only` |
| [`unity-ui-auditor`](unity-ui-auditor/SKILL.md) | Analytic resolution-matrix uGUI audit: off-screen, overlap, tiny targets, scaler issues — facts, not screenshots. | `read_only` |
| [`unity-debug`](unity-debug/SKILL.md) | Hypothesis-driven runtime debugging — evidence before fixes, no shotgun changes. | `read_only` (reproduce `review_only`, fix `write`) |

**Change & verify**

| Skill | What it teaches | Minimum safety mode |
|---|---|---|
| [`unity-edit-verify`](unity-edit-verify/SKILL.md) | The canonical C# edit-and-verify loop: read → hash-guarded edit → auto-compile wait → console check. | `write` |
| [`unity-safe-iteration`](unity-safe-iteration/SKILL.md) | Checkpoint → change → verify → keep-or-rollback loop for risky scene edits. | `write` |
| [`unity-ui-design`](unity-ui-design/SKILL.md) | Senior UI design & build for uGUI and UI Toolkit — design tokens, atomic prefabs, anchored layouts, screenshot- and audit-verified iteration. | `write` |

**Test** — VALIDATE-class; the `testing` tool group must be activated first

| Skill | What it teaches | Minimum safety mode |
|---|---|---|
| [`unity-test-run`](unity-test-run/SKILL.md) | The verification contract: a "tests pass" claim is valid only with a completed job and a cited job id. | `review_only` |
| [`unity-test-pilot`](unity-test-pilot/SKILL.md) | Test triage: one-call summaries, flaky-vs-deterministic classification, play-mode smoke tests, console triage. | `review_only` |

**Project-specific examples** — adapt before use; they encode one game's conventions, not universal rules

| Skill | What it teaches | Minimum safety mode |
|---|---|---|
| [`sw-rules-guard`](sw-rules-guard/SKILL.md) | Pre-flight / post-edit compliance gates for a specific game ("States At War"). A **template**: replace the gates with your own project's non-negotiable rules, keep the gate structure. | `read_only` |
| [`unity-scene-rebuild`](unity-scene-rebuild/SKILL.md) | Rebuild a scene from a project's own authoritative procedural generator (menu item / build script), with checkpoint-based rollback. Assumes such a generator exists. | `write` |

## Why these exist

This fork classifies every tool call (`read` / `validate` / `write` / `destructive`) and enforces a configured safety mode before anything reaches Unity, and it adds a set of read-only intelligence tools (project health, prefab health, scene queries, scene contracts, UI audits, play smoke tests). Raw tool schemas tell an agent *what* it can call; these skills encode *how to think* with them — check the posture before acting, prefer facts over pixels, page instead of flooding, and never report success without a verifying read.

The shipped [`unity-mcp-skill`](../.claude/skills/unity-mcp-skill/SKILL.md) remains the full tool/orchestration reference; the skills here are focused, workflow-shaped layers on top of it.

## License

MIT, same as the repository — see [`LICENSE`](../LICENSE).
