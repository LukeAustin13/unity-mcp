---
name: unity-review
description: Performs a comprehensive, senior-level audit of a Unity game project — covering repo structure, architecture, code quality, Unity-specific patterns, performance, and engineering health. Use this skill when asked to review, audit, or assess the current state of the Unity project before planning, refactoring, or onboarding.
license: MIT
---

Conduct the review as if you are being handed this codebase from another team and need to form a complete, honest engineering assessment before taking ownership of it. Do not produce surface-level observations: every finding is specific, evidenced, and actionable. Read before concluding.

---

## Scope the audit first

Decide the audit scope before running any phase, and state it in the report header:

- **Full audit** — onboarding, pre-refactor assessment, or "review the project". Run all phases.
- **Focused audit** — the user named a specific area ("review the combat code", "is our UI performant?"). Run Phase 0 plus only the relevant phases, and list the skipped phases in the report so the reader knows what was not inspected.

Do not run a five-phase exhaustive audit when the user asked about one system.

---

## Evidence rule (non-negotiable)

Every finding in the final report must cite the evidence that produced it: the exact `find_in_file` pattern that found it, the file path and line, the `read_console` excerpt, the `find_gameobjects` query result, or the `unity_reflect` output. A finding without cited evidence is an opinion, not an audit observation. If you cannot cite, you have not investigated — go investigate.

---

## MCP Investigation Tools

Check the `mcpforunity://custom-tools` resource first — this project may expose custom investigation capabilities.

The following read-only MCP tools accelerate the audit. Use them in preference to manual file inspection where they apply; every "are there any X patterns?" question in the phases below must be answered by actually searching, not by inference.

| Tool | When to use |
|---|---|
| `find_in_file` | Pattern searches across scripts — `FindObjectOfType`, `FindFirstObjectByType`, `GetComponent` in hot paths, `sharedMaterial`, LINQ in `Update`, magic numbers, legacy `Input.*` API, etc. Faster and more complete than reading files individually. |
| `find_gameobjects` | Inspect actual scene hierarchy and component configuration. Cross-check against what source code implies — they can diverge, especially in projects that generate scene content procedurally. |
| `unity_reflect` | Enumerate compiled types, fields, and methods from loaded assemblies without reading every source file. Useful for confirming what is actually compiled vs what source suggests. |
| `read_console` | Capture active errors and warnings from the Editor. A console full of unacknowledged warnings is itself a finding. Run this in Phase 0 and include the baseline console state in your findings. |
| `manage_scene` | Query scene metadata: loaded scene name, path, dirty state. |
| `manage_profiler` | Drive a real profiler session (start, capture frames, read CPU/memory counters, take memory snapshots, list expensive frames). Performance findings should be backed by profiler data, not inference — use this in Phase 4. |
| `manage_editor` | Read build target, scripting backend, API compat level, and other player settings programmatically. |
| `unity_docs` | Resolve Unity API questions during the review without leaving the audit context. |

---

## Phase 0 — Baseline Capture

Run this before any other phase. Findings about "X is wrong" only make sense against a recorded baseline.

1. **Console baseline.** Call `read_console(types=["error","warning"], count=50)`. Record current error and warning counts. This is the line against which "no new errors" claims will be measured throughout the audit.
2. **Active scene state.** Call `manage_scene(action="status")` (or equivalent). Record loaded scene name, path, and dirty flag. If the scene is dirty, note it — uncommitted changes may invalidate later structural findings.
3. **Build target & player settings.** Call `manage_editor(action="get_settings")` (or `get_build_target`). Record: scripting backend (Mono / IL2CPP), API compatibility level, target platform, colour space (Linear / Gamma), graphics APIs. These shape what is and isn't a finding in later phases (e.g. Gamma colour space is a finding unless documented as deliberate).
4. **Editor & Unity version.** Note the Unity version and Editor state (Play Mode? Compiling?). The audit should not be performed while compiling — wait for `editor_state.isCompiling == false`.

Include the baseline as a short header in the final report.

---

## Phase 1 — Repository & Project Structure Audit

Examine the full file and folder layout first. Evaluate:

**Folder Organisation**
- Does the `Assets/` hierarchy follow a coherent convention? (feature-based, type-based, or hybrid). Flag ambiguity or inconsistency.
- Are `Scripts/`, `Prefabs/`, `Scenes/`, `Materials/`, `Shaders/`, `Animations/`, `Audio/`, `UI/`, `Resources/`, `StreamingAssets/` and `Editor/` folders structured deliberately or haphazardly?
- Identify any files sitting at incorrect hierarchy depths — scripts directly under `Assets/`, prefabs mixed with scenes, etc.
- Are Editor-only scripts correctly isolated under `Editor/` folders to avoid inclusion in builds?
- Are third-party assets clearly separated from first-party code? Identify any vendor code that has been modified in-place (a maintenance risk).

**Assembly Definitions (`.asmdef`)**
- Are Assembly Definitions in use? If not, flag the compile-time and iteration-speed implications.
- If present: do they reflect logical ownership boundaries? Are circular dependencies present? Are Editor assemblies correctly restricted to Editor-only platforms?
- Are test assemblies (`EditMode`, `PlayMode`) present and correctly configured?

**Version Control Hygiene**
- Inspect `.gitignore` — are `Library/`, `Temp/`, `Obj/`, `Build/`, `Logs/`, `.vs/`, `*.csproj`, `*.sln` correctly excluded?
- Are large binary assets tracked directly in git, or is Git LFS in use?
- Are there any committed secrets, API keys, or environment-specific config files that should be excluded?

**Project Settings**
- Inspect `ProjectSettings/` files directly. Flag: scripting backend (Mono vs IL2CPP), API compatibility level, target architectures, graphics API selections, colour space (Linear vs Gamma — flag Gamma unless documented), Physics layer collision matrix bloat, tag and layer sprawl.
- Is `Enter Play Mode Settings` configured? Faster iteration with domain/scene reload disabled is a sign of a mature project.
- Is the Input System old, new, or both? Flag dual-input-system usage as a risk.
- Is `Application.targetFrameRate` set anywhere in code? On mobile, leaving it unset (default -1 = platform default) is almost always a finding.

---

## Phase 2 — Architecture & Systems Design Review

This is the highest-signal section. Go deep.

**MonoBehaviour Usage Patterns**
- Are MonoBehaviours used as pure view/controller components, or are they acting as data stores, service locators, and God objects simultaneously?
- Identify any MonoBehaviours with more than ~300 lines — these are almost always violating single responsibility.
- Is `Update()`, `FixedUpdate()`, and `LateUpdate()` usage deliberate? Flag any empty Unity event methods (they still incur a native-to-managed call overhead).
- Are there polling patterns in `Update()` that should be event-driven?
- Is there evidence of `FindObjectOfType`, `FindFirstObjectByType`, `FindAnyObjectByType`, `GameObject.Find`, or `SendMessage` in runtime code? Catalogue every occurrence. Use:
  ```
  find_in_file(pattern="FindObjectOfType|FindFirstObjectByType|FindAnyObjectByType|GameObject\\.Find|SendMessage", files="*.cs")
  ```
  Note: `FindObjectOfType` was deprecated in Unity 2023+ in favour of `FindFirstObjectByType` / `FindAnyObjectByType`. If both APIs appear in the same project, flag the inconsistency — pick one.

**Dependency Management**
- How are dependencies wired? Inspector references, static singletons, a service locator, dependency injection (Zenject/VContainer), or ScriptableObject-based event channels?
- Are Singletons present? Are they persistent across scenes (`DontDestroyOnLoad`)? Are they destroying duplicates on Awake correctly? Flag any Singleton managing mutable gameplay state as an architectural risk.
- Is there tight coupling between systems that should be decoupled (e.g., a `PlayerController` directly referencing `UIManager`)?

**Scene Architecture**
- How are scenes structured? Is there a dedicated bootstrapper/initialiser scene?
- Is scene loading additive or single? Is there a loading screen system?
- Are there any cross-scene dependencies baked into scenes (hard object references between scenes will break at runtime)?
- Is scene management centralised or scattered across scripts?

**Data Architecture**
- Are ScriptableObjects used for shared data/configuration, or is data hardcoded into MonoBehaviours and Prefabs?
- Is there a save/load system? What is the serialisation strategy? (PlayerPrefs-only is a red flag for anything beyond trivial data.)
- Are Runtime Sets or Event Channels in use, or equivalent?

**State Management**
- Is there a formal state machine pattern (FSM, HFSM, behaviour trees) for entities with complex state, or is state managed via boolean flag soup?
- For AI agents, animation controllers, and game flow — assess whether state is explicit and traceable.

**Event System**
- What event/messaging pattern is in use? Unity Events, C# events/delegates, ScriptableObject channels, a custom event bus, or none?
- Are there UnityEvent subscriptions that are not being unsubscribed (memory leak / stale reference risk)?
- Are there C# event subscriptions in MonoBehaviours not cleaned up in `OnDestroy`? Search:
  ```
  find_in_file(pattern="\\+= ", files="*.cs", context_lines=3)
  ```
  Then verify each subscription has a matching `-=` in `OnDestroy` / `OnDisable`.

**Object Pooling**
- Is Unity's built-in `ObjectPool<T>` (2021+) in use, a custom pool, or no pooling at all?
- Identify any `Instantiate`/`Destroy` calls in hot paths (bullet spawn, particle spawn, enemy spawn) — flag each one.

---

## Phase 3 — Code Quality Review

**Naming & Conventions**
- Are C# conventions followed consistently? Flag whichever convention is in use and flag violations.
- Are `SerializeField` attributes used in preference to making fields public?
- Are `[Header]`, `[Tooltip]`, and `[Range]` attributes used thoughtfully in Inspector-exposed code?

**SOLID Principles**
- Identify concrete violations: God classes, classes open for modification instead of extension, concrete dependencies instead of interfaces. Be specific — name the file and the violation.

**C# Quality**
- Is `string` concatenation happening in hot paths instead of `StringBuilder` or interpolation?
- Are LINQ queries used in `Update()` loops (allocation risk)? Use:
  ```
  find_in_file(pattern="\\.Where\\(|\\.Select\\(|\\.ToList\\(|\\.OrderBy\\(", files="*.cs")
  ```
  then cross-reference each hit against the enclosing method — flag any inside `Update`, `FixedUpdate`, `LateUpdate`, or per-frame coroutines.
- Are there unnecessary `GetComponent<T>()` calls in `Update()` instead of cached references?
- Are coroutines used where async/await would be more appropriate? Are `WaitForSeconds` instances cached?
- Are there any `async void` methods outside of event handlers (unhandled exception risk)?
- Is `null` checking done with `== null` on Unity objects (triggers the Unity equality operator overhead) vs `is null` (bypasses it — but bypasses lifecycle checks too; flag both deliberately)?
- Are magic numbers and strings present? Flag and suggest constants or ScriptableObject config.

**Error Handling**
- Are there bare `try/catch` blocks swallowing exceptions silently?
- Is there defensive null checking, or does the code assume references are always valid?
- Are coroutines started on potentially-inactive GameObjects (will silently fail)?

---

## Phase 4 — Performance Risk Assessment

Identify concrete patterns that are known Unity performance risks — do not speculate. Where the project is live and runnable, drive `manage_profiler` to gather real evidence rather than relying on static analysis alone.

**Profiler-backed evidence**
For any "this might be slow" finding, attempt to confirm with a profiler capture:
```
manage_profiler(action="start", areas=["CPU","Memory","Rendering","UI"])
# trigger the workload (play mode + run the suspected hot scene)
manage_profiler(action="capture_frames", count=300)
manage_profiler(action="get_top_costs", category="CPU", top=20)
```
Cite the captured timings in the finding (e.g. `Canvas.SendWillRenderCanvases averaging 4.2ms over 300 frames in MainMenu`).

**Rendering**
- Is there evidence of dynamic batching being undermined (non-uniform scale, too many materials per renderer)?
- Are `Renderer.material` (allocates a new instance) vs `Renderer.sharedMaterial` used correctly?

**Physics**
- Is `Physics.Raycast` used with appropriate layer masks to minimise broadphase cost?
- Are any Rigidbodies being moved via `Transform.position` instead of `Rigidbody.MovePosition` (breaks physics interpolation)?
- Are colliders appropriately primitive vs mesh?

**Memory & GC**
- Are there allocations in `Update()` or `FixedUpdate()` — new collections, LINQ, string operations, boxing of value types? Confirm via the Memory Profiler / `manage_profiler(action="snapshot")` rather than guessing.
- Are any large textures missing compression settings or mip maps?
- Is `Resources.Load` used at runtime (synchronous, stalls main thread) — should be Addressables?
- If Addressables are in use, are assets being released correctly to avoid memory leaks?

**Audio**
- Are `AudioSource.PlayOneShot` calls used appropriately vs pooled audio sources?
- Are audio clips set to the correct load type (Streaming for music, Decompress on Load for short SFX)?

**UI performance — delegate to `unity-ui-design`**
If the project's UI is non-trivial — multiple Canvases, dynamic lists, frequent screen updates — do not duplicate the UI audit inline. Hand off the UI portion of the review to the `unity-ui-design` skill, which owns:
- Canvas splitting (static vs dynamic) with backing profiler data
- `RaycastTarget` hygiene
- `Mask` vs `RectMask2D`
- Layout group cost and rebuild loops
- TMP allocation patterns (`SetText(StringBuilder)` vs interpolation)
- Animator-on-UI anti-pattern
- Atlasing and overdraw
- Mobile safe-area + touch-target compliance
- Design-token / prefab-variant discipline

In this skill's UI section, simply state: "UI audit delegated to `unity-ui-design` — see that report" and include its findings in the consolidated severity buckets below.

---

## Phase 5 — Tooling, Testing & Build Health

**Editor Tooling**
- Are there custom Editor scripts / PropertyDrawers / EditorWindows that suggest a mature workflow?
- Are Gizmos implemented on key components to aid scene debugging?
- Is there a CI/CD pipeline (GitHub Actions, Unity Cloud Build, etc.)?

**Testing**
- Are there EditMode or PlayMode tests? If so, run them using `/unity-test-run` and report the actual result with a job id — test existence without a passing run is not evidence of health.
- Are testable systems designed to be testable (not entirely dependent on MonoBehaviour lifecycle)?

**Build Configuration**
- Are there multiple build profiles / defines for Development vs Release?
- Are Scripting Define Symbols used to gate debug tooling out of release builds?
- Is logging wrapped in a debug utility that can be stripped in release builds, rather than raw `Debug.Log` calls throughout?
- Is there evidence of platform-specific code that is not correctly `#if`-gated?

---

## Output Format

Structure your review as follows. Every point must name the specific file, folder, pattern, or system it refers to, and cite the evidence that produced it — no vague generalisations.

### Baseline (from Phase 0)
Console state (errors / warnings), active scene, build target, scripting backend, Unity version. One-line each.

### Executive Summary
3–5 sentences. Overall health, biggest risk, and the single most important thing to fix first.

### Critical Issues
Correctness bugs, data loss risk, crash risk, or severe architectural problems that will compound. Must fix before any new feature work. Each item: **path** + **evidence cite** + **one-line fix**.

### High Priority
Significant technical debt, performance risks under load, or patterns that will cause pain at scale. Address in the near term. Same citation rule.

### Medium Priority
Code quality, convention violations, missing tooling. Track and address incrementally.

### Low Priority / Nice-to-Have
Improvements that would raise quality but are non-urgent.

### Strengths
What this codebase does well. Be honest and specific — do not fabricate positives.

### Recommended Next Actions
An ordered list of the 5–10 most impactful changes, written as concrete tasks. Each must reference the specific system or file it targets.

---

### Companion Skills for Follow-up

After completing the review, route subsequent work as follows:
- Use `/unity-plan` to produce an implementation plan for any Critical or High Priority finding before writing code.
- Use `/unity-ui-design` for any UI-touching follow-up (refactor, restyle, audit deep-dive).
- If the project has a rules-gate skill (a project-specific compliance check; see `sw-rules-guard` as a template), run it before any code change touching the systems it guards.
- Use `/unity-edit-verify` for every code change — compile confirmation via `read_console` is not optional.
- If the project generates scene content procedurally, run its scene-rebuild skill (see `unity-scene-rebuild` as a template) after any structural change that affects generation.
- Use `/unity-test-run` to validate that any fix did not introduce regressions — cite the job id.
- Use `/unity-debug` when a finding involves runtime misbehaviour whose cause is not yet proven — diagnose before fixing.

---

Be direct. Be specific. Do not soften findings for comfort. A lead developer's job is to give an honest picture so that good decisions can be made.
