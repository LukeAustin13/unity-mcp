---
name: unity-safe-iteration
description: The checkpoint → change → verify → keep-or-rollback loop for risky Unity scene edits through MCP for Unity — snapshot loaded scenes with manage_checkpoint, make the change, verify with console/tests/smoke-test evidence, then either keep the result or restore the snapshot. Use when asked to "try something risky", make large multi-object scene changes, reorganize a hierarchy, experiment with physics/layout, or whenever a mistake would be expensive to undo by hand. Requires write mode (restore is destructive and needs confirm:true acknowledgement).
license: MIT
---

# Unity Safe Iteration

Risky scene work should be a loop with an exit, not a leap: **snapshot → change → verify → keep or roll back.** The snapshot is `manage_checkpoint`; the verification is evidence, not optimism.

Checkpoint actions have different safety classes — know which mode you need:

| Action | Class | Needs |
|---|---|---|
| `list` | READ | any mode |
| `create` | VALIDATE (writes only under `Library/`, no project mutation) | `review_only`+ |
| `delete` | WRITE | `write` |
| `restore` | **DESTRUCTIVE** — overwrites scene files on disk, discards unsaved changes | `write` + `confirm:true` |

The full loop therefore needs `write` mode. Check `safety_status()` first and say so if the mode is lower.

## 1. Snapshot before the risk

```python
manage_checkpoint(action="create", label="before-hierarchy-refactor")
# label: ^[A-Za-z0-9_-]{1,64}$ — server generates an id if omitted
```

Know the limits before you rely on it:

- Only **loaded scenes with a saved path** are captured. Untitled/never-saved scenes come back in `skipped_untitled` — if the scene you're about to mutate is in that list, save it first or you have **no rollback**. Check the create result, not your assumption.
- Capped at 20 checkpoints; snapshots live in `Library/McpCheckpoints/` (not version control, not synced, wiped with `Library`). This is an experiment-scale seatbelt, **not a substitute for git** — anything worth keeping across sessions belongs in a commit.
- Checkpoint what the change touches: assets, project settings, and scripts are **not** captured — only scene state. If the experiment edits scripts too, rely on git for those.

When to checkpoint: multi-object scene surgery, hierarchy reorganization, physics/layout experiments, anything where "undo it by hand" would take longer than redoing the task. For a one-object tweak, `undo` is enough — don't burn checkpoint slots.

## 2. Make the change

Smallest mutation that tests the idea; batch independent edits with `batch_execute`. Follow the operator habits: wait out compilation after script changes, check the console after each mutation wave.

## 3. Verify with evidence, cheapest first

1. `read_console(types=["error"], format="summary")` — new errors?
2. Structural read — `query_scene` / `validate_scene_contracts` if the scene has a contract: does the result actually hold?
3. If behaviour changed: `run_tests_and_summarize(mode="EditMode")`.
4. Final gate for scene-level risk: `play_smoke_test(action="run", seconds=15)` — does it still boot and run clean? (Tests and smoke tests are VALIDATE; the `testing` tool group must be activated — see the `unity-test-pilot` skill.)

Decide on the evidence: **keep** if the goal is met and verification is clean; **roll back** if it isn't and iterating forward would be slower than restarting from the snapshot.

## 4a. Keep — clean up

Save the scenes (`manage_scene(action="save")`, verify not-dirty), then delete the checkpoint once it has no remaining value:

```python
manage_checkpoint(action="delete", id="before-hierarchy-refactor")
```

## 4b. Roll back — deliberately

```python
manage_checkpoint(action="restore", id="before-hierarchy-refactor", confirm=True)
```

Understand what you are acknowledging: restore **overwrites the scene files on disk** with the snapshot and **discards all current unsaved scene changes** — including any unrelated work a human left unsaved. `confirm:true` is destructive-operation acknowledgement metadata, not human confirmation; if there is any sign of unsaved human work in the editor beyond your own experiment, stop and ask before restoring. Restore is refused during play mode or compilation — exit/wait first rather than retrying.

After restoring, verify like any other change: console clean, the scene state actually matches pre-experiment (spot-check with `query_scene`).

## Report

State: what was checkpointed (and anything `skipped_untitled`), what changed, the verification evidence (console/tests/smoke, with numbers and job ids), and the outcome — kept (saved + checkpoint deleted) or rolled back (restored + verified). If you rolled back, say what was learned so the next attempt starts smarter, not just cleaner.
