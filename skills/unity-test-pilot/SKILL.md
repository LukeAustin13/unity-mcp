---
name: unity-test-pilot
description: Run and triage Unity tests through MCP for Unity — one-call EditMode/PlayMode test summaries with failing-test source context, flaky-vs-deterministic classification via automatic retry, play-mode smoke tests ("does the game actually boot?"), and deduped console triage. Use when asked to "run the tests", "why is this test failing?", "is this flaky?", "smoke test the game", or to verify a change didn't break anything. Requires review_only or write safety mode (tests and play mode are VALIDATE-class); in read_only mode report that and stop.
license: MIT
---

# Unity Test Pilot

Verification through the test framework and play mode. Everything here is VALIDATE-class — allowed in `review_only` and `write`, refused in `read_only`. Check `safety_status()` first; in `read_only`, say tests cannot run in this mode and stop — never imply they passed.

## Preconditions

1. **The `testing` tool group is disabled by default.** If `run_tests_and_summarize` / `play_smoke_test` are missing, enable it first:

```python
manage_tools(action="activate", group="testing")
```

2. **Never start a run mid-compile.** Poll the `editor_state` resource's `compilation.is_compiling` until false, then check `read_console(types=["error"], count=10)` — a compile error means the tests you'd run are stale; fix or report that first.

## Running tests — one call, not start+poll

```python
run_tests_and_summarize(mode="EditMode", timeout_seconds=180, max_failures=20)
```

Starts the run, waits server-side, returns `all_passed`, `summary` (total/passed/failed/skipped), `failing_tests`, and the `job_id`. Prefer EditMode for the fast loop; PlayMode costs a domain reload — run it when the change touches runtime behaviour, or when asked.

If `timed_out: true`, the run is still going — report *that* with the `job_id` (pollable via `get_test_job`), never a pass/fail guess.

### Triage knobs — use them instead of hand-digging

```python
run_tests_and_summarize(
    mode="EditMode",
    include_source_context=True,   # ±5 source lines around each failing frame (local runs)
    retry_failed=2,                # re-run ONLY the failures, up to 3 retries
)
```

`retry_failed` re-runs just the failed tests and classifies each as **flaky** (passed on retry) or **deterministic** (kept failing). Lead your report with that split — a deterministic failure is a bug to fix; a flaky one is a test to stabilize, and "re-run everything until green" is not a fix.

## Play-mode smoke test — what run_tests can't tell you

Tests passing does not mean the game boots. `play_smoke_test` enters play mode, runs N seconds, collects errors/exceptions raised while playing, exits, and gives a verdict — surviving the domain reload that play mode triggers:

```python
play_smoke_test(action="run", seconds=15)   # max 120
# → {completed, error_count, exception_count, warning_count, sample_errors, verdict, reason}
```

`verdict: "fail"` means errors/exceptions during play **or** the game failed to boot at all (e.g. a compile error blocked play mode) — read `reason` before blaming the scene. Use the smoke test after scene surgery, before handoff, and as the final gate of a safe-iteration loop.

## Console triage without the flood

```python
read_console(action="get", types=["error"], format="summary")
# → grouped signatures: count + one sample + file:line per distinct error
```

500 identical NullReference spam lines are one bug. Triage by distinct signature; only pull `format="detailed"` (with `include_stacktrace=True`) for the one signature you're actually chasing. Do not `read_console(action="clear")` — destroying evidence is a WRITE and never required for triage.

## Reporting

```text
Tests: 214/216 passed (EditMode, job 4f2a…)
Failed deterministic (1): PlayerSpawnTests.SpawnsAtCheckpoint — NullReference at SpawnManager.cs:41
Failed flaky (1): SaveLoadTests.RoundTrip — passed on retry 1; likely time/order dependent
Smoke: PASS — 15s in play mode, 0 errors, 0 exceptions (3 warnings)
```

- Always cite the `job_id` behind a pass/fail claim.
- Distinguish "failed", "flaky", "timed out", and "skipped (read_only mode)" — they are four different facts.
- A smoke-test fail with `error_count: 0` is a boot failure; report the `reason`, not "the game has errors".
