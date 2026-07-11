---
name: unity-test-run
description: The binding verification contract for Unity tests run through MCP for Unity — a "tests pass" claim is valid ONLY when backed by a completed Test Runner job with a cited job_id, never inferred. Use whenever a task's completion depends on tests actually passing and the result must be proven, not assumed ("run the tests and confirm", "did that break anything?", "verify before I merge"). Running tests is VALIDATE-class, so this needs review_only or write safety mode; in read_only, report that tests cannot run and stop — never imply a pass. For failure triage (flaky classification, source context, smoke tests) use unity-test-pilot; this skill owns the pass/fail evidence contract.
license: MIT
---

# Unity Test Run

The verification contract: **a claim of "tests pass" is only valid when backed by a completed Test Runner job with a cited `job_id`.** Treat results as evidence, not inference. Never write "should pass", "tests likely pass", or "this looks correct" — run them and report what the job actually returned. If you cannot run them (mode, compile state, timeout), say exactly that and stop; a missing result is not a pass.

## Lane

- **This skill** is the minimal binding contract for any claim that tests were run: activate → run → cite the completed job. Invoke it whenever "done" depends on a green run.
- **`unity-test-pilot`** owns triage *workflows* — flaky-vs-deterministic classification, failing-test source context, play-mode smoke tests, deduped console triage. When a run comes back red and the question shifts to *why*, hand off there. Both use the same tools; the split is contract vs investigation.

## Preconditions — three gates, in order

**1. Safety posture.** Running tests is VALIDATE-class, enforced server-side.

```python
safety_status()   # or read mcpforunity://server/safety
```

| Mode | Can run tests? |
|---|---|
| `read_only` | No — VALIDATE is refused. Report "tests cannot run in read_only mode; no run was performed" and stop. Never imply a pass. |
| `review_only` | Yes (minimum required). |
| `write` | Yes. |

`get_test_job` alone is READ-class, so in `read_only` you may still *poll* a job someone already started — but you cannot start one. Do not present a job you didn't complete as a pass.

**2. Testing tool group.** The `testing` group is **disabled by default**. If `run_tests_and_summarize` / `run_tests` / `get_test_job` are missing, they are hidden, not absent:

```python
manage_tools(action="list_groups")                    # confirm status
manage_tools(action="activate", group="testing")      # enable for this session
```

**3. Compile-clean.** A run launched over a broken build gives a misleading result. Never start mid-compile: poll the `editor_state` resource's `compilation.is_compiling` until false, then:

```python
read_console(action="get", types=["error"], count=20)
```

If compile errors are present, **stop** — surface them and route the fix through `/unity-edit-verify` before running. The tests you'd run are stale until the build is clean.

## Primary path — one call

`run_tests_and_summarize` starts the run, waits server-side up to `timeout_seconds`, and returns the whole verdict in one call — no manual start+poll loop.

```python
run_tests_and_summarize(mode="EditMode", timeout_seconds=180, max_failures=20)
```

Returns: `job_id`, `status`, `completed`, `all_passed`, `summary` (`total`/`passed`/`failed`/`skipped`/`duration_seconds`/`result_state`), and `failing_tests` (capped at `max_failures`, with `failing_tests_truncated`).

Read the result like this:

| Field state | Meaning | Report |
|---|---|---|
| `completed: true`, `all_passed: true` | Job finished, `failed == 0` | **PASS** — cite `job_id` and counts |
| `completed: true`, `all_passed: false` | Job finished, some tests failed | **FAIL** — cite `job_id`, list `failing_tests` |
| `timed_out: true` | Run did not finish within `timeout_seconds` | **STILL RUNNING** — report the `job_id` and that it is unfinished; poll `get_test_job(job_id)` for the final result. **Never guess PASS/FAIL from a timeout.** |

A timed-out run is not a failed run and not a passed run — it is an unfinished run. The only honest report is "still running, job `<id>`, not yet confirmed".

## Choosing mode and filter scope

**Mode** — `mode="EditMode"` (default) for pure-logic tests (damage calculators, inventory rules, state machines, serialisation). `mode="PlayMode"` only when the behaviour genuinely needs a running scene (MonoBehaviour lifecycle, coroutines, physics). PlayMode costs a domain reload — for it, pass a longer `timeout_seconds` (e.g. 300) and, on the advanced path, `init_timeout=120000`. If both have relevant tests, run EditMode first, then PlayMode separately.

**Filter** — the run is scoped by parameters (there is **no** `testFilter` argument):

| Parameter | Scopes to |
|---|---|
| `test_names=["Ns.Class.Method"]` | Exact full test names |
| `group_names=["Project.Combat.*"]` | Regex-capable name filter (class/namespace prefixes) |
| `category_names=["Fast"]` | NUnit `[Category]` names |
| `assembly_names=["Project.Combat.Tests"]` | Whole test assemblies |

| Situation | Scope |
|---|---|
| Verifying a change touched only its own surface | Narrow — `group_names` or `assembly_names` for the affected area |
| Cross-cutting change (renaming a widely-used type) | Full run, no filter — narrow filters hide regressions in adjacent systems |
| Re-checking one previously failing test | `test_names=["that.one.Test"]` first for fast feedback, then a full run to confirm no new breakage |
| Routine CI-equivalent run | Full run, no filter |

Default to the narrowest filter that genuinely answers the question, but never let a narrow filter stand in for a cross-cutting claim.

## Advanced path — fine control

When you need to start a run and do other work while it runs, or want progress polling, use the two-call form. Verify params against `Server/src/services/tools/run_tests.py`.

```python
# Start — returns immediately with a job_id
run_tests(mode="PlayMode",
          assembly_names=["Project.Combat.Tests"],
          include_failed_tests=True,     # failed/skipped detail in the result
          init_timeout=120000)           # PlayMode domain-reload headroom

# Poll — wait_timeout makes the SERVER wait; do not hand-roll a sleep loop
get_test_job(job_id, include_failed_tests=True, wait_timeout=60)
```

`get_test_job` `status` values are **`running`, `succeeded`, `failed`, `cancelled`** (lowercase). The job is complete when status is `succeeded`, `failed`, or `cancelled`. `wait_timeout` (seconds) blocks server-side until completion or timeout, so one call with `wait_timeout=60` replaces thirty 2-second polls. If it returns still `running`, call again — do not conclude a result. Cap total wait at your judgement of the suite size and report a **TIMEOUT** with the `job_id` (then `read_console(types=["error"], count=20)` to surface anything blocking the runner) rather than polling forever or claiming a result.

Note: a `status: "succeeded"` job can still contain failing tests — completion is not the same as passing. Derive PASS/FAIL from the summary's `failed` count (`failed == 0` → PASS), never from the job status alone.

## Reporting — the traceable record

```
Test run — job <job_id>
Mode:    EditMode | PlayMode
Filter:  <params used, or "(none — full run)">
Result:  PASS | FAIL | STILL RUNNING | TIMEOUT | SKIPPED (read_only)
  Total:   N
  Passed:  N
  Failed:  N
  Skipped: N

Failures (first 5 of N):
  1. Ns.Class.Method — <message first line>
       <stack trace first frame>
  ...
  (N-5 more omitted — re-run scoped to the failing namespace for full output)
```

- **Always cite the `job_id`.** It is the evidence anchor; a report without one is an inference, not a result.
- If `failed > 5`, show the first 5 in full and summarise the rest (the first failures are usually causally upstream).
- Distinguish these five facts — they are not interchangeable: **PASS** (completed, 0 failed), **FAIL** (completed, ≥1 failed), **STILL RUNNING / TIMEOUT** (no completed job — job_id only), **SKIPPED** (`read_only` mode; no run performed). Never collapse a timeout or a skip into a pass.
- Report only counts you read from the job. Do not add "and everything else is fine" beyond what the summary states.

## On failure

Do not silently move on when a test fails.

1. **Caused by this session's change** — investigate the root cause (use `/unity-debug` if it is not obvious from the message; use `unity-test-pilot` with `retry_failed`/`include_source_context` to split flaky from deterministic and locate the failing frame), route the fix through `/unity-edit-verify` (read → hash-guarded edit → validate → compile-wait → console check), then return to the **Preconditions** gates and re-run. Do not bypass `/unity-edit-verify` even for a "small" fix.
2. **Pre-existing** (present before this session's changes) — state it explicitly: "This failure predates the current changes — out of scope for this task." Do not claim the session's work is complete while relevant tests are red unless the user has acknowledged the pre-existing failure.

Never report a task as done while tests introduced by or relevant to it are failing, unfinished, or unrun.
