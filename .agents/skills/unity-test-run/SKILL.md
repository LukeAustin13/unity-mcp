---
name: unity-test-run
description: Runs the Unity Test Runner (EditMode or PlayMode) via MCP and produces a verifiable, job-id-cited result. Use this skill whenever tests must be run and results must be confirmed — not assumed. A "tests pass" claim is only valid when backed by a completed Test Runner job with a cited job id.
license: MIT
---

Treat test results as evidence, not inference. A claim of "tests pass" is only valid when backed by a completed job result from the Test Runner with a cited job id. Never write "should pass" or "tests likely pass" — run them and report what actually happened.

---

## Step 1 — Determine Test Mode

Identify which test mode is appropriate for the task:

**EditMode** (default): for pure-logic C# tests that do not require scene loading or play mode. These run without entering play mode and are faster. Use EditMode for tests of damage calculators, inventory rules, state machines, save-data serialisation, and any test in an `EditMode` assembly.

**PlayMode**: for tests that require scene instantiation, MonoBehaviour lifecycle (`Awake`, `Start`, `Update`), coroutine execution, or physics. Use PlayMode only when the behaviour under test genuinely requires a running scene.

If the task does not specify a mode, default to EditMode. If both modes have relevant tests, run EditMode first, then PlayMode separately.

---

## Step 2 — Check Compile State First

Before invoking the test runner, call `read_console(types=["error"], count=20)` and confirm there are no active compile errors. A test run launched over a broken build will fail to start and give a misleading result.

If compile errors are present, stop here. Surface the errors and direct the user to fix them (or use `/unity-edit-verify` to fix them first) before running tests.

---

## Step 3 — Choose Filter Scope

Before calling `run_tests`, decide how scoped the run should be:

| Situation | Filter |
|---|---|
| Verifying a specific change touched only its own surface | Scope to the affected test class or namespace: `testFilter="Project.Combat.Tests"` |
| Verifying a cross-cutting change (e.g. renaming a widely-used type) | Full run, no filter — narrow filters can hide regressions in adjacent systems |
| Re-running after a fix to a previously failing test | Scope to that test alone first (fast feedback), then a full run to confirm no new breakage |
| Routine CI-equivalent run | Full run, no filter |

Default to the narrowest filter that genuinely answers the question. A full run that completes in 10 seconds is fine; a full run that takes 3 minutes when 4 tests would have answered the question wastes the user's time.

---

## Step 4 — Run Tests

Call `run_tests` with:
- `testMode`: `"EditMode"` or `"PlayMode"` per Step 1.
- `testFilter` (optional): a class name, namespace, or test name pattern per Step 3. Omit for a full run.

Capture the job handle returned by `run_tests`. This handle is the evidence anchor for Step 6.

---

## Step 5 — Poll for Completion (with timeout)

Call `get_test_job` with the job handle from Step 4. If the job is still running, wait and call it again. Do not assume a fixed wait time — poll until the `status` field shows `Completed`, `Failed`, or `Cancelled`.

**Polling discipline:**
- Poll every 2 seconds.
- Cap polling at **30 attempts (60 seconds)** for EditMode runs.
- Cap polling at **150 attempts (5 minutes)** for PlayMode runs (scene loads can be slow).
- If the cap is hit without completion, report it as **TIMEOUT** with the job id, then call `read_console(types=["error"], count=20)` to surface anything blocking the runner. Do not silently keep polling forever and do not claim a result.

---

## Step 6 — Parse and Report Results

Extract from the completed job result:
- Total tests run
- Passed count
- Failed count
- Skipped count
- For each failed test: test name, failure message, and stack trace (if available)

**Truncation policy.** If the failed count is > 5, show the first 5 in full detail and summarise the remainder. Long failure walls are unreadable and the first few failures are usually causally upstream of the rest.

**Format the report as:**

```
Test run complete — job <job-id>
Mode: EditMode | PlayMode
Filter: <filter or "(none — full run)">
Result: PASS / FAIL
  Total:   N
  Passed:  N
  Failed:  N
  Skipped: N

Failures (showing first 5 of N):
  1. <TestClassName.TestMethodName>
       <failure message>
       <stack trace first line>
  2. ...
  ...
  (N-5 additional failures omitted — re-run with testFilter scoped to the failing namespace for full output)
```

If all tests pass, write `Result: PASS` and include the counts. Do not omit the job id — it is the traceable record that the run actually occurred.

---

## Step 7 — On Failure

If any test fails, do not silently move on. Report the failures clearly, then either:

1. **If the failure is caused by a change made in this session** — investigate the root cause (use `/unity-debug` if the cause is not obvious from the failure message), then route the fix through `/unity-edit-verify` (which handles read → edit → validate → refresh → console check in one disciplined loop). After the fix, return to Step 2 of this skill and re-run the tests. Do not bypass `/unity-edit-verify` even for "small" fixes.
2. **If the failure is pre-existing** (present before this session's changes) — state that explicitly: "This failure was present before the current changes. Out of scope for this task." Do not claim the session's work is complete while tests are broken unless the user has acknowledged the pre-existing failure.

Never claim a task is done while tests introduced by or relevant to that task are failing.
