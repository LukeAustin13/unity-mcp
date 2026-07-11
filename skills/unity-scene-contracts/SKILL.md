---
name: unity-scene-contracts
description: Author and validate JSON scene contracts through MCP for Unity — a contract declares what a Unity scene MUST and MUST NOT contain (required/forbidden objects, required components, camera/light caps, no missing references, tags/layers, build-settings membership, UI wiring), and validate_scene_contracts checks a loaded scene against it. Use when asked to "validate the scene", "make sure the menu scene always has X", "add a scene check to CI", or to codify scene invariants after a bug. Read-only — validation never opens, modifies, or saves anything and works in read_only mode.
license: MIT
---

# Unity Scene Contracts

A scene contract turns tribal knowledge ("the boot scene must have exactly one EventSystem and the Player must be tagged") into a JSON file the team and CI can enforce. Validation is READ-classified: it checks the **currently-loaded** scene, never opens one, never mutates anything.

## Validating

Provide **exactly one** of `contract` (inline JSON) or `contract_path` (project-relative `.json`, read Unity-side, sandboxed to the project root):

```python
validate_scene_contracts(contract_path="Assets/Contracts/main_menu.contract.json")
validate_scene_contracts(contract={...}, scene="MainMenu")   # scene must already be LOADED
```

Returns `{passed, scene, findings, summary, caveats}` — one finding per rule-entry with `rule`, `status`, `severity`, `object_path`, `details`, `suggested_fix`. `passed` is true only when zero findings failed.

## The contract schema (complete)

**Unknown top-level keys are rejected** — a typo'd rule fails the call instead of silently passing. These are all the supported keys:

```json
{
  "required_objects": ["Player", "UI/Canvas/PauseMenu"],
  "forbidden_objects": ["DebugConsole", "TestDummy"],
  "required_components": [
    { "object": "Player", "components": ["Rigidbody", "PlayerController"] }
  ],
  "max_cameras": 1,
  "max_lights": 4,
  "forbid_missing_references": true,
  "required_tags":   [ { "object": "Player", "tag": "Player" } ],
  "required_layers": [ { "object": "UI/Canvas", "layer": "UI" } ],
  "require_in_build_settings": true,
  "ui": { "require_event_system": true, "require_graphic_raycaster": true }
}
```

Notes:

- Object entries accept a full hierarchy path (`UI/Canvas/PauseMenu`) or a bare name (matches anywhere).
- `max_cameras` / `max_lights` count **enabled** components.
- `forbid_missing_references` scans for missing scripts and dangling serialized references — the most valuable single line in most contracts.
- `require_in_build_settings` requires the scene to be present **and enabled** in Build Settings.

## Authoring a contract from a working scene

Derive contracts from a known-good scene rather than from memory:

1. Load the scene in the editor (a human or a prior `write`-mode task — this skill never opens scenes).
2. Inventory the invariants: `query_scene(include=["components"])` for the anchors, `query_scene(component_type="Camera")` / `"Light"` for the caps, and the editor context for the active scene name.
3. Write the smallest contract that would have caught the bug you care about. Resist encoding everything — a contract listing 40 objects breaks on every refactor and gets deleted; one that pins the 6 load-bearing invariants survives.
4. Save it under version control, e.g. `Assets/Contracts/<scene>.contract.json` (writing the file needs `write` mode; in lower modes, emit the JSON for a human to commit).
5. Validate immediately — a brand-new contract should pass against the scene it was derived from. If it doesn't, the contract is wrong, not the scene.

## In CI

The CLI mirrors the tool against a running editor bridge:

```bash
unity-mcp scene validate-contracts --contract-path Assets/Contracts/main_menu.contract.json
```

Run it in `review_only` (or even `read_only`) server mode — validation needs nothing more, and a CI agent with no write capability cannot be prompt-injected into "fixing" the scene.

## Reporting

- Lead with `passed` and the failed-rule count, then list each failed finding with its `object_path` and `suggested_fix`.
- Findings are per rule-entry, so a pass is also informative: "9 rules evaluated, all passed" beats "OK".
- Report `caveats` from the result verbatim when present — they flag checks that degraded (e.g. a uGUI type unresolvable in this project).
- If asked to *fix* failures: that is `write`-mode work and a separate step. Propose the minimal edits implied by the `suggested_fix` fields and stop unless the mode and the user allow applying them.
