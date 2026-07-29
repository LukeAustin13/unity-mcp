---
name: unity-scene-setup
description: Generic, safety-aware workflow for building a fresh Unity scene through MCP for Unity — checks the safety posture first (this needs write mode), creates a new scene with a Main Camera and a Directional Light, adds the requested GameObjects, saves the scene via manage_scene, checks the console after each change, and verifies the result. Use when asked to "set up a scene", "create a new scene", "build a starter scene", or scaffold a scene from scratch. Not project-specific — for a project's own procedural scene generator, use that project's dedicated scene-rebuild skill instead.
license: MIT
---

# Unity Scene Setup (generic)

A generic, safety-aware workflow for creating a fresh Unity scene from scratch through MCP for Unity: a new scene with a camera and a light, your objects added, saved to disk, and verified.

This is **not** project-specific. If a project has its own authoritative procedural scene generator (a `Setup Scene` menu item, a build script, etc.), that generator is the source of truth — use the project's dedicated scene-rebuild skill and do not hand-wire the scene here. Use *this* skill for generic, from-scratch scene scaffolding where there is no such generator.

## This workflow needs WRITE mode

Creating a scene, adding objects, and saving are **WRITE** actions (persistent project mutation). They are only permitted when the server safety mode is `write`. In `read_only` or `review_only` these calls are refused by the enforcement layer — so check the posture first and stop early with a clear message rather than firing writes that will bounce.

Note on "destructive" vs "write": creating, loading, and saving a scene are WRITE, not DESTRUCTIVE. The steps below that *are* destructive — deleting a GameObject, deleting an asset — need `confirm:true` **acknowledgement metadata** on the call. That `confirm:true` is a machine acknowledgement that the action is destructive; it is **not** human confirmation and it never relaxes the safety mode. You still need `write` mode; `confirm:true` only satisfies the extra destructive-action gate on top of it. `manage_scene` itself has no `confirm` parameter — do not pass one to it.

## Step 0 — Check the safety posture

```
safety_status()
```

or read `mcpforunity://server/safety`.

From the result:
- If `mode != "write"`, **stop.** Report: this scene-setup workflow requires `write` mode, the server is currently in `<mode>`, and no changes were made. Do not attempt any create/add/save.
- If `mode == "write"`, note `destructive_requires_confirm` (it will be `true`) — you will need `confirm:true` on any *destructive* step (e.g. deleting a GameObject or asset later). Scene create/load/save are not destructive and take no `confirm`.

## Step 1 — Check editor readiness and guard the current scene

Read the editor context / readiness snapshot before creating anything:

```
get_editor_context()
```
or fall back to `mcpforunity://editor/state`.

Confirm the editor is idle: not `is_compiling`, not `is_domain_reload_pending`, `ready_for_tools == true`. If it is busy, let it settle first — creating a scene mid-compile is unreliable.

### Step 1b — Do not silently destroy an unsaved scene

Creating or loading a new scene replaces whatever is currently open. If the currently open scene has unsaved changes, replacing it loses that work.

1. Check the active scene's dirty state — read it from the editor context, or via `manage_scene(action="get_active")` / `get_loaded_scenes` (both read-only).
2. If the current scene is **dirty**: do not blindly overwrite. Creating/loading a scene replaces what is open and will discard those unsaved changes — there is no `confirm` gate on `manage_scene` to catch this, so it is on you to protect the work. Save it first with `manage_scene(action="save")` if the user wants it kept, or ask the user when it is ambiguous. Only proceed once the current scene is saved or the user has said it is safe to discard.
3. If the current scene is **clean**, proceed.

## Step 2 — Create the new scene

Create the scene. `name` is **required**; templates available: `empty`, `default`, `3d_basic`, `2d_basic`. Give a `path` too so the file lands where intended, and pick a fresh name/path — `create` fails if the target scene file already exists.

```
manage_scene(action="create", name="MyScene", path="Assets/Scenes/MyScene.unity", template="3d_basic")
```

- `3d_basic` / `default` typically already include a Main Camera and a Directional Light (and `3d_basic` also adds a `Ground` plane) — read the resulting hierarchy (Step 4 below) and only add a camera/light if they are missing, to avoid duplicates.
- `empty` gives you nothing — you must add both the camera and the light yourself in Step 3.

Then check the console:

```
read_console(action="get", types=["error"], count=10, format="detailed")
```

## Step 3 — Ensure a Camera and a Directional Light, then add objects

First confirm what the template gave you (see Step 4's hierarchy read), then fill gaps.

### Camera (only if missing)

```
manage_gameobject(action="create", name="Main Camera", components_to_add=["Camera"])
```

Give it a sensible transform if the template did not:

```
manage_gameobject(action="modify", target="Main Camera", position=[0, 1, -10], rotation=[0, 0, 0])
```

### Directional Light (only if missing)

```
manage_gameobject(action="create", name="Directional Light", components_to_add=["Light"])
```

Then set the Light component's type to Directional and a downward-facing rotation:

```
manage_components(action="set_property", target="Directional Light", component_type="Light", properties={"type": "Directional"})
manage_gameobject(action="modify", target="Directional Light", rotation=[50, -30, 0])
```

`set_property` takes `component_type` plus either a single `property`/`value` pair or a `properties` map, and payload shapes vary by component — if this template shape is rejected, read the component's resource payload and adapt the property name/value.

### The requested objects

Add whatever the task calls for. Batch independent creates for speed:

```
batch_execute(commands=[
  {"tool": "manage_gameobject", "params": {"action": "create", "name": "Ground", "primitive_type": "Plane"}},
  {"tool": "manage_gameobject", "params": {"action": "create", "name": "Player", "primitive_type": "Capsule", "position": [0, 1, 0]}},
  {"tool": "manage_gameobject", "params": {"action": "create", "name": "Obstacle", "primitive_type": "Cube", "position": [3, 0.5, 2]}}
])
```

If the batch contains any destructive command (e.g. deleting an existing object first), that command needs its own `confirm:true` inside its `params`; the batch is classified by its most-severe command.

After the additions, check the console again:

```
read_console(action="get", types=["error"], count=10, format="detailed")
```

## Step 4 — Verify the hierarchy

Read the scene hierarchy and confirm the essentials exist exactly once:

```
manage_scene(action="get_hierarchy", page_size=50)
```

or find the anchors directly:

```
batch_execute(commands=[
  {"tool": "find_gameobjects", "params": {"search_term": "Camera", "search_method": "by_component"}},
  {"tool": "find_gameobjects", "params": {"search_term": "Light",  "search_method": "by_component"}}
])
```

Confirm: exactly one active Camera, exactly one Directional Light, and each requested object present. A count > 1 for camera/light usually means the template already provided one and you added a duplicate — remove the extra (a delete is destructive, so pass `confirm:true`):

```
manage_gameobject(action="delete", target="<duplicate instance id>", confirm=true)
```

## Step 5 — Save the scene

```
manage_scene(action="save", path="Assets/Scenes/MyScene.unity")
```

Provide an explicit `path` for a brand-new scene so it is written where intended. Then verify the save landed:

```
manage_scene(action="get_active")
```

Confirm the active scene now reports a valid on-disk path and is no longer dirty. A scene still reporting dirty after `save` means the save did not take — investigate before reporting success.

Optionally, capture a screenshot to confirm the scene looks right:

```
manage_camera(action="screenshot", include_image=True, max_resolution=512)
```

## Step 6 — Report

State plainly:
- Safety mode observed (and, if it was not `write`, that the workflow stopped without changes).
- Scene template used and the final save path.
- Which anchors were verified (`Camera`, `Directional Light`) and their counts.
- Which requested objects were created.
- Console result after the changes: "no errors" or the errors seen.
- Save confirmation (path written, not dirty).

Never report "scene created and saved" without the Step 4 verification and the Step 5 not-dirty confirmation.
