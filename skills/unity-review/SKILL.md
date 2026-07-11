---
name: unity-review
description: Senior-level whole-project audit of a Unity project through MCP for Unity — repo structure, architecture, code quality, Unity-specific patterns, performance risk, and engineering health, delivered as prioritized findings with cited evidence. Use when asked to "review the project", "audit the codebase", "assess the state of this Unity project", or before planning a refactor or onboarding. Layers senior judgment on top of the factual health scans that unity-project-doctor produces. Minimum safety mode read_only for the whole audit; the optional test leg (run_tests_and_summarize) and a live profiler session need review_only. This skill never fixes — it reports.
license: MIT
---

# Unity Review

A whole-project engineering assessment, conducted as if you are taking ownership of this codebase from another team. Every finding is specific, evidenced, and actionable — no surface-level observations. **Read before concluding.**

Follow the `unity-mcp-operator` loop (orient → cheapest evidence → report what you observed). This is a **read-only** activity: plan it to end in a findings report with suggested fixes, never in edits. Applying a fix is a separate `write`-mode task routed through the follow-up skills below.

## Lane — what this skill is (and is not)

| Skill | Owns |
|---|---|
| **`unity-project-doctor`** | Fast, factual health *scans* (asset/material/prefab validity, build pre-flight, console pulse). Pure facts. |
| **`unity-review`** (this) | Senior *judgment* — architecture, code quality via reading scripts, Unity anti-patterns, engineering health, priorities. **Consumes the doctor's scans as its evidence base.** |
| **`unity-ui-auditor`** | The analytic uGUI layout/interaction audit (`audit_ui_layout`). Delegate UI-layout findings here. |
| **`unity-scene-contracts`** | Scene-invariant validation (`validate_scene_contracts`). Delegate contract checks here. |
| **`unity-test-run` / `unity-test-pilot`** | Running tests and triaging failures. Delegate the test leg here. |

Do not re-implement the doctor's scans by hand — run them (or invoke `/unity-project-doctor`) and interpret the output. Your value is the judgment on top.

## Orient and scope first

Two reads before any phase (operator §1):

```python
safety_status()                    # confirm read_only is enough; note if the test leg is blocked
```
```text
mcpforunity://editor/context       # resource: editor state + selection + prefab stage + console counts
```

If `mcpforunity://editor/state` reports `compilation.is_compiling: true` or `advice.ready_for_tools: false`, **wait** — poll `compilation.is_compiling` until false before trusting any read. If no Unity instance is connected, stop and say so — a whole-project audit needs the live editor.

Then choose scope and state it in the report header:

- **Full audit** — "review the project", onboarding, pre-refactor. Run all phases.
- **Focused audit** — the user named one area ("review the combat code", "is the UI performant?"). Run Phase 0 plus only the relevant phases, and **list the skipped phases** so the reader knows what was not inspected.

Check `mcpforunity://custom-tools` — this project may expose custom investigation tools worth using.

## Evidence rule (non-negotiable)

Every finding cites the evidence that produced it. A finding without a cite is an opinion, not an audit observation. Valid cites:

- A `query_scene` / `find_gameobjects` result (object path + instance id).
- A `manage_project` / `validate_build` finding (its `rule_id`/`check` + `severity` + `suggested_fix`).
- A `read_console` signature (`format="summary"` group: count + `first_file_line`).
- A source location (`file:line`) from your own file search or `find_in_file`.

Mark heuristic findings **advisory** and report them as "worth checking", not as defects — this covers `audit_mobile`, `unused_assets`, `prefab_health` entries flagged `advisory`, and `audit_ui_layout`. Never claim a check ran that you did not run.

## Where the facts come from

The old hand-rolled scans are replaced by the fork's read-only audit tools. Match each question to the tool that actually answers it — do not guess a signature.

| Question | Current tool / resource | Notes |
|---|---|---|
| Aggregate project health, missing scripts, pink materials, broken prefabs | `manage_project(action="project_health" \| "validate_assets" \| "validate_materials" \| "prefab_health")` | READ, core. Paged (`page_size`+`cursor`), capped (`max_issues`). |
| Dependency graph: what uses / what breaks / what's orphaned | `manage_project(action="get_dependencies" \| "find_references" \| "unused_assets", target=...)` | `unused_assets` is **advisory** — blind to Addressables/Resources/reflection. |
| Mobile import/settings risks (mobile targets only) | `manage_project(action="audit_mobile")` | **Advisory** static heuristics, not a profiler. Read the returned `caveats`. |
| Would a build for target X succeed? | `validate_build(target="android")` | READ pre-flight. Targets: windows64, osx, linux64, android, ios, webgl, uwp, tvos, visionos. Omit for active target. |
| "How big is every X, what's on it, what materials?" | `query_scene(component_type=..., include=["world_bounds","components","materials"])` | Replaces find→get-components→get-properties loops. READ, paged. |
| Find objects by name/tag/layer/component | `find_gameobjects(search_term, search_method="by_component")` | Returns instance ids; hydrate via `mcpforunity://scene/gameobject/{id}`. |
| Loaded scene name / list | `manage_scene(action="get_active" \| "get_loaded_scenes")` | There is **no** `status` action. |
| Console baseline / error triage | `read_console(action="get", types=["error","warning"], count=50, format="summary")` | `summary` dedupes to signatures with counts — one bug ≠ 500 lines. |
| Build target & scripting backend / defines / bundle id | `manage_build(action="platform")` (target); `manage_build(action="settings", property="scripting_backend" \| "defines" \| "architecture" \| "bundle_id")` | READ, core. There is **no** `manage_editor(action="get_settings")`. |
| Unity version, platform, project root | `mcpforunity://project/info` | Static resource. |
| Physics collision-matrix bloat / physics settings | `manage_physics(action="get_collision_matrix" \| "get_settings")` | READ, core. |
| Tag / layer sprawl | `mcpforunity://project/tags`, `mcpforunity://project/layers` | Resources. |
| Colour space, graphics APIs, API compat, Enter-Play-Mode settings | Read `ProjectSettings/*.asset` on disk with your file tools | No MCP tool exposes these; cite the file. |
| Codebase-wide code patterns (`FindObjectOfType`, LINQ in `Update`, `+=` without `-=`) | Your own file search (Grep/Glob/Read) over `Assets/**/*.cs` | See the note below. |
| Confirm a type/member is really compiled | `unity_reflect(action="search" \| "get_type")` | Group `docs` — **off by default**; activate first (below). |

**`find_in_file` searches ONE file, not a glob.** Its signature is `find_in_file(uri, pattern, max_results, ignore_case)` — `uri` is a single file under `Assets/`; there is no `files="*.cs"` or `context_lines` parameter. For whole-codebase pattern sweeps use your native file-search tools (Grep/Glob/Read) over the project's `Assets/` on disk — complete and cheap. Reserve `find_in_file` for a targeted regex inside one known file through the bridge.

**Group activation.** Only the `core` group is on by default. The `testing`, `profiling`, and `docs` tools you may want are hidden until activated:

```python
manage_tools(action="list_groups")                 # see what's available/active
manage_tools(action="activate", group="testing")   # or "profiling", "docs"
```

## Phase 0 — Baseline capture

Record the line every later "X is wrong" is measured against. Include it as a short report header.

1. **Console.** `read_console(action="get", types=["error","warning"], count=50, format="summary")` — record error/warning counts and distinct signatures. A console full of unacknowledged warnings is itself a finding.
2. **Editor + scene state.** From `mcpforunity://editor/context`: active scene, prefab-stage state, play/compile state. `manage_scene(action="get_loaded_scenes")` for the full scene set. Note `assets.external_changes_dirty` if set (uncommitted external edits may invalidate structural findings).
3. **Build & player settings.** `manage_build(action="platform")` (target) + `manage_build(action="settings", property=...)` (scripting_backend, defines, architecture, bundle_id). `mcpforunity://project/info` for Unity version + platform. Read `ProjectSettings/ProjectSettings.asset` / `GraphicsSettings.asset` on disk for colour space (flag Gamma unless documented), graphics APIs, and API compat level.
4. **Health pulse.** `manage_project(action="project_health")` — the per-category healthy flags orient every later phase. Drill into a category only when it reports unhealthy.

## Phase 1 — Repository & project structure

Read the file/folder layout first (your file tools), then evaluate:

- **Folder organisation** — is `Assets/` a coherent convention (feature / type / hybrid)? Flag scripts loose under `Assets/`, prefabs mixed with scenes, vendor code modified in place, Editor-only scripts **not** isolated under an `Editor/` folder (they ship in builds).
- **Assembly definitions** — are `.asmdef`s used at all (if not, flag the iteration-speed cost)? Do they reflect real ownership boundaries? Circular deps? Editor assemblies platform-restricted? EditMode/PlayMode test assemblies present and wired?
- **Version control hygiene** — `.gitignore` excludes `Library/`, `Temp/`, `Obj/`, `Build/`, `Logs/`, `.vs/`, generated `*.csproj`/`*.sln`? Large binaries in Git LFS or bloating the repo? Any committed secrets/API keys/env config?
- **Project settings** — scripting backend (Mono vs IL2CPP, from `manage_build settings`), colour space, graphics APIs, target architectures (from `ProjectSettings/` files); collision-matrix bloat via `manage_physics(action="get_collision_matrix")`; tag/layer sprawl via `mcpforunity://project/tags` + `/layers`. Is the Input System old, new, or both (flag dual usage)? Is `Application.targetFrameRate` ever set (on mobile, unset is almost always a finding)?

## Phase 2 — Architecture & systems design

The highest-signal phase. Go deep, reading the scripts themselves.

**MonoBehaviour patterns.** Are MonoBehaviours pure view/controllers, or God objects doubling as data stores and service locators? Sweep for the anti-patterns and cite each `file:line`:

```
# your file search over Assets/**/*.cs
FindObjectOfType|FindFirstObjectByType|FindAnyObjectByType|GameObject\.Find|SendMessage
```

`FindObjectOfType` is deprecated (Unity 2023+) for `FindFirstObjectByType`/`FindAnyObjectByType`; if both APIs appear, flag the inconsistency. Flag classes >~300 lines (SRP risk), empty Unity event methods (they still cost a native→managed call), and polling in `Update()` that should be event-driven. Cross-check source against reality with `query_scene` — code and scene diverge, especially in procedurally-built scenes.

**Dependency wiring.** Inspector refs, static singletons, service locator, DI (Zenject/VContainer), or ScriptableObject event channels? Flag singletons that hold mutable gameplay state, `DontDestroyOnLoad` duplicates not de-duped on `Awake`, and tight coupling that should be decoupled (e.g. `PlayerController` reaching into `UIManager`).

**Scene & data architecture.** Is there a bootstrapper scene? Additive vs single loading, loading-screen system? Hard cross-scene object references (break at runtime)? ScriptableObjects for shared config vs data hardcoded in prefabs? Save/load strategy (PlayerPrefs-only is a red flag beyond trivial data)?

**State & events.** Formal state machines for complex entities, or boolean-flag soup? Event mechanism (UnityEvents / C# events / SO channels / bus / none)? Sweep for `+=` subscriptions and verify each has a matching `-=` in `OnDestroy`/`OnDisable` — unbalanced subscriptions leak. Object pooling: `ObjectPool<T>`, custom, or none — flag `Instantiate`/`Destroy` in hot paths (bullets, particles, enemies).

## Phase 3 — Code quality

Read the code; cite `file:line` for every claim.

- **Conventions** — name the C# convention in use and flag violations. `SerializeField` preferred over public fields? `[Header]`/`[Tooltip]`/`[Range]` used thoughtfully on Inspector-exposed fields?
- **SOLID** — name concrete violations: God classes, concrete deps where an interface belongs, closed-for-extension designs. Be specific — file + violation.
- **C# hot-path quality** — string concatenation instead of `StringBuilder`/interpolation; LINQ (`.Where`/`.Select`/`.ToList`/`.OrderBy`) inside `Update`/`FixedUpdate`/`LateUpdate` or per-frame coroutines (allocation); uncached `GetComponent<T>()` per frame; uncached `WaitForSeconds`; `async void` outside event handlers (unhandled-exception risk); magic numbers/strings that want constants or SO config.
- **Error handling** — bare `try/catch` swallowing exceptions; missing null-checks on refs assumed valid; coroutines started on inactive GameObjects (silently no-op).

## Phase 4 — Performance risk

Identify **known** Unity performance risks from concrete patterns — do not speculate. Prefer static + audit-tool evidence; treat the profiler as an optional deep-dive.

- **Rendering / materials** — dynamic batching undermined (non-uniform scale, many materials per renderer); `Renderer.material` (allocates an instance) where `sharedMaterial` was intended. Confirm material presence per object with `query_scene(include=["materials"])`.
- **Physics** — `Physics.Raycast` without a layer mask (broadphase cost); Rigidbodies moved via `Transform.position` instead of `Rigidbody.MovePosition` (breaks interpolation); mesh colliders where a primitive suffices.
- **Memory & GC** — allocations in `Update`/`FixedUpdate` (new collections, LINQ, string ops, boxing); large textures missing compression/mips; `Resources.Load` at runtime (stalls main thread — should be Addressables); Addressables not released.
- **Audio** — `PlayOneShot` where pooled sources fit; wrong clip load type (Streaming for music, Decompress-on-Load for short SFX).

**Optional profiler evidence.** Only if the project is live and a "this is slow" finding needs numbers, and you have `review_only`+ (the session actions are VALIDATE-class). Activate the group first (`manage_tools(action="activate", group="profiling")`), then:

```python
manage_profiler(action="profiler_start")
# optionally scope areas: manage_profiler(action="profiler_set_areas", areas={"CPU": true, "Rendering": true})
# enter play mode and exercise the suspected scene
manage_profiler(action="get_counters", category="Render")   # or get_frame_timing / get_object_memory
manage_profiler(action="profiler_stop")
```

There is no `capture_frames`/`get_top_costs` action — read counters (`get_counters`, category one of Render/Scripts/Memory/Physics), frame timing (`get_frame_timing`), and object memory (`get_object_memory`); memory snapshots are `memory_take_snapshot`/`memory_compare_snapshots`. The counter reads are READ (work in `read_only` once the group is active); `profiler_start`/`profiler_stop`/`profiler_set_areas` are VALIDATE and need `review_only`+. Cite the actual counter you read. If you cannot run the profiler (mode too low, group off, no play mode), say the finding is static-analysis only — do not imply a capture happened.

**UI performance — delegate.** If the UI is non-trivial (multiple Canvases, dynamic lists, frequent updates), do **not** duplicate the UI audit here. Run `/unity-ui-auditor` (analytic `audit_ui_layout`: off-screen/overlap/touch-target/clipping, CanvasScaler, missing EventSystem/GraphicRaycaster) for the layout/interaction facts, and `/unity-ui-design` for Canvas-split, RaycastTarget hygiene, Mask vs RectMask2D, layout-rebuild cost, TMP allocation, atlasing/overdraw, and mobile safe-area/touch-target judgment. In this report, state "UI audit delegated to unity-ui-auditor / unity-ui-design" and fold their findings into the severity buckets below.

## Phase 5 — Tooling, testing & build health

- **Editor tooling** — custom Editor scripts / PropertyDrawers / EditorWindows (signs of a mature workflow); Gizmos on key components; CI/CD (GitHub Actions, Cloud Build).
- **Testing** — are there EditMode/PlayMode tests? Existence is not health. If you have `review_only`+, run them and cite the result:

  ```python
  manage_tools(action="activate", group="testing")
  run_tests_and_summarize(mode="EditMode", timeout_seconds=180, max_failures=20)
  # → all_passed, summary, failing_tests, job_id
  ```

  Report `all_passed` with the `job_id`. If `timed_out: true`, report that with the job id — never a pass/fail guess. In `read_only`, state "tests not run (read_only mode)" and stop — do not imply they pass. For deep failure triage delegate to `/unity-test-pilot`; for the binding pass/fail contract, `/unity-test-run`. Also assess testability: are systems designed to be tested (not wholly dependent on MonoBehaviour lifecycle)?
- **Build config** — `validate_build()` for a read-only pre-flight (target support, build-scene sanity, compile state, PlayerSettings red flags). Dev vs Release build profiles/defines? Debug tooling gated out of release via Scripting Define Symbols? Logging wrapped in a strippable utility vs raw `Debug.Log` everywhere? Platform-specific code correctly `#if`-gated?

## Output format

Every point names the specific file/folder/pattern/system and cites its evidence — no vague generalisations.

**Baseline (from Phase 0)** — console state (errors/warnings + distinct signatures), active scene, build target, scripting backend, Unity version, colour space. One line each. State scope (full vs focused) and any skipped phases.

**Executive summary** — 3–5 sentences: overall health, biggest risk, the single most important thing to fix first.

**Critical** — correctness bugs, data-loss/crash risk, severe architectural problems that compound. Must fix before new features. Each: **path** + **evidence cite** + **one-line fix**.

**High priority** — significant tech debt, load-bearing performance risks, patterns that hurt at scale. Same citation rule.

**Medium priority** — code quality, convention violations, missing tooling. Track and address incrementally.

**Low / nice-to-have** — quality improvements that are non-urgent.

**Strengths** — what this codebase does well. Honest and specific; do not fabricate positives.

**Recommended next actions** — an ordered list of the 5–10 highest-impact changes as concrete tasks, each referencing the system/file it targets.

Throughout: distinguish confirmed defects from **advisory** heuristics, state coverage honestly (counts, pages truncated, scans capped, categories not scanned), and never report a step the mode forbade as if it succeeded.

## Companion skills for follow-up

The review only produces findings. Route the work it uncovers:

- `/unity-project-doctor` — re-run or drill into any factual health scan this review cites.
- `/unity-plan` — turn any Critical/High finding into an implementation plan before code is written.
- `/unity-ui-auditor` and `/unity-ui-design` — any UI-touching follow-up (audit deep-dive, refactor, restyle).
- `/unity-scene-contracts` — codify a scene invariant a finding exposed.
- `/unity-edit-verify` — every C# change; compile confirmation via the editor-state compile gate + `read_console` is not optional.
- `/unity-test-run` (evidence contract) and `/unity-test-pilot` (triage) — prove a fix introduced no regressions; cite the job id.
- `/unity-debug` — when a finding is runtime misbehaviour whose cause is not yet proven; diagnose before fixing.
- `/unity-safe-iteration` — wrap risky multi-object scene changes in a checkpoint→verify→keep-or-rollback loop.
- If the project ships a rules-gate skill (project-specific compliance; `sw-rules-guard` is the template) or a procedural scene-rebuild skill (`unity-scene-rebuild`), run it before/after the relevant change.

Be direct. Be specific. Do not soften findings for comfort — an honest picture is what lets good decisions get made.
