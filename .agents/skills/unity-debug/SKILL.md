---
name: unity-debug
description: Hypothesis-driven debugging of Unity runtime problems — exceptions, wrong behaviour, broken interactions, physics oddities, or anything that compiles but misbehaves. Use this skill when something is wrong at runtime and the cause is not yet known. It diagnoses with evidence before recommending a fix; the fix itself is routed through unity-edit-verify.
license: MIT
---

Debug by forming hypotheses and killing them with evidence — never by shotgunning speculative fixes. A diagnosis is only valid when you can point at the tool output that proves it: a console stack trace, a `find_gameobjects` result showing the wrong component state, a `unity_reflect` output showing the value that should not be there. "Probably" is not a diagnosis.

Compile errors are not debugging — route them straight to `/unity-edit-verify`. Use this skill when the project compiles but misbehaves.

Do not edit any script until Step 5. Edits made while the cause is unknown destroy the evidence and widen the search space. And never "fix" anything by editing scene state during Play Mode — those changes are discarded when Play Mode exits, and a script edit mid-play triggers a domain reload that invalidates the reproduction. Exit Play Mode before editing.

---

## Step 1 — Capture the Failure Precisely

Before touching anything, pin down what "wrong" means:

1. **Symptom statement.** One sentence: what happens, what was expected instead, and when (always / sometimes / after a specific action).
2. **Console capture.** Call `read_console(types=["error","warning","log"], count=50)`. If there is a stack trace, it is your primary evidence — read every frame of it, not just the top line. Note the exact exception type: `NullReferenceException`, `MissingReferenceException` (a destroyed Unity object — different cause class than null), `IndexOutOfRangeException`, etc.
3. **Editor state.** Confirm the editor is not compiling and note whether the symptom occurs in Play Mode, Edit Mode, or a build. A symptom that only appears in builds points at stripping, `#if UNITY_EDITOR` code, or serialization differences — note this early because it changes the whole investigation.
4. **Recent-change check.** If code changed recently (this session or last commit), list the changed files. The most recent change is the prime suspect until evidence clears it.

If there is no console output at all, the bug is behavioural — proceed, but expect the evidence to come from scene inspection (Step 3) rather than stack traces.

---

## Step 2 — Reproduce Before Theorising

A bug you cannot reproduce cannot be verified as fixed.

1. Find the minimal reproduction: the shortest sequence of actions that triggers the symptom. Drive Play Mode via `manage_editor` (enter play / pause / stop — verify exact action names with `tool_search` if unsure) and observe via `read_console`.
2. If the symptom is intermittent, run the reproduction several times and record the trigger rate. Intermittence usually points at: frame-order dependence (`Update` vs `FixedUpdate` vs `LateUpdate`), uninitialised execution order between scripts, physics timing, or coroutine/async races.
3. If you cannot reproduce it, stop and say so. Report what you tried, what extra information would help (exact steps, a seed, a save state), and do not guess at fixes.

---

## Step 3 — Gather Evidence From the Live Project

Use the MCP read tools to inspect actual state — not what the source files imply, what the editor actually holds:

| Tool | What it answers |
|---|---|
| `read_console` | Stack traces, error sequence and timing, which log line fired before the failure |
| `find_gameobjects` | Is the object actually in the scene? Active? Holding the expected components with the expected serialized values? Duplicated? |
| `unity_reflect` | What is actually compiled — does the type/field/method the code expects really exist with that signature? |
| `find_in_file` | Every call site of the suspect method, every writer of the suspect field, every subscriber to the suspect event |
| `manage_scene` | Which scene is loaded, is it dirty, does it match the scene the code assumes |
| `manage_profiler` | For performance-flavoured symptoms (hitches, frame drops, memory growth) — capture frames and get real numbers before blaming anything |

Classic Unity evidence patterns to check when relevant:

- **Null/missing reference**: is the field unassigned in the Inspector (`find_gameobjects` shows the component's serialized state), or destroyed at runtime (`MissingReferenceException`), or never initialised because `Awake` order was wrong?
- **Event/callback bugs**: `find_in_file(pattern="\\+= ")` on the suspect event — is there a subscriber that was destroyed without unsubscribing, or a double subscription?
- **Lifecycle ordering**: does script A read in `Awake` what script B only sets in `Start`? Check Script Execution Order assumptions.
- **Scene/code divergence**: if the scene is generated or hand-wired, the object in the scene may predate the current code. Compare actual components against what the current source expects.

---

## Step 4 — Hypothesise, Predict, Test

1. State the most likely cause as a falsifiable hypothesis: "X is null at the time Y runs because Z runs after Y."
2. State the prediction it makes: "If true, `find_gameobjects` will show the field unassigned" or "a log line placed before the call will show the wrong value."
3. Test the prediction with the cheapest read-only check available. Adding a temporary `Debug.Log` (via the normal edit loop) is acceptable when no read-only check can answer it — mark it clearly and remove it before finishing.
4. If the prediction fails, the hypothesis is dead. Record it as ruled out (so you do not re-test it), form the next one, repeat. Do not carry more than one live hypothesis into a fix.

---

## Step 5 — Diagnose, Then Fix Through the Loop

Only when one hypothesis has survived its test:

1. **State the diagnosis** with its evidence chain: symptom → evidence → cause. If you cannot write that chain, return to Step 4.
2. **Fix the root cause, not the symptom.** A null check that hides the failure is not a fix unless null is genuinely a valid state.
3. Route every code change through `/unity-edit-verify` — read, hash-guarded edit, validate, refresh, console check. No exceptions for "one-line fixes".
4. Re-run the reproduction from Step 2 and confirm the symptom is gone. Then check the fix did not break adjacent behaviour: run relevant tests via `/unity-test-run` and cite the job id.
5. Remove any temporary debug logging added during Step 4.

---

## Step 6 — Report

- **Symptom**: the one-sentence failure statement.
- **Diagnosis**: root cause with the evidence chain (cite the console excerpt, search hit, or inspection result).
- **Hypotheses ruled out**: one line each, with the evidence that killed them.
- **Fix**: files changed, routed through `unity-edit-verify` (cite its console result).
- **Verification**: reproduction re-run result, plus test job id if tests were run.
- **Follow-ups**: anything observed but out of scope (pre-existing warnings, adjacent fragility) — listed, not silently fixed.

Never report a bug as fixed on the strength of "the code looks right now". Fixed means the reproduction no longer reproduces, verified in this session.
