---
name: unity-scene-rebuild
description: Runs the procedural scene-setup step for States At War after structural code changes — triggers States At War > Setup Scene 3D via MCP, verifies expected objects exist, saves the scene, and confirms no new console errors. PROJECT-SPECIFIC EXAMPLE — before using in another project, replace the menu path, anchor objects, and expected counts with your own; keep the gate/verify/report structure.
license: MIT
---

This project's scene is procedurally generated — the editor menu item `States At War > Setup Scene 3D` is the authoritative source of truth for scene layout. Manual scene wiring is not used, and the scene file in version control is only a snapshot of a correct generation run. After any structural code change, this generation step must be re-run and verified before the task is considered complete.

---

## Step 0 — Confirm Scene Is Safe to Rebuild

Running `Setup Scene 3D` over a dirty scene will silently destroy any unsaved manual edits. Before doing anything else:

1. Call `manage_scene(action="status")` (or equivalent — verify the action name via `tool_search` if unsure). Read the `isDirty` / `dirty` flag.
2. **If the scene is dirty**: stop. Report the dirty state to the user and ask whether to: (a) discard the unsaved edits and rebuild, (b) save the current scene first as a backup, then rebuild, or (c) abort. Do not silently overwrite.
3. **If the scene is clean**: proceed to Step 1.

A clean baseline means the rebuild output is reproducible and the version-controlled scene file can be diffed cleanly afterward.

---

## Step 1 — Confirm Compile State

Call `read_console(types=["error","warning"], count=20)` and confirm there are no active compile errors before proceeding. Running scene setup over a broken build produces a corrupt or incomplete scene. If there are compile errors, stop here and use `/unity-edit-verify` to resolve them first.

Capture the warning count too — it becomes the baseline against which Step 5's "no new warnings introduced by the setup" check is measured.

---

## Step 2 — Run Setup Scene 3D and Wait for Completion

Call `execute_menu_item` with the exact path:

```
States At War/Setup Scene 3D
```

This menu item is defined in `States At War/Assets/Editor/SceneSetup3D.cs` at the `[MenuItem("States At War/Setup Scene 3D")]` attribute. It rebuilds the full Campaign scene procedurally: all 28 provinces, terrain zones, rivers, roads, armies, settlements, UI canvas, camera, and systems.

**Completion semantics.** `execute_menu_item` may return as soon as the menu item is invoked, not when its work has finished. Confirm completion explicitly:

1. After the call returns, poll `editor_state` (or equivalent: `read_console` will not show new logs until the main thread is free) until the editor is idle. If `isCompiling` flips on (the setup may have triggered a domain reload), wait until it flips back off.
2. Then immediately call `read_console(types=["error"], count=20)`. If the menu item threw, the exception will be logged here — investigate before proceeding.
3. If the call returned an explicit success/failure structure, use that as the primary signal and the console as the secondary.

Do not proceed to Step 3 while the editor is busy.

---

## Step 3 — Verify Core Objects and Counts

Use `find_gameobjects` to confirm the following structural anchors. Existence alone is not enough — count matters too, because a partial rebuild can leave orphan objects from prior state alongside the freshly generated ones.

| Object name | Expected components | Expected count |
|---|---|---|
| `Main Camera` | `Camera`, `RTSCamera` | exactly 1 |
| `DirectionalLight` | `Light` (type Directional) | exactly 1 |
| `GameManager` | `GameManager`, `AIController`, `SelectionHandler`, `FogOfWarController` | exactly 1 |

If any required anchor is **missing**, the scene generation failed or threw a partial error — check `read_console` for the failure point before continuing.

If any anchor has a **count > 1**, the setup did not clear prior state correctly. This is itself a finding worth surfacing: prior state was not cleared, and the duplicates need to be resolved (either by the user manually or by fixing `SceneSetup3D.cs` to call `DestroyImmediate` on existing instances before generating).

**Province spot-check.** Run a count query for province objects (e.g. `find_gameobjects(name_pattern="Province_*")` or list known names like `"Silverkeep"`, `"Thorngate"`). Expected: 28. Report the actual count. A mismatch indicates a generation failure mid-loop and should be investigated, not ignored.

---

## Step 4 — Save the Scene

Call `manage_scene` with `action: "save"` to write the freshly generated scene to disk. Do not rely on Unity auto-saving — explicit save is required so the scene file in version control reflects the current generation.

Verify the save succeeded by re-checking `manage_scene(action="status")` and confirming `isDirty == false` after the save call. A scene that still reports dirty after `save` indicates the save call failed silently.

---

## Step 5 — Console Check

Call `read_console(types=["error","warning"], count=30)` after the setup completes. Compare against the baseline captured in Step 1.

**Look for:**
- New `MissingReferenceException` or `NullReferenceException` traces from the setup itself — these are blocking.
- Missing serialized-field warnings on freshly created objects — indicate a component property was not wired by the setup script. Blocking unless the property is intentionally optional.
- Any error mentioning a type you recently changed that the setup script instantiates — blocking.
- New warnings about the scene file itself (e.g. orphaned components, missing scripts on serialized objects) — non-blocking but worth surfacing.

Pre-existing warnings that were present in the Step 1 baseline do not block completion, but note them if any are now duplicated (the rebuild may have re-introduced a previously-fixed warning).

---

## Step 6 — Report

State:
- **Trigger**: which structural change prompted the rebuild (e.g. "Added `FogOfWarController` to `GameManager`'s component list in `SceneSetup3D.cs`").
- **Pre-rebuild state**: dirty / clean (from Step 0); console baseline (errors / warnings from Step 1).
- **Verification result for each required anchor** in Step 3: `found (count=N)` or `missing`.
- **Province count**: actual count from the spot-check.
- **Scene save status**: confirmed (isDirty=false) or failed.
- **Console check result**: "no new errors or warnings" or list of new entries since baseline with their status.

Never write "scene rebuilt" without citing the verification results from Step 3 and the dirty-flag confirmation from Step 4.
