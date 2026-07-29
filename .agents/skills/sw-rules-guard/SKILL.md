---
name: sw-rules-guard
description: Pre-flight and post-edit compliance check for States At War — enforces GameManager authority, turn-flow gates, combat trigger model, FactionId rules, input system constraints, prohibited changes, project scope, and assembly/namespace hygiene. Run this skill before any substantive edit to a core system, and again before marking a task complete. PROJECT-SPECIFIC EXAMPLE — before using in another project, replace the gates with your own project's non-negotiable rules; keep the pre-flight/post-edit gate structure and the block-and-explain contract.
license: MIT
---

Apply the project's non-negotiable rules from `AGENTS.md` before edits happen (to catch prohibited directions early) and after edits are drafted (to catch accidental rule breaks). Each gate below is a hard check — not a suggestion. If a gate fails, block the change and explain exactly which rule was violated and what the compliant alternative is.

---

## Gate applicability quick reference

Use this table to skip gates that obviously don't apply to the current change. Each gate's own "When to check" section is authoritative; the table just helps an executing agent triage fast.

| Gate | Pre-flight (before editing) | Post-edit (before marking done) | Skip if change is |
|---|:-:|:-:|---|
| 1 — GameManager Authority | ✓ | ✓ | Pure UI, art, audio, or non-`GameManager`-touching |
| 2 — Turn-Flow Guarantees | ✓ | ✓ | Outside the turn callgraph (no `GameManager`, `ArmyUnit`, `SelectionHandler`, `AIController`, panel dismiss) |
| 3 — Combat Trigger Model | ✓ | ✓ | No movement, proximity, or combat initiation code touched |
| 4 — FactionId, Not Enum | — | ✓ | No new ownership comparisons added |
| 5 — Input System | ✓ | ✓ | No input handling touched |
| 6 — Prohibited Changes | ✓ | — | Within the requested task's surface (no architectural sprawl) |
| 7 — Performance Hot Paths | — | ✓ | No `Update`, `FixedUpdate`, `LateUpdate` or callees touched |
| 8 — Git Co-Author Rule | — | ✓ (at commit time) | Not committing this turn |
| 9 — Scope Discipline | ✓ | ✓ | Strictly the user's requested change with no adjacent additions |
| 10 — asmdef & Namespace Hygiene | ✓ | ✓ | No new files, no namespace changes, no asmdef changes |

Gates 1, 2, 5, 6, 9 are the most frequent blocks. When in doubt, check.

---

## Gate 1 — GameManager Authority

**When to check**: any change that writes to `GameManager` state — fields, properties, or method calls that mutate `_combatPhase`, `_selectedArmy`, `_armies`, `_settlements`, `IsPlayerTurn`, `IsComputerActing`, or `IsResolvingCombat`.

**Rule**: the only scripts permitted to mutate `GameManager` state are:
- `SelectionHandler`
- `AIController`
- `UIController`
- `PreBattlePanel`
- `BattleReportPanel`
- `GameManager` itself

If the change originates from any other script, it is not permitted. Adding a new approved writer requires explicit user sign-off, a concrete justification, and an ADR. Block any new caller that does not meet this bar.

---

## Gate 2 — Turn-Flow Guarantees

**When to check**: any change to `GameManager.cs`, `ArmyUnit.cs`, `SelectionHandler.cs`, `AIController.cs`, or any panel script that dismisses combat.

**Rule**: the canonical turn-flow callgraph must remain intact:

```
Player move → TryMoveSelectedArmy() → ArmyUnit.TryMoveTo()
  → OnPlayerArmyMoveComplete()
    [combat] → BeginPrebattle() [phase=PreBattle]
      [auto-resolve] → ExecuteAutoResolve() → phase=BattleReport
        [dismiss]   → DismissBattleReport() → phase=None
      [retreat]     → CancelCombat()        → phase=None
    [no combat] → CheckSettlementCapture() → CheckWinLoss()
  Player ends turn → EndPlayerTurn()
    → AIController.TakeTurn()
    → WaitUntil(!IsResolvingCombat)
    → BeginPlayerTurn()
      → TickTiredness() [must fire BEFORE ResetMovement]
      → ResetMovement()
      → ProcessEconomy()
```

Specifically verify:
- `IsPlayerTurn` gates all player actions.
- `IsComputerActing` gates all player input during the AI turn.
- `IsResolvingCombat` (`CombatPhase != None`) gates all player actions while any combat panel is open.
- `TickTiredness()` fires **before** `ResetMovement()` at every turn boundary. Reversing this order corrupts fatigue capture.

Block any change that breaks an edge in this graph or removes a gate.

---

## Gate 3 — Combat Trigger Model

**When to check**: any change to movement, proximity detection, or combat initiation logic in `ArmyUnit.cs`, `SelectionHandler.cs`, `AIController.cs`, or `GameManager.cs`.

**Rule**: player armies use **explicit click-to-attack only**. Combat is never triggered for the player army by proximity alone. AI armies retain proximity-based triggers. This is a deliberate design decision (see project memory `project_combat_model`).

Block any change that wires an automatic proximity check to a player-army combat call.

---

## Gate 4 — FactionId, Not Enum

**When to check**: any new or modified code that compares faction ownership.

**Rule**: the `Faction` enum was removed and replaced with the `FactionId` struct (see project memory `project_faction_system`). All comparisons must use:
- `FactionRegistry.IsHumanPlayer(owner)`
- `FactionRegistry.IsNeutral(owner)`
- Or direct `FactionId` equality via the struct's `==` operator.

Never use `== Faction.Player`, `== Faction.Neutral`, or any other `Faction` enum member — those types no longer exist. Flag any such comparison as a compile-time regression.

Detection pattern:
```
find_in_file(pattern="Faction\\.(Player|Neutral|Enemy|Computer|None)", files="*.cs")
```
Any hit is a blocker.

---

## Gate 5 — Input System

**When to check**: any change to input handling in `SelectionHandler.cs`, `UIController.cs`, camera scripts, or any new component that reads mouse or keyboard input.

**Rule**: this project uses the **New Input System only**. The following are prohibited:
- `Input.GetMouseButton`, `Input.GetKey`, `Input.mousePosition`, or any `UnityEngine.Input.*` API.
- `StandaloneInputModule` component or references.
- Any mixing of old and new input paths.

`Mouse.current`, `Keyboard.current`, and `InputSystemUIInputModule` are the correct APIs. `EventSystem.IsPointerOverGameObject()` must be used to prevent UI clicks leaking into world selection — flag any click-handling that omits this guard.

Detection pattern:
```
find_in_file(pattern="UnityEngine\\.Input\\.|Input\\.GetMouse|Input\\.GetKey|Input\\.mousePosition|StandaloneInputModule", files="*.cs")
```

---

## Gate 6 — Prohibited Changes

**When to check**: before starting any task that touches architecture, dependencies, or cross-cutting systems.

The following changes require **explicit user request, blast-radius analysis, and written justification** before any implementation begins. If the task implies one of these without the user having explicitly requested it, stop and confirm intent before writing a single line:

- Replacing `GameManager` with another authority model.
- Adding an event bus, service locator, or dependency injection framework.
- Converting polling to event-driven architecture purely for aesthetic reasons.
- Swapping the input architecture.
- Introducing multi-scene runtime flow.
- Adding assembly definitions as a broad reorganisation. (See also Gate 10.)
- Adding package dependencies for problems solvable with existing code.
- Rewriting working systems because a theoretically better architecture exists.
- Broad folder/file restructuring or prefab architecture shifts.
- Scene generation rewrites.
- Introducing ScriptableObject-driven architecture broadly.

---

## Gate 7 — Performance Hot Paths

**When to check**: any change to `Update()`, `FixedUpdate()`, `LateUpdate()`, or methods called from them.

Flag the following patterns as violations:
- `FindObjectOfType<T>()`, `FindFirstObjectByType<T>()`, `FindAnyObjectByType<T>()`, or `GameObject.Find()` called inside `Update()` or any per-frame path.
- `renderer.material` mutation (allocates a new instance per call — leaks materials).
- `renderer.sharedMaterial` mutation (corrupts shared assets).
- Allocation-heavy LINQ (`.Where().Select().ToList()`, `.OrderBy()`) in per-frame code.
- Per-frame `new` allocations for objects that could be cached or pooled (especially `new Vector3(...)` in tight loops — these are stack-allocated structs and are fine; but `new List<T>()`, `new T[N]`, `new StringBuilder()` are heap allocs).
- `string` interpolation (`$"..."`) or `+` concatenation per-frame instead of cached `StringBuilder`.
- `GetComponent<T>()` called instead of using a cached reference acquired in `Awake` / `OnEnable`.

Detection patterns to run:
```
find_in_file(pattern="FindObjectOfType|FindFirstObjectByType|FindAnyObjectByType|GameObject\\.Find", files="*.cs")
find_in_file(pattern="\\.material\\s*=|\\.sharedMaterial\\s*=", files="*.cs")
find_in_file(pattern="\\.Where\\(|\\.Select\\(|\\.ToList\\(|\\.OrderBy\\(", files="*.cs")
find_in_file(pattern="GetComponent\\s*<", files="*.cs")
```

For each hit, verify the enclosing method. A hit inside `Awake`, `Start`, `OnEnable`, or an event handler is not a violation. A hit inside `Update`, `FixedUpdate`, `LateUpdate`, or a method demonstrably called from one of those (trace the call chain via `find_in_file` for the containing method name) is a Block.

---

## Gate 8 — Git Co-Author Rule

**When to check**: before committing or generating a commit message.

Never add `Co-Authored-By: Codex` or any variant of Codex attribution to commit messages, branch names, or PR descriptions. This is a hard user preference (see project memory `feedback_no_coauthor`).

---

## Gate 9 — Scope Discipline

**When to check**: whenever the implementation of a task grows beyond what was explicitly requested.

Apply `AGENTS.md` "Smallest viable change." If you notice adjacent improvements while working on the requested task, list them as separate follow-up items. Do not silently include them in the current change unless they are required for correctness of the requested feature.

---

## Gate 10 — Assembly Definition & Namespace Hygiene

**When to check**: any change that creates a new C# file, adds or moves an `.asmdef`, or introduces a new top-level namespace.

**Rule**: this project's assembly and namespace structure is deliberate. Unilateral changes to either compound and are hard to undo.

**Block**:
- Creating a new `.asmdef` without explicit user request and an ADR. New asmdefs reshape compile boundaries and are part of Gate 6's prohibited changes when done as a broad reorganisation; even one-off additions require justification.
- Moving an existing `.asmdef` to a different folder, or adding/removing references between asmdefs.
- Introducing a new top-level namespace (e.g. `StatesAtWar.NewFeature` when the existing convention is `StatesAtWar.Combat`, `StatesAtWar.AI`, `StatesAtWar.UI`, etc.) without checking what the convention is and either matching it or proposing the new namespace explicitly.
- Placing a new script in a folder structure that does not match the existing namespace convention — file location and namespace must agree.

**Warn (not block)**:
- A new file with no namespace declaration when the surrounding files do declare one. Surface for fixing but don't block.
- A namespace that matches convention but with inconsistent casing (e.g. `statesAtWar.Combat` when others use `StatesAtWar.Combat`).

Detection workflow:
1. Before creating a new file, run `find_in_file(pattern="^namespace\\s+", files="*.cs", limit=20)` to confirm the existing convention.
2. Check whether a relevant `.asmdef` already exists for the area you're adding to. If yes, reuse it. If no, ask the user before creating one.
3. Match folder structure to namespace structure — if your file is at `Assets/Scripts/Combat/NewCombatThing.cs`, the namespace should almost certainly be `StatesAtWar.Combat` (verify against existing files in that folder).

---

## Reporting

For each gate checked, report one of:

- **Pass** — rule not applicable to this change, or applicable and satisfied.
- **Warn** — rule applies; change is borderline; flag it to the user for awareness.
- **Block** — rule applies and is violated; state exactly which rule, what the violation is, and what the compliant alternative is.

### Example outputs

**Pass example:**
> Gate 1 (GameManager Authority): Pass — change touches only `RTSCamera.cs`; no `GameManager` state mutation introduced.

**Warn example:**
> Gate 9 (Scope Discipline): Warn — the requested task is "fix the camera zoom on scroll", but the patch also renames three unrelated private fields in `RTSCamera.cs` for consistency. Recommend splitting the rename into a follow-up PR so the zoom fix can be merged and reverted independently if needed.

**Block example:**
> Gate 4 (FactionId, Not Enum): **Block** — `AIController.cs:147` introduces `if (army.Owner == Faction.Computer)`. The `Faction` enum was removed; this will not compile. Compliant alternative: `if (!FactionRegistry.IsHumanPlayer(army.Owner) && !FactionRegistry.IsNeutral(army.Owner))` — or, if a more specific computer-owned check exists in `FactionRegistry`, use that.

Do not proceed past a **Block** without the user explicitly overriding it and acknowledging the rule being broken.
