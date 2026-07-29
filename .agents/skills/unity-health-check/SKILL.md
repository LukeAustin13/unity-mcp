---
name: unity-health-check
description: Fast, read-only pulse check of a Unity project through MCP for Unity — reads the server safety posture and editor context, scans whole-project asset/material health, reads the console for errors, and (when the safety mode allows) runs a quick test summary, then reports a concise health summary. Use when asked "is the project healthy?", "quick project pulse", "any errors?", or before starting work in an unfamiliar Unity project. Read-only: it never mutates the project and works in any safety mode.
license: MIT
---

# Unity Health Check

A fast, **read-only** pulse of a Unity project. Lighter than a full `/unity-review` audit — this is the "is anything on fire right now?" check you run before starting work, or when someone asks whether the project is healthy.

Every step in this skill is read-only. It works in **any** safety mode (`read_only`, `review_only`, `write`) and never mutates the project. The only step that is not a pure READ is the optional test summary, which is a VALIDATE action and is skipped in `read_only` mode.

## When to use

- "Is the project healthy?" / "quick project pulse" / "any errors?"
- Before starting work in an unfamiliar or possibly-broken project.
- After pulling changes, to confirm the project still compiles and has no new asset breakage.

For a deep senior-level audit (architecture, code quality, performance), use `/unity-review` instead. This skill is deliberately shallow and fast.

## Step 0 — Read the safety posture

Know what you're allowed to do before you do it. Read the posture up front so you can decide whether the optional test step will run.

```
safety_status()
```

or read the equivalent resource:

```
mcpforunity://server/safety
```

From the result, note:
- `mode` — one of `read_only`, `review_only`, `write`.
- `allowed_classes` — includes `read` in every mode; includes `validate` in `review_only` and `write`.

Decision: if `mode == "read_only"`, **skip Step 4** (the test summary is a VALIDATE action and will be refused). Every other step runs in all modes.

Do not attempt any write. This skill has no fallback that mutates the project — a health check that changed the project would not be a health check.

## Step 1 — Read the editor context

Confirm a Unity instance is connected and the editor is in a state where reads return meaningful data. `editor/context` is a **resource**, not a tool — read it, do not call it:

```
mcpforunity://editor/context
```

This single read unions the editor state, current selection, prefab-stage status, and console error/warning counts. If `mcpforunity://editor/context` is not available in this server build, fall back to the canonical readiness snapshot:

```
mcpforunity://editor/state
```

From whichever you read, capture:
- Unity version, platform, active scene.
- Play-mode state (`is_playing`) — note it; a project in play mode is a valid but worth-flagging state.
- Compilation state — `is_compiling` / `is_domain_reload_pending`. If the editor is mid-compile or mid-domain-reload, note it and prefer to let it settle before trusting the console read in Step 3 (a console read during compile may miss errors that appear when compilation finishes).
- `ready_for_tools` / `blocking_reasons` (from `editor/state`) — if not ready, report the blocking reason rather than proceeding as if the numbers are trustworthy.

If no Unity instance is connected, stop and report that — there is nothing to health-check.

## Step 2 — Whole-project asset & material health

Run the aggregate project-health scan. This is a **read-only** whole-project scan (it inspects assets on disk; it never modifies them).

```
manage_project(action="project_health")
```

`project_health` returns one aggregate report with a per-category `healthy` flag covering asset validity and material validity. Read the per-category flags.

If `project_health` reports a category as unhealthy and you want the specifics, drill in with the focused scans (all read-only):

```
manage_project(action="validate_assets")     # missing scripts, broken prefab instances, dangling references
manage_project(action="validate_materials")  # missing shader / error ('pink') shader / unsupported shader
```

Large scans are paged — follow `next_cursor` if you need the full list, but for a *pulse* check the aggregate flags plus the first page of issues is usually enough. Report counts, not every individual issue.

Optional, if the user wants a size/shape sense of the project:

```
manage_project(action="asset_inventory")   # counts rolled up by asset type
```

## Step 3 — Console errors

Read the console for real compile/runtime errors. This is read-only (only `read_console(action="clear")` would be a write — do not use it).

```
read_console(action="get", types=["error"], count=20, format="detailed")
```

Optionally widen to warnings for a fuller picture:

```
read_console(action="get", types=["error", "warning"], count=30, format="detailed")
```

Report the error count and the first few distinct error messages. If Step 1 showed the editor was still compiling, say so — the console may not yet reflect the final state.

## Step 4 — Test summary (only if the mode allows)

Skip this step entirely if `mode == "read_only"` (from Step 0). In `review_only` or `write`, run the one-call test summary. This is a **VALIDATE** action (transient editor state, no persistent mutation), which is why `review_only` permits it.

```
run_tests_and_summarize(mode="EditMode", timeout_seconds=180, max_failures=20)
```

This starts the run, waits server-side, and returns pass/fail counts plus a capped list of failing tests in one call — no manual start + poll loop. Read:
- `all_passed` — the headline.
- `summary` — `total` / `passed` / `failed` / `skipped`.
- `failing_tests` — the capped list of failures with messages.
- `timed_out` — if `true`, the run did not finish inside `timeout_seconds`; report that it is still running rather than claiming pass or fail, and cite the `job_id` so it can be polled with `get_test_job`.

Keep it to EditMode for a fast pulse. PlayMode is slower (domain reload) — only run it if specifically asked.

## Step 5 — Report a concise health summary

Produce a short, scannable summary. Lead with an overall verdict, then the evidence.

Suggested shape:

```
Unity Health Check — <project / scene name>

Overall: HEALTHY | ISSUES FOUND | BLOCKED

Safety mode:   <read_only | review_only | write>
Editor:        Unity <version>, <platform>, scene "<name>", play mode: <on/off>, compiling: <yes/no>
Assets:        <healthy | N issues>  (missing scripts: N, broken prefabs: N, dangling refs: N)
Materials:     <healthy | N issues>  (missing/error shaders: N)
Console:       <no errors | N errors>  — <first distinct message, if any>
Tests:         <all passed (P/T) | N failed | skipped (read_only mode) | timed out, job <id>>
```

Rules for an honest report:
- **Overall = BLOCKED** if no Unity instance is connected, or `ready_for_tools` was false and you could not get trustworthy reads.
- **Overall = ISSUES FOUND** if there are console errors, unhealthy asset/material categories, or failing tests.
- **Overall = HEALTHY** only when the console is clean, project-health categories are all `healthy`, and (where it ran) tests passed.
- Never write "tests passed" unless Step 4 actually ran and returned `all_passed: true` with a cited `job_id`. If tests were skipped because of `read_only` mode, say "skipped (read_only mode)" — do not imply they passed.
- Cite the numbers you actually observed. Do not summarize a category you did not read.
