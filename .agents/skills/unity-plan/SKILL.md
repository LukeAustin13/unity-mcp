---
name: unity-plan
description: Produces an implementation plan for any Unity development task — feature, system, refactor, optimisation, or integration. Use this skill when asked to plan, design, or architect anything in the Unity project, before code is written. Scales plan depth to task size, investigates the live project via MCP before committing to an approach, and routes execution through the companion unity skills. It does not diagnose runtime failures (use unity-debug) or audit existing project health (use unity-review).
license: MIT
---

Plan before building. Write plans a mid-level developer could execute with confidence and a senior developer would trust as technically sound: systems, trade-offs, and sequenced risk reduction — not isolated tasks. Every statement the plan makes about the existing project must come from actual investigation (MCP tool output, file reads, searches), never from assumption.

The user will provide a task. It may be a feature, a system, a refactor, a performance fix, a tooling addition, or an integration. Your job is to produce a complete, specific, and executable implementation plan for it.

---

## Right-Size the Plan

Match plan weight to task weight before writing anything:

- **Compact plan** — single system, roughly ≤3 files, no cross-system impact, no unresolved unknowns. Produce: the context assessment (Step 1), one numbered implementation sequence with acceptance criteria, and the execution routing (Step 7). Skip the ADR, phase tables, and effort estimates.
- **Full plan** — multiple systems, architectural decisions, unknowns needing research, or performance-critical work. Run every step below.

When in doubt, start compact and escalate if investigation reveals hidden coupling. A five-phase plan for a one-script change is itself a planning failure.

---

## Step 0 — Understand Before Planning

Before writing a single plan item, reason through the following:

1. **What is actually being asked?** Strip away the surface request and identify the underlying system or mechanic. Is this a feature, a refactor, a performance fix, a tooling addition, an integration, or a combination?
2. **What do I not know that could invalidate the plan?** Identify any technical unknowns — Unity version constraints, third-party SDK behaviour, platform targets, existing system dependencies — that require investigation before committing to an approach.
3. **What are the two or three fundamentally different ways this could be implemented?** Do not default to the first approach that comes to mind. Consider alternatives. Then commit to one and justify it.

**MCP-assisted investigation**: Before or instead of spawning `/researcher` for questions resolvable from the live project, use the Unity MCP read tools to investigate directly:
- `find_in_file` — search for call sites, patterns, or class references across the codebase.
- `find_gameobjects` — inspect what is actually instantiated in the scene at runtime; source code and the scene can diverge, especially in projects that generate scene content procedurally.
- `unity_reflect` — enumerate types, methods, and component properties from the loaded assemblies without reading every source file.
- `unity_docs` — resolve Unity API questions without spinning up an external agent.
- `manage_profiler` — for performance-flavoured planning tasks, drive a real profiler session to ground the plan in measured data rather than guessed bottlenecks.

**Skill-assisted investigation**: if the task touches UI (menus, HUD, panels, RectTransform, Canvas, UXML/USS, TMP, mobile safe-areas, design tokens, accessibility), consult `/unity-ui-design` for the UI-specific investigation patterns and constraints. Do not plan UI inline if a UI-touching plan is required — the UI skill owns the architectural shape (uGUI vs UI Toolkit detection, design-token requirements, atomic prefab ladder, Canvas-split rules) that the plan must respect. Cite which sections of `/unity-ui-design` apply.

Reserve `/researcher` for unknowns that cannot be resolved from the project itself: third-party package internals, platform-specific runtime behaviour, or Unity version edge cases.

If there are significant unknowns that require external research (Unity API specifics, a package's internal behaviour, platform constraints, a design pattern's suitability), **spawn a `/researcher` subagent** with a precise research question before proceeding. If no researcher skill is installed in the project, run the investigation directly with `unity_docs` and web search — do not skip it. Format the research request clearly:

> **Research request → /researcher**: [Specific question with enough context to get a precise answer. E.g.: "What are the memory and GC implications of Unity 2022's ObjectPool<T> vs a custom array-backed pool for high-frequency bullet spawn/despawn at 60fps on mobile?"]

Integrate the research findings before finalising the plan. If multiple research questions exist, spawn multiple targeted requests — do not bundle unrelated questions into one.

If the codebase has not been reviewed yet and the task touches existing systems, recommend running `/unity-review` first and state which specific findings would affect this plan.

---

## Step 1 — Context Assessment

State the following before the plan:

**What I understand about the task**
A precise restatement of what is being implemented, in technical terms. If the task is ambiguous, state the interpretation you are committing to and why.

**Relevant existing systems**
Which existing scripts, prefabs, scenes, or systems does this task touch, extend, or depend on? Name them specifically. Use `find_gameobjects` and `manage_scene` (read) to verify what is actually present in the live scene — do not rely solely on source files, which can be out of sync with the scene, especially when scene content is generated procedurally.

**Constraints and risks identified**
- Unity version limitations relevant to this task
- Platform targets that constrain the approach (mobile GC pressure, WebGL single-threaded limitations, console certification requirements, etc.)
- Third-party dependencies involved
- Known technical debt in adjacent systems that could complicate this work
- Performance budget considerations (is this on a hot path?)

**Chosen approach and rationale**
State the implementation strategy chosen and briefly explain why it was preferred over the alternatives considered. Be honest about trade-offs.

---

## Step 2 — Implementation Plan

Break the work into sequenced phases. Each phase must be independently completable and testable. Do not write phases that require simultaneous partial completion of other phases.

For each phase:

### Phase N — [Phase Name]

**Goal**: What this phase achieves in one sentence.

**Scope**: Which files, systems, prefabs, or scenes are created or modified.

**Implementation steps**: Numbered, concrete steps. Each step must be specific enough that there is no ambiguity about what to do. Include:
- Class names, method signatures, or interface shapes where the design is clear enough to specify
- Unity-specific implementation details (which lifecycle methods, which threading model, which Unity API)
- Serialisation decisions (what goes on the Inspector vs driven by code vs lives in ScriptableObjects)
- Any ordering constraints within the phase

**Acceptance criteria**: How do you know this phase is done and correct? State observable, verifiable outcomes — not "it works" but "spawning 200 projectiles in the profiler shows zero GC allocations per frame" or "transitioning from `GameState.Playing` to `GameState.GameOver` reliably triggers the correct UI state with no edge cases on rapid repeat presses."

**Risks in this phase**: What could go wrong here specifically, and how should it be handled?

---

## Step 3 — Architecture Decisions Record

For every significant design decision made in this plan, document it:

**Decision**: [What was decided]
**Context**: [Why a decision was needed]
**Options considered**: [What else was considered]
**Rationale**: [Why this option was chosen]
**Consequences**: [What this decision forecloses or commits to]

Minimum one ADR per plan. More for complex tasks.

---

## Step 4 — Unity-Specific Engineering Notes

Implementation-level guidance specific to Unity that the executing developer must keep in mind. Tailor this entirely to the task at hand — do not produce generic Unity tips. Every note must be directly relevant to what is being built.

Cover as applicable:

- **Lifecycle ordering**: If multiple MonoBehaviours interact, document the required execution order and how it will be enforced (Script Execution Order settings, explicit initialisation sequencing, lazy initialisation patterns).
- **Memory and GC**: Flag any patterns in the plan that will allocate on the heap. Specify mitigation: pooling, pre-allocation, struct usage, cached delegates, cached `WaitForSeconds`, etc.
- **Threading**: If any part of this work touches Jobs, Burst, async/await, or background threads — document exactly what runs on which thread and how Unity API access is gated to the main thread.
- **Serialisation**: What data persists across Play Mode, between scenes, or across sessions? Document the serialisation strategy and any `[NonSerialized]` / `[SerializeField]` decisions.
- **Editor vs Runtime behaviour**: If any system behaves differently in the Editor (e.g., domain reload, `[InitializeOnLoad]`, `OnValidate`), document it explicitly.
- **Physics interactions**: If Rigidbodies, colliders, triggers, or raycasts are involved — document the expected physics update cadence and any FixedUpdate/Update synchronisation concerns.
- **Prefab workflow**: If new Prefabs are being created — document whether they are standard Prefabs, Prefab Variants, or Nested Prefabs, and the intended instantiation strategy (scene-placed, pooled, Addressables).
- **Input handling**: If this task involves input — document which Input System is being used, and where in the lifecycle input is read.
- **Platform considerations**: Flag anything in the plan that behaves differently across target platforms and how it will be handled.
- **UI-specific notes (if task touches UI)**: Delegate to `/unity-ui-design`. Do not duplicate UI architecture decisions inline. Reference the specific sections that apply (e.g., "UI follows `unity-ui-design` cardinal rules; design tokens via existing `DesignTokens.asset`; new panels as prefab variants of `Button_Base`; safe-area handling via the existing `SafeAreaPanel` script"). The plan should specify *what* UI is built and *how* it integrates with the systems above, while delegating the UI implementation discipline to the dedicated skill.

---

## Step 5 — Testing Strategy

**EditMode tests**
Logic that can be tested without entering Play Mode — data structures, state machines, utility classes, ScriptableObject logic. List what should be covered.

**PlayMode tests**
Gameplay interactions, scene transitions, system inputs/outputs that require the Unity runtime. List what should be covered.

**Manual verification steps**
For anything that cannot be automated, provide a precise manual test procedure with specific inputs and expected outputs.

**Profiler checkpoints**
If this task has performance implications, specify exactly what to measure, in which profiler (CPU Profiler, Memory Profiler, Frame Debugger, Physics Debugger), under what conditions (device type, scene state, stress conditions). Drive captures via `manage_profiler` where possible so the measurements are reproducible.

**Running tests**: Use `/unity-test-run` to execute EditMode or PlayMode tests and capture a verifiable job-id result. Do not mark test criteria as met without a cited completed run.

---

## Step 6 — Sequencing & Effort Estimate

| Phase | Estimated effort | Blocking dependency | Confidence |
|-------|-----------------|--------------------|----|
| Phase 1 | X hours | None | High / Medium / Low |
| Phase 2 | X hours | Phase 1 complete | High / Medium / Low |

**Total estimate**: X–Y hours

**Confidence level**: High / Medium / Low — with an honest explanation of what drives uncertainty. Do not give false precision.

**What would cause this plan to be wrong**: State the top 2–3 assumptions that, if invalidated, would require significant re-planning.

---

## Step 7 — Execution Companion Skills

When this plan is handed off for implementation, route each phase through the appropriate skill:

- **Before any code edit** — if the project has a rules-gate skill (a project-specific compliance check; see `sw-rules-guard` as a template), run it to confirm the planned change does not violate the project's non-negotiable architectural rules.
- **For any UI-touching work** — run `/unity-ui-design`. It owns the cardinal rules (TMP over Text, anchors before pixels, design tokens before colours, prefab-first, Canvas-split for perf, screenshot-verify loop) and the component recipes. Do not author UI inline against ad-hoc patterns.
- **Each code change** — follow `/unity-edit-verify` (read → hash-guarded edit → validate_script → refresh_unity → poll isCompiling → read_console). No phase is complete until the console shows zero new errors.
- **After structural scene changes** — if the project generates scene content procedurally, run its scene-rebuild skill (see `unity-scene-rebuild` as a template) to re-run generation, verify the expected objects exist (exact counts, not just existence), save the scene, and confirm no new console errors.
- **Test verification** — use `/unity-test-run` for any EditMode or PlayMode tests listed in Step 5. Cite the job id in the implementation report.
- **If something misbehaves at runtime during implementation** — use `/unity-debug` to diagnose with evidence before changing code. Do not patch symptoms mid-phase.

---

## Output Discipline

- Be specific. Name files, classes, methods, Unity APIs. Do not say "create a manager script" — say "create `EnemySpawnManager.cs` as a MonoBehaviour on the `[GameSystems]` root GameObject with a `[SerializeField] EnemyWaveConfigSO[] waveConfigs` and an internal `ObjectPool<EnemyController>`."
- Do not pad. Every sentence must carry information the executing developer needs.
- Make architectural decisions. If a choice is genuinely ambiguous, present the two options with a clear recommendation and state what information would resolve the ambiguity.
- Flag scope creep risks. If the task will inevitably pull in adjacent work, name it explicitly so it can be scoped in or out deliberately.
- Think about the developer reading this at 9am tomorrow. They should be able to open this plan and know exactly what to do first.
