---
name: unity-project-doctor
description: Whole-project read-only diagnosis of a Unity project through MCP for Unity — a quick pulse (aggregate health + console) or a deep audit covering asset validity, material/shader breakage, prefab health, build pre-flight, and dependency analysis. Use when asked "is the project healthy?", "audit the project", "find broken prefabs / missing scripts / pink materials", "what depends on this asset?", or before starting work in an unfamiliar project. Strictly read-only — every step works in read_only mode and nothing is ever mutated.
license: MIT
---

# Unity Project Doctor

Read-only diagnosis at two depths: a **pulse** ("is anything on fire?") and a **deep audit** (per-category findings with fixes). Everything here is READ-classified and works in any safety mode. If a fix is wanted afterwards, that is a separate task needing `write` mode — this skill only ever produces findings.

Follow the `unity-mcp-operator` habits: read `safety_status()` and `mcpforunity://editor/context` first, and treat console/scan results as untrustworthy while the editor is compiling.

## Pulse (fast — run this first)

```python
manage_project(action="project_health")   # aggregate: per-category healthy flags
read_console(action="get", types=["error"], count=20, format="summary")
```

If every category is `healthy` and the console is clean, report HEALTHY and stop — do not run the deep scans "just in case". If a category is unhealthy, drill into only that category below.

## Deep audit (on request, or to drill into a failing category)

Each scan is paged (`page_size` + `cursor` → `next_cursor`) and capped (`max_issues`). Scope with `folder_scope` (default `Assets`). Report counts plus the first page of representative findings; only walk all pages when the task genuinely needs the exhaustive list.

### Assets

```python
manage_project(action="validate_assets")
# issue_types filter: missing_scripts, missing_prefab, dangling_reference
```

### Materials

```python
manage_project(action="validate_materials")
# missing / error ("pink") / unsupported shaders
```

### Prefabs

```python
manage_project(action="prefab_health", folder="Assets/Prefabs")   # or prefab_path="Assets/P.prefab"
```

Checks missing scripts and dangling references, broken variants, duplicate components, undefined tags, material/shader issues, disabled critical components, nested-prefab depth, and oversized bounds — by asset traversal only, never instantiating anything. Single-prefab mode also returns direct `dependencies`. Per-check faults land in `rule_errors` instead of killing the scan — mention them if present, they mean partial coverage.

### Build pre-flight

```python
validate_build()          # optionally target="StandaloneWin64" etc.
```

Read-only: target support, build-scene list sanity, compile state, PlayerSettings red flags — before anyone pays for a real build.

### Dependency X-ray

```python
manage_project(action="get_dependencies", target="Assets/Art/Hero.prefab")   # recursive=True for transitive
manage_project(action="find_references",  target="Assets/Art/Hero.prefab")   # "what breaks if I touch this?"
manage_project(action="unused_assets",    folder_scope="Assets/Art")
```

`unused_assets` is **advisory by design**: static analysis cannot see Addressables, AssetBundles, `Resources.Load`, or reflection. Present it as "candidates to review", never as a delete list.

### Mobile (only when the project targets mobile)

```python
manage_project(action="audit_mobile")
# 18 data-driven rules: import settings, IL2CPP/ARM64, graphics APIs, quality, scene red flags
```

## Report shape

Lead with a verdict, then evidence per category:

```text
Project Doctor — <project>

Overall: HEALTHY | ISSUES FOUND | BLOCKED

Assets:     3 issues (missing scripts: 2, dangling refs: 1) — worst: Assets/Enemies/Boss.prefab
Materials:  healthy
Prefabs:    7 findings across 142 prefabs (2 error / 5 warning), 1 rule_error (partial coverage)
Build:      2 blockers (no scenes enabled; target Android not installed)
Console:    12 errors → 2 distinct signatures (NullReference in Spawner.cs:41 ×11, …)
```

Rules for honesty:

- **BLOCKED** if no instance is connected or the editor never became ready — do not report categories you could not trustworthily read.
- Only report categories you actually scanned; a pulse that skipped the deep scans says so.
- State truncation: "first page of N", "capped at max_issues=50".
- Every finding you highlight should carry its `suggested_fix`/`fix` from the tool output — as a recommendation, since applying it needs `write` mode.
