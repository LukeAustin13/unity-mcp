---
name: unity-plan
description: Produces an implementation plan for any Unity development task — feature, system, refactor, optimisation, tooling, or integration — grounded in a live-project investigation through MCP for Unity (hardened fork). Use when asked to plan, design, architect, scope, or "how should we approach" anything in the Unity project, before code is written. Scales plan depth to task size; investigates with the read-only intelligence tools (safety_status, editor/context, project_health, query_scene, find_references, validate_build) before committing to an approach; routes execution through the companion unity skills. Planning is READ-class and runs in any safety mode including read_only — but the plan it writes must fit the active mode. It does not diagnose runtime failures (use unity-debug), audit existing project/code health (use unity-project-doctor / unity-review), or execute the edits it plans (use unity-edit-verify).
license: MIT
---

# Unity Plan

Plan before building. Write plans a mid-level developer could execute with confidence and a senior developer would trust as technically sound: systems, trade-offs, and sequenced risk reduction — not isolated tasks. **Every statement the plan makes about the existing project must come from actual investigation** (MCP tool output, file reads, searches), never from assumption.

Planning itself is **READ-class** — it inspects, it does not mutate — so it runs in every safety mode. The catch is in **Step 0**: the plan you write must fit the mode the executor will run in. This skill orients, investigates, decides an approach, and hands sequenced work to the companion skills. It does not run the edits, run the tests, or diagnose runtime bugs — it routes those.

This skill assumes the base operating discipline in `unity-mcp-operator` (orient → posture → cheapest evidence tier → verify → report). Don't re-derive it; lean on it for *how* to make each read cheaply.

---

## Right-Size the Plan

Match plan weight to task weight before writing anything:

- **Compact plan** — single system, roughly ≤3 files, no cross-system impact, no unresolved unknowns. Produce: Step 0 orientation, the context assessment (Step 1), one numbered implementation sequence with acceptance criteria, and the execution routing (Step 7). Skip the ADR, phase tables, and effort estimates.
- **Full plan** — multiple systems, architectural decisions, unknowns needing research, or performance-critical work. Run every step below.

When in doubt, start compact and escalate if investigation reveals hidden coupling. A five-phase plan for a one-script change is itself a planning failure.

---

## Step 0 — Orient, then investigate the live project

Two reads before any planning thought, then targeted investigation. All of this is READ-class and safe in `read_only`.

### 0a. Read the posture FIRST — it constrains the whole plan

```python
safety_status()      # or read mcpforunity://server/safety
# → mode, allowed_classes, destructive_requires_confirm, execute_code_enabled, ...
```

The mode decides what kind of plan is even valid:

| Active mode | The plan may end in… |
|---|---|
| `read_only` | findings + a sequenced recommendation only. The routed edit/test/build steps CANNOT run in this session — say so explicitly in Step 7. |
| `review_only` | the above, plus verification steps (tests, play mode, screenshots, refresh) can be executed by the routed skills. Persistent edits still cannot. |
| `write` | full execution — every routed skill can run; destructive steps (builds, checkpoint restore, deletes) additionally need `confirm:true` acknowledgement metadata on their own call. |

A plan for a `read_only` session that says "then apply the fix" is the wrong plan — it promises work the mode forbids. `confirm:true` is destructive-op acknowledgement metadata, not human approval, and never relaxes the mode: never plan around it to "get past" `read_only`/`review_only`. Do not build any plan on `execute_code` (disabled by default even in `write`) or arbitrary `execute_menu_item` (allowlist-only) — write a script with the script tools and let Unity compile it.

### 0b. One-read orientation

```text
mcpforunity://editor/context     # resource — read it, don't call it
# → editor state + selection + prefab stage + console error/warning counts in one read
# (fallback on older servers: mcpforunity://editor/state)
```

If it reports compiling or a pending domain reload, the project is mid-flux — do not trust reads until `editor_state`'s `compilation.is_compiling` is false. If a Unity instance isn't connected, plan from source only and say the live-scene claims are unverified.

### 0c. Investigate before committing to an approach

Prefer the live project over guessing, and prefer these read-only intelligence tools over hand-rolled read loops. Reach for `/researcher` only for things the project itself cannot answer (third-party package internals, platform runtime behaviour, Unity version edge cases).

| Question the plan must answer | Cheapest tool |
|---|---|
| How big / what shape is this project? | `manage_project(action="asset_inventory")`, `manage_project(action="project_health")` |
| Is the project healthy enough to build on? | `manage_project(action="project_health")` + `read_console(action="get", types=["error"], format="summary")` |
| What is actually in the live scene, and how big? | `query_scene(...)` — one paged call, filter + projection; replaces the find-object → get-components → get-properties loop |
| What breaks if the plan touches this asset? (blast radius) | `manage_project(action="find_references", target="Assets/.../X.prefab")` — reverse dependency lookup |
| What does this asset depend on? | `manage_project(action="get_dependencies", target=..., recursive=true)` |
| Does this class / method / property actually exist? | `unity_reflect(action="search"|"get_type"|"get_member", ...)` before designing around an API |
| How is that API meant to be used? | `unity_docs(action="get_doc", class_name=...)` — usage, gotchas, examples |
| Where are the call sites of X? | `find_in_file(...)` — regex over a file for references/patterns |
| Would a build still succeed? (build-affecting plans) | `validate_build(target=...)` — read-only pre-flight, no artifacts |
| Where is the real performance cost? (perf plans) | `manage_profiler(...)` — measure, don't guess the bottleneck |

> **Non-core tool groups are disabled by default.** `unity_reflect` / `unity_docs` live in the `docs` group and `manage_profiler` in the `profiling` group — like the `testing` group, they must be turned on before use: `manage_tools(action="list_groups")` then `manage_tools(action="activate", group="docs")` / `"profiling"`. Only `core` (the `manage_project` / `query_scene` / `validate_build` / `read_console` rows above) is on out of the box. If one of these tools appears missing during investigation, activate its group rather than concluding the capability is gone.

Exact-call examples:

```python
# Scene reality — "what enemies exist, what's on them, how big are they?" — one call, not N
query_scene(component_type="EnemyController",
            include=["components", "world_bounds"], page_size=50)

# Blast radius — "what will break if the plan reworks this prefab?"
manage_project(action="find_references", target="Assets/Prefabs/Player.prefab")

# Project shape before a refactor
manage_project(action="project_health")      # aggregate asset + material health, per-category healthy flag
```

`find_references` / `unused_assets` / `audit_mobile` results marked `advisory` are heuristics and cannot see Addressables/AssetBundles/reflection loads — read the returned `caveats` and treat them as "worth checking", not settled fact.

### 0d. Reason it through

Before writing a plan item, answer:

1. **What is actually being asked?** Strip the surface request to the underlying system or mechanic. Feature, refactor, perf fix, tooling, integration, or a combination?
2. **What don't I know that could invalidate the plan?** Unity version constraints, third-party SDK behaviour, platform targets, existing dependencies. Resolve from the project (0c) where possible; spawn a `/researcher` request only for genuinely external unknowns:

   > **Research request → /researcher**: [precise question with context. E.g. "GC/memory implications of Unity 2022 `ObjectPool<T>` vs a custom array-backed pool for 60fps bullet spawn/despawn on mobile?"]

   Integrate findings before finalising. Spawn multiple targeted requests rather than one bundled question.
3. **What are the two or three fundamentally different ways to implement this?** Don't default to the first idea. Consider alternatives, commit to one, justify it.

If the task touches UI (menus, HUD, panels, RectTransform, Canvas, UXML/USS, TMP, safe-areas, design tokens, accessibility), the architectural shape belongs to `unity-ui-design` — read it for the uGUI-vs-UI-Toolkit detection, design-token requirements, and prefab ladder the plan must respect, and cite which parts apply rather than inventing UI structure inline. If the codebase is unreviewed and the task touches existing systems, recommend `unity-project-doctor` (fast read-only diagnosis) or `unity-review` (senior code/architecture audit) first, and name which of their findings would change this plan.

---

## Step 1 — Context Assessment

State the following before the plan:

**What I understand about the task** — a precise, technical restatement of what is being implemented. If ambiguous, state the interpretation you are committing to and why.

**Relevant existing systems** — which scripts, prefabs, scenes, or systems this touches, extends, or depends on. Name them specifically. Verify against the live scene with `query_scene` (and `manage_scene(action="get_hierarchy"|"get_active")` for structure) — source files and the scene diverge, especially when scene content is generated procedurally. Cite the blast radius you found in 0c for any asset the plan will modify.

**Constraints and risks identified**
- Unity version limitations relevant to this task
- Platform targets that constrain the approach (mobile GC pressure, WebGL single-threaded limits, console cert requirements)
- Third-party dependencies involved
- Known technical debt in adjacent systems (cite the `project_health` / console-summary evidence, don't assert it)
- Performance budget — is this on a hot path? If so, ground it in a `manage_profiler` reading, not a guess.

**Chosen approach and rationale** — the strategy chosen and why it beat the alternatives. Be honest about trade-offs.

---

## Step 2 — Implementation Plan

Break the work into sequenced phases. Each phase must be independently completable and testable — no phase may require simultaneous partial completion of another.

### Phase N — [Phase Name]

**Goal**: what this phase achieves, one sentence.

**Scope**: which files, systems, prefabs, or scenes are created or modified.

**Implementation steps**: numbered, concrete, unambiguous. Include:
- Class names, method signatures, or interface shapes where the design is clear enough to specify
- Unity specifics (which lifecycle methods, which threading model, which Unity API — verified via `unity_reflect` if there was any doubt it exists)
- Serialisation decisions (Inspector vs code-driven vs ScriptableObject)
- Ordering constraints within the phase

**Acceptance criteria**: observable, verifiable outcomes — not "it works" but "spawning 200 projectiles shows zero GC allocs/frame in the profiler" or "`GameState.Playing → GameOver` reliably drives the correct UI with no edge case on rapid repeat presses." Write criteria a routed verification skill can actually check.

**Risks in this phase**: what could go wrong here specifically, and how it's handled.

---

## Step 3 — Architecture Decisions Record

For every significant design decision:

**Decision** — what was decided.
**Context** — why a decision was needed.
**Options considered** — what else was on the table.
**Rationale** — why this option won.
**Consequences** — what it forecloses or commits to.

Minimum one ADR per full plan. More for complex tasks.

---

## Step 4 — Unity-Specific Engineering Notes

Implementation-level guidance the executor must keep in mind. Tailor entirely to this task — no generic Unity tips; every note must be directly relevant.

Cover as applicable:

- **Lifecycle ordering** — if MonoBehaviours interact, document required execution order and how it's enforced (Script Execution Order, explicit init sequencing, lazy init).
- **Memory and GC** — flag every heap-allocating pattern and its mitigation: pooling, pre-allocation, structs, cached delegates, cached `WaitForSeconds`.
- **Threading** — for Jobs/Burst/async/background work, document exactly what runs on which thread and how Unity API access is gated to the main thread.
- **Serialisation** — what persists across Play Mode, scenes, or sessions, and the `[SerializeField]`/`[NonSerialized]` decisions.
- **Editor vs Runtime** — anything that behaves differently in the Editor (domain reload, `[InitializeOnLoad]`, `OnValidate`).
- **Physics** — Rigidbody/collider/trigger/raycast cadence and any FixedUpdate/Update sync concerns.
- **Prefab workflow** — standard vs Variant vs Nested, and instantiation strategy (scene-placed, pooled, Addressables).
- **Input handling** — which Input System, and where in the lifecycle input is read.
- **Platform considerations** — anything that differs across targets and how it's handled.
- **UI-specific notes (if task touches UI)** — delegate to `unity-ui-design`; do not duplicate UI architecture inline. Reference the sections that apply (e.g. "design tokens via existing `DesignTokens.asset`; new panels as prefab variants of `Button_Base`; safe-area via existing `SafeAreaPanel`"). The plan specifies *what* UI is built and how it integrates; the UI skill owns the discipline.

---

## Step 5 — Testing Strategy

**EditMode tests** — logic testable without Play Mode: data structures, state machines, utilities, ScriptableObject logic. List what to cover.

**PlayMode tests** — gameplay interactions, scene transitions, runtime I/O. List what to cover.

**Manual verification** — for anything unautomatable, a precise procedure with specific inputs and expected outputs.

**Profiler checkpoints** — if performance matters, specify what to measure, in which profiler (CPU, Memory, Frame Debugger, Physics), under what conditions (device, scene state, stress). Drive captures via `manage_profiler` so measurements are reproducible.

**Running tests** — routed to `unity-test-pilot` / `unity-test-run`. Note in the plan that the `testing` tool group is **disabled by default** — those skills teach `manage_tools(action="activate", group="testing")`; the plan just needs to flag that tests are VALIDATE-class (need `review_only`+ to run) and that no test criterion is "met" without a cited completed job id.

---

## Step 6 — Sequencing & Effort Estimate

| Phase | Estimated effort | Blocking dependency | Confidence |
|-------|-----------------|--------------------|----|
| Phase 1 | X hours | None | High / Medium / Low |
| Phase 2 | X hours | Phase 1 complete | High / Medium / Low |

**Total estimate**: X–Y hours.

**Confidence level**: High / Medium / Low — with an honest explanation of what drives the uncertainty. No false precision.

**What would cause this plan to be wrong**: the top 2–3 assumptions that, if invalidated, force significant re-planning.

---

## Step 7 — Execution Routing

Planning stops here; execution is other skills' work. Route each phase to the right skill — and gate the whole handoff on the mode from Step 0a.

**Mode gate**: if Step 0a found `read_only`, the plan ships as a recommendation only — say plainly "execution requires `write` mode; the server is in `read_only`, so no edits/tests/builds were run." In `review_only`, verification steps can run but persistent edits cannot. Only in `write` does the full routing execute (destructive steps carry their own `confirm:true`).

| When | Route to | Lane |
|---|---|---|
| Any project driving, orientation, or a refused call | `unity-mcp-operator` | Base operating layer — posture, evidence tiers, verify-before-claim. |
| Codebase not yet understood; "is it healthy?" | `unity-project-doctor` | Read-only whole-project diagnosis (asset/material/prefab health, build pre-flight, dependencies). |
| Senior architecture / code-quality audit needed first | `unity-review` | Structure, architecture, Unity-pattern and performance audit that a plan consumes. |
| Any UI-touching work | `unity-ui-design` | Owns TMP/anchors/tokens/prefab-first/Canvas-split and component recipes — don't author UI inline. |
| UI must work across resolutions / "why can't I click this?" | `unity-ui-auditor` | Analytic uGUI resolution-matrix audit (advisory, screenshot-free). |
| Scene must satisfy invariants (required/forbidden objects, caps, wiring) | `unity-scene-contracts` | Author + validate a JSON scene contract against the loaded scene. |
| Each C# create/modify/delete | `unity-edit-verify` | The canonical read → hash-guarded edit → validate → **wait for compilation** → console loop. Script edits already trigger import + compile — do **not** plan a `refresh_unity` after an edit; the skill waits on `editor_state.compilation.is_compiling → false`, then reads `read_console(types=["error"])`. No phase is complete until the console shows zero new errors. |
| Risky multi-object scene surgery a mistake would make expensive | `unity-safe-iteration` | Checkpoint → change → verify → keep-or-rollback. Restore is DESTRUCTIVE (needs `write` + `confirm:true`). |
| Procedural scene regen after structural code change | scene-rebuild skill (`unity-scene-rebuild` is a project-specific template) | Re-run generation, verify exact expected object counts, save, confirm no new console errors. |
| Tests in Step 5 | `unity-test-pilot` (run + triage, flaky-vs-deterministic, smoke test) / `unity-test-run` (job-id-cited result) | Cite the job id in the implementation report; a "tests pass" claim without one is invalid. |
| Something misbehaves at runtime mid-implementation | `unity-debug` | Diagnose with evidence before changing code — don't patch symptoms. |
| Project has a rules-gate | `sw-rules-guard` is a project-specific template | Confirm the planned change doesn't violate non-negotiable architectural rules before editing. |

---

## Output Discipline

- **Be specific.** Name files, classes, methods, Unity APIs. Not "create a manager script" — "create `EnemySpawnManager.cs` as a MonoBehaviour on the `[GameSystems]` root with `[SerializeField] EnemyWaveConfigSO[] waveConfigs` and an internal `ObjectPool<EnemyController>`."
- **Ground every project claim in observed evidence.** Cite the tool and what it returned. Mark heuristic findings (`advisory`) as "worth checking", and mark any live-scene claim as unverified if no Unity instance was connected. Never state a project fact you did not read.
- **No padding.** Every sentence carries information the executor needs.
- **Make architectural decisions.** If a choice is genuinely ambiguous, present the two options with a clear recommendation and state what information would resolve it.
- **Flag scope creep.** If the task will pull in adjacent work, name it so it can be scoped in or out deliberately.
- **Fit the mode.** A plan that assumes `write` when the session is `read_only` is a broken plan — the routing in Step 7 must match the posture from Step 0a.
- Think about the developer reading this at 9am tomorrow. They should open it and know exactly what to do first.
