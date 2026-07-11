---
name: unity-debug
description: Hypothesis-driven diagnosis of Unity runtime problems through MCP for Unity — exceptions, wrong behaviour, broken interactions, physics oddities, missing/destroyed references, or anything that compiles but misbehaves. Use when something is wrong at runtime and the cause is unknown; it kills hypotheses with tool evidence before recommending a fix, never shotgunning speculative changes. Read-only evidence works in read_only mode; play-mode reproduction and smoke tests need review_only (VALIDATE class); applying the fix needs write mode and routes through unity-edit-verify.
license: MIT
---

# Unity Debug

Debug by forming hypotheses and killing them with evidence — never by shotgunning speculative fixes. A diagnosis is only valid when you can point at the tool output that proves it: a console stack trace, a `query_scene` row showing the wrong serialized state, a `visibility_report` showing zero on-screen coverage. **"Probably" is not a diagnosis.**

This skill is the runtime-bug lane. It builds on `unity-mcp-operator` (orientation, safety posture, cheapest-evidence discipline) — don't re-derive that here. Compile errors are *not* debugging: route them straight to `unity-edit-verify`. Use this skill only when the project **compiles but misbehaves**.

**Do not edit any script until Step 5.** Edits made while the cause is unknown destroy the evidence and widen the search space. Never "fix" anything by editing scene state during Play Mode — those changes are discarded when Play Mode exits, and a script edit mid-play triggers a domain reload that invalidates the reproduction. Exit Play Mode before editing.

## Posture first

Diagnosis is mostly READ/VALIDATE; the fix is WRITE. Check the posture before you start so you plan a reachable endpoint:

```python
safety_status()   # or read mcpforunity://server/safety
```

- **`read_only`** — pure-read evidence only (console, `query_scene`, `find_gameobjects`, `unity_reflect`, `find_in_file`, `visibility_report`, profiler reads). You can often diagnose fully here, but you **cannot reproduce in Play Mode** and cannot apply the fix. End with a diagnosis + suggested fix, and say the fix was not applied.
- **`review_only`** — adds VALIDATE: Play-Mode reproduction, `play_smoke_test`, screenshots. This is the minimum mode to *reproduce* a runtime bug live.
- **`write`** — required to apply the fix. `confirm:true` is destructive-op acknowledgement metadata, never human approval and never a mode override — a script edit doesn't need it, a delete does.

Non-core tool groups start disabled. Three tools this skill leans on live in disabled groups — activate them when needed:

```python
manage_tools(action="list_groups")
manage_tools(action="activate", group="testing")     # play_smoke_test
manage_tools(action="activate", group="docs")         # unity_reflect
manage_tools(action="activate", group="profiling")    # manage_profiler
```

A **missing** tool means its group is deactivated; a **refused** tool means the safety mode forbids its class. They are different failures — don't shop for a workaround that dodges the gate.

---

## Step 1 — Capture the failure precisely

Before touching anything, pin down what "wrong" means:

1. **Symptom statement.** One sentence: what happens, what was expected instead, and when (always / sometimes / after a specific action).
2. **Orient in one read.** Read `mcpforunity://editor/context` — it unions editor state, selection, prefab stage, and console error/warning counts. For readiness, read the `editor_state` resource: if its `advice.ready_for_tools` is false, report `advice.blocking_reasons` rather than trusting the data. If it is compiling or a domain reload is pending, poll `editor_state`'s `compilation.is_compiling` until false before reading further — stale reads mislead a diagnosis.
3. **Console capture — summary before detail.** When errors flood, start with the deduped view:
   ```python
   read_console(action="get", types=["error"], format="summary")
   # → ranked signature groups: count + one sample + first file:line per distinct error
   ```
   500 identical `NullReferenceException` lines are one bug. Pick the signature you're chasing, then pull its stack:
   ```python
   read_console(action="get", types=["error"], format="detailed", include_stacktrace=True, count=20)
   ```
   Read **every frame** of the trace, not just the top line. Note the exact exception type — `NullReferenceException`, `MissingReferenceException` (a *destroyed* Unity object — a different cause class than plain null), `IndexOutOfRangeException` — the type narrows the search before you read a single field. **Never `read_console(action="clear")`** — it is a WRITE that destroys the evidence you're triaging.
4. **Where it fires.** Note whether the symptom is Play Mode, Edit Mode, or a build. A symptom that only appears in builds points at stripping, `#if UNITY_EDITOR` code, or serialization differences — flag it early, it changes the whole investigation.
5. **Recent-change check.** If code changed this session or last commit, list the changed files. The most recent change is the prime suspect until evidence clears it.

If there is no console output at all, the bug is behavioural — proceed, but expect the evidence to come from scene inspection (Step 3), not stack traces.

---

## Step 2 — Reproduce before theorising

A bug you cannot reproduce cannot be verified as fixed. Reproduction is VALIDATE-class — it needs `review_only` or `write`; in `read_only`, say so and diagnose from static reads instead.

1. **Boot-check first.** Before hand-driving play, ask whether the bug fires just from running:
   ```python
   play_smoke_test(action="run", seconds=15)   # testing group; max 120
   # → {completed, error_count, exception_count, warning_count, sample_errors, verdict, reason}
   ```
   `play_smoke_test` enters Play Mode, collects errors/exceptions raised while playing, exits, and survives the domain reload play triggers. A `verdict:"fail"` with `error_count:0` is a **boot failure** — read `reason`, don't blame the scene.
2. **Minimal reproduction.** If the bug needs interaction, find the shortest action sequence that triggers it. Drive Play Mode with `manage_editor(action="play")` / `pause` / `stop` and observe via `read_console`. Enter/exit are the exact action names — no discovery call needed.
3. **Intermittence.** If it's flaky, run the reproduction several times and record the trigger rate. Intermittence usually points at frame-order dependence (`Update` vs `FixedUpdate` vs `LateUpdate`), uninitialised script execution order, physics timing, or coroutine/async races. A test that flakes this way is `unity-test-pilot`'s lane (retry-based flaky-vs-deterministic classification) — hand off rather than re-implementing it here.
4. **Can't reproduce?** Stop and say so. Report what you tried, what would help (exact steps, a seed, a save state), and do not guess at fixes.

---

## Step 3 — Gather evidence from the live project

Inspect what the editor *actually holds*, not what the source files imply. Use the cheapest tool that answers the question:

| Question | Tool | Class |
|---|---|---|
| What's the error signature / which log fired before the failure? | `read_console` (`format="summary"`, then `detailed` for one signature) | READ |
| Is the object present, active, duplicated? | `find_gameobjects` — returns **instance IDs only**, paged | READ |
| What are the object's *actual* serialized values and components? | `query_scene` (filter + projection, one call) | READ |
| Does the type / method / field the code calls really exist with that signature? | `unity_reflect` (docs group) | READ |
| Where in a *specific* suspect script is X written / subscribed? | `find_in_file` (one file per call, by URI) | READ |
| Which scene is loaded, is it dirty, does it match what the code assumes? | `mcpforunity://editor/context`, then `manage_scene(action="get_active")` | READ |
| Is the object actually on-screen (visual "nothing renders" bug)? | `manage_camera(action="visibility_report")` | READ |
| Real frame/counter/memory numbers for a perf symptom | `manage_profiler` (profiling group) | READ |

**State inspection is `query_scene`, not `find_gameobjects`.** `find_gameobjects` returns IDs only — it answers *existence / count / duplication*. For the serialized state behind a suspect object, use `query_scene` (one call replaces the old find → get-components → get-properties loop):

```python
# "What is actually on every EnemyController, and how big is it?"
query_scene(component_type="EnemyController",
            include=["components", "transform", "world_bounds"], page_size=50)
```

Projections: `transform`, `world_bounds`, `components`, `materials`, `mesh_stats`. Ask only for what the hypothesis needs. For a single known object, `mcpforunity://scene/gameobject/{id}/components` gives full component detail.

**`unity_reflect` verifies the compiled API surface — not runtime values.** Use it to confirm a class/method/field the code calls actually exists with that signature (training data is often stale); it does **not** report the live value of a field. `action="get_type"` (member summary), `get_member` (full signature), `search` (type-name search).

**`find_in_file` searches one file.** Give it the suspect script's URI and a regex; it returns matching lines in that file. It is not a project-wide grep.

```python
find_in_file(uri="Assets/Scripts/PlayerController.cs", pattern="\\+=")   # subscriptions in THIS file
```

Classic Unity evidence patterns to check when relevant:

- **Null vs destroyed reference.** Is the field unassigned in the Inspector (`query_scene` shows the component's serialized state), destroyed at runtime (`MissingReferenceException`), or never initialised because `Awake` order was wrong?
- **Event/callback bugs.** `find_in_file` the suspect script for `+=` / `-=` — a subscriber destroyed without unsubscribing, or a double subscription.
- **Lifecycle ordering.** Does script A read in `Awake` what script B only sets in `Start`? Check Script Execution Order assumptions.
- **Visual "it's gone" bugs.** `manage_camera(action="visibility_report", camera="MainCamera")` returns per-object `in_frustum`, `screen_rect`, `coverage_pct`, `distance` — zero-pixel facts. Confirm the object is off-screen / behind the camera / zero-scale *before* spending a screenshot.
- **Scene/code divergence.** A generated or hand-wired scene object may predate the current code. Compare its actual components (`query_scene`) against what the current source expects.

---

## Step 4 — Hypothesise, predict, test

1. **Falsifiable hypothesis.** "X is null when Y runs because Z runs after Y." Not "something's wrong with X."
2. **Prediction.** What a specific tool will show if the hypothesis holds: "`query_scene` will show the field unassigned" or "a log placed before the call will show the wrong value."
3. **Test with the cheapest read that can falsify it.** Adding a temporary `Debug.Log` (via the normal edit loop) is acceptable *only* when no read-only check can answer it — mark it clearly and remove it in Step 5.
4. **Kill or keep.** If the prediction fails, the hypothesis is dead — record it as ruled out (so you don't re-test it) and form the next one. Carry **at most one** live hypothesis into a fix.

---

## Step 5 — Diagnose, then fix through the loop

Only when one hypothesis has survived its test:

1. **State the diagnosis** as an evidence chain: symptom → evidence → cause. If you can't write that chain, return to Step 4.
2. **Fix the root cause, not the symptom.** A null check that hides the failure is not a fix unless null is genuinely a valid state.
3. **Route every code change through `unity-edit-verify`** — read, hash-guarded edit, validate, then wait for compilation and check the console. No exceptions for "one-line fixes." Do **not** call `refresh_unity` yourself after a script edit — the script tools already trigger import + compilation; wait for `editor_state.compilation.is_compiling` to go false, then `read_console(types=["error"])`. If the fix touches risky multi-object scene state, snapshot first with `unity-safe-iteration`.
4. **Re-run the Step 2 reproduction** and confirm the symptom is gone, then check for collateral damage: hand a regression run to `unity-test-pilot` (or `unity-test-run`) and cite the returned `job_id`.
5. **Remove any temporary debug logging** added in Step 4.

---

## Step 6 — Report what you actually observed

- **Symptom** — the one-sentence failure statement.
- **Diagnosis** — root cause with its evidence chain; cite the console signature, `query_scene` row, `visibility_report`, or search hit that proves it.
- **Hypotheses ruled out** — one line each, with the evidence that killed them.
- **Fix** — files changed, routed through `unity-edit-verify` (cite its console result), or "not applied — `read_only` mode" if the posture blocked it.
- **Verification** — reproduction re-run result (cite the `play_smoke_test` verdict or Play-Mode observation) plus the test `job_id` if tests were run.
- **Follow-ups** — anything observed but out of scope (pre-existing warnings, adjacent fragility): listed, not silently fixed.

Never report a bug as fixed on the strength of "the code looks right now." **Fixed means the reproduction no longer reproduces, verified in this session** with a cited read. Distinguish "diagnosed but not applied (read_only)", "fixed and verified", and "could not reproduce" — they are three different outcomes.
