---
name: unity-mcp-operator
description: The base operating layer for any work in a Unity project through MCP for Unity (hardened fork) — how to start a session, plan within the active safety mode, gather the cheapest evidence that answers the question, batch and page efficiently, and verify every change before claiming success. Use whenever driving the Unity Editor via MCP tools — inspecting, editing, testing, or automating — especially at the start of a session or when a tool call is refused. Works in every safety mode; it teaches how to find out which one is active.
license: MIT
---

# Unity MCP Operator

How to operate the Unity Editor through this MCP server well: cheap, safe, and honest. The loop is always the same:

**Orient → plan to the posture → act at the cheapest tier that answers the question → verify → report what you actually observed.**

## 1. Orient before anything else

Two reads, at the start of every session, before the first real action:

```python
safety_status()                      # or read mcpforunity://server/safety
# → mode, allowed_classes, destructive_requires_confirm,
#   execute_code_enabled, arbitrary_menu_items_enabled
```

```text
mcpforunity://editor/context         # resource — read it, don't call it
# → editor state + selection + prefab stage + console error counts in one read
# (fallback on older servers: mcpforunity://editor/state)
```

From the editor context, check readiness: if it reports compiling or a pending domain reload, wait before trusting reads or firing writes — poll the `editor_state` resource's `compilation.is_compiling` field until false. If `ready_for_tools` is false, report the `blocking_reasons` instead of proceeding on untrustworthy data. If no Unity instance is connected, stop and say so.

## 2. Plan to the posture

Every tool call is classified and enforced server-side before it reaches Unity:

| Class | Meaning | In `read_only` | In `review_only` | In `write` |
|---|---|---|---|---|
| READ | No state change (queries, scans, resources) | ✅ | ✅ | ✅ |
| VALIDATE | Transient editor state only (tests, play mode, screenshots, refresh) | ❌ | ✅ | ✅ |
| WRITE | Persistent mutation (create/modify/save) | ❌ | ❌ | ✅ |
| DESTRUCTIVE | Deletes data, runs builds, executes arbitrary code/menu items | ❌ | ❌ | ✅ + `confirm:true` |

Plan the whole task inside what the mode allows. In `read_only`, an audit that ends with "then I'll fix it" is the wrong plan — the right plan ends with a findings report and suggested fixes.

**`confirm:true` is destructive-operation acknowledgement metadata, not human confirmation.** It is a machine acknowledgement that the call destroys data. It never relaxes the mode: in `read_only`/`review_only` no value of `confirm` makes a destructive call run. Real safety comes from the externally configured safety mode and policy — never present `confirm:true` as if a human approved anything.

Two more posture facts to respect:

- `execute_code` is **disabled by default** even in `write` mode. Do not plan around it; write a script with the script tools and let Unity compile it instead.
- Non-core tool groups (`testing`, `vfx`, `animation`, `ui`, …) start disabled. If a tool you expect is missing, run `manage_tools(action="list_groups")` and `manage_tools(action="activate", group="testing")` rather than concluding the capability doesn't exist.

## 3. Act at the cheapest tier that answers the question

Escalate through evidence tiers only when the cheaper tier cannot answer. Most questions die at tier 1 or 2.

**Tier 1 — resources.** Editor context, project info, scene lists. One read, no round-trip loops.

**Tier 2 — structured queries.** `query_scene` is "SQL for the scene": filter + projection in one paged call, replacing the find-object → get-components → get-properties loop.

```python
# "How big is every enemy, and what's on it?" — one call, not N
query_scene(component_type="EnemyController",
            include=["world_bounds", "components"], page_size=50)
```

Projections: `transform`, `world_bounds`, `components`, `materials`, `mesh_stats`. Ask only for what you need — every extra projection is tokens.

**Tier 3 — targeted scans.** The read-only audit tools (`manage_project`, `audit_ui_layout`, `validate_scene_contracts`, `validate_build`) return structured findings with severities and suggested fixes. Prefer them over hand-rolling the same analysis from raw reads.

**Tier 4 — zero-pixel visual facts.** Before any screenshot, ask whether geometry already answers it:

```python
manage_camera(action="visibility_report", camera="MainCamera")
# → per-object in_frustum, screen_rect, coverage_pct, distance — no image
```

**Tier 5 — pixels, last.** When you genuinely need to *see*: inline screenshots are right-sized by default; keep `max_resolution` at 256–512 for scene understanding. For "did the frame change?" use `manage_camera(action="screenshot_compare", baseline_path=...)` — a numeric diff, no image shipped.

Console reads have the same discipline: when errors flood, use `read_console(action="get", types=["error"], format="summary")` — deduped signatures with counts and a sample, instead of hundreds of near-identical lines.

## 4. Efficiency rules

- **Batch independent operations.** `batch_execute` takes up to 25 commands (including multiple `find_gameobjects` for discovery). A batch is classified by its most severe command — a delete inside it still needs its own `confirm:true` in that command's params.
- **Page deliberately.** Follow `next_cursor` only when the task needs the full list. For a verdict, the summary plus the first page of findings is usually enough — report counts, not floods.
- **Use the caps.** Scans expose `max_issues` / `max_findings` / `page_size`. Set them to what the answer needs.
- **Don't re-read what you already have.** The editor context from step 1 answers scene/selection/error-count questions for the rest of the turn unless something changed it.

## 5. Writing safely (when the mode allows)

- Make the smallest mutation that achieves the goal; batch the independent ones.
- After **every** script write, the tools auto-trigger import + compilation — do not call `refresh_unity`. Wait for `compilation.is_compiling` to go false, then `read_console(types=["error"], count=10)`. A new component/type is unusable until compilation succeeds.
- After scene/object mutations, verify with a read: `query_scene` or `find_gameobjects` for existence/placement, console for errors.
- Before a risky multi-step scene change, snapshot with `manage_checkpoint(action="create", label="before-x")` so you can roll back — the full loop is the `unity-safe-iteration` skill.
- Guard other people's work: creating or loading a scene replaces the open one, and there is no `confirm` gate on `manage_scene` — check the active scene's dirty state first and never silently discard unsaved changes.

## 6. When a call is refused

Do not retry verbatim, and do not shop for an equivalent tool that dodges the gate — the classification is the point.

1. Read the refusal: it names the class you tripped.
2. Re-check `safety_status()` if the posture might have changed.
3. Either switch to an action the mode allows (e.g. report findings instead of fixing), or tell the user plainly: "this needs `write` mode; the server is in `read_only` — no changes were made."

A missing tool is a different failure from a refused tool: missing usually means its group is deactivated (see §2), refused means the mode forbids its class.

## 7. Report what you actually observed

- Never claim "created/fixed/passing" without the verifying read that shows it. "Saved" means the post-save read reported not-dirty; "tests pass" means a summarized run returned `all_passed: true` with a job id.
- If a step was skipped because the mode forbids it, say "skipped (`read_only` mode)" — never imply it succeeded.
- Cite the numbers you read (error counts, finding counts, pages truncated). If a scan was capped or a page unfollowed, say the coverage was partial.
- Findings marked `advisory: true` are heuristics — report them as "worth checking", not defects.
