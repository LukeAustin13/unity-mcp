---
name: unity-edit-verify
description: The canonical read → hash-guarded edit → validate → wait-for-compile → check-console loop for any C# script change in the Unity project through MCP for Unity. Use whenever you create, modify, or delete a C# script and must confirm the project compiles cleanly before reporting done. Covers create_script / script_apply_edits / apply_text_edits / delete_script, SHA-guard and stale-file recovery, and the compile-wait protocol. Requires write safety mode (script edits are WRITE-class; delete_script is DESTRUCTIVE and needs confirm:true).
license: MIT
---

# Unity Edit & Verify

The one loop for changing C# in Unity: **read → hash-guarded edit → validate → wait for compilation → check console → report what compiled.** "It compiles cleanly" is a claim you back with a `read_console` read from this session, never an assumption.

This skill owns the *script edit* leg. It does not run the test framework — for EditMode/PlayMode runs and play-mode smoke tests, hand off to **unity-test-pilot**. For the base orient/plan/verify discipline it sits inside, see **unity-mcp-operator**.

## Posture first — this needs `write` mode

Editing or creating a script is **WRITE**; `delete_script` is **DESTRUCTIVE**. So before touching anything:

```python
safety_status()          # or read mcpforunity://server/safety
```

- `read_only` / `review_only` → you cannot edit. Report findings and the required mode ("this needs `write`; server is in `review_only` — no changes made") and stop. Do not shop for a tool that dodges the gate.
- `write` → proceed. `confirm:true` on a delete is destructive-op acknowledgement metadata, not human approval — it never relaxes the mode.

Then take one orientation read:

```text
mcpforunity://editor/context   # editor state + selection + prefab stage + console error counts, one read
```

If it reports Play Mode active, exit first (`manage_editor(action="stop")`, VALIDATE) — a script edit during play triggers a domain reload that discards runtime state. If `compilation.is_compiling` is true or `ready_for_tools` is false, wait (see Step 4) before trusting reads or firing edits.

---

## Step 1 — Read before touching

Read the current file so your edit targets real content:

| Need | Call |
|---|---|
| Full current contents | `manage_script(action="read", name="Foo", path="Assets/Scripts")` (READ) |
| Locate a pattern / line numbers in one file | `find_in_file(uri="Assets/Scripts/Foo.cs", pattern="void Update")` (READ) |

`find_in_file` searches a **single** file — it has no project-wide glob. Do not pass a `files="*.cs"` argument; it does not exist.

If you will make more than one edit to the same file, or another agent might edit it concurrently, capture its hash first:

```python
get_sha(uri="Assets/Scripts/Foo.cs")   # → {sha256, lengthBytes}
```

Hash-guard by default. Skip it only for a single trivial edit to a file you read this same turn. Cost: one call. Benefit: a mid-edit conflict is caught before it silently corrupts the file.

---

## Step 2 — Edit with the right tool

Pick deliberately. These are the current script-tool family — verify a call against its signature, never guess a param.

| Tool | Signature (key params) | Use when |
|---|---|---|
| `script_apply_edits` | `name, path, edits[], options?` | **Default.** Structured C# ops at method/class level. Also handles text ops. |
| `apply_text_edits` | `uri, edits[{startLine,startCol,endLine,endCol,newText}], precondition_sha256?` | Byte-exact text patch when you need exact coordinates or `script_apply_edits` refuses a malformed file. 1-indexed; tabs count as one column. |
| `create_script` | `path, contents, namespace?` | A brand-new file. `path` under `Assets/`, must end `.cs`; the server base64-encodes contents for you. |
| `delete_script` | `uri` | Remove a script. **DESTRUCTIVE — pass `confirm:true`.** |

`manage_script(action=create|read|delete)` is a legacy compatibility router — prefer the dedicated tools above; use it only for the plain `read`.

**`script_apply_edits` ops** (pass the exact op string):
- Structured: `replace_method`, `insert_method` (`position: after|before` + `afterMethodName`/`beforeMethodName`), `delete_method`, `replace_class`, `delete_class`, `anchor_insert`, `anchor_replace`, `anchor_delete`.
- Text: `prepend`, `append`, `replace_range`, `regex_replace`.

```python
script_apply_edits(
    name="SmartReach", path="Assets/Scripts/Interaction",
    edits=[{"op": "replace_method", "className": "SmartReach",
            "methodName": "HasTarget",
            "replacement": "public bool HasTarget(){ return currentTarget != null; }"}],
)
```

**Hash-guarding is per-tool — do not over-claim it:**
- `apply_text_edits` takes `precondition_sha256`. Pass the `sha256` from Step 1's `get_sha`. If the file changed since you read it, the call fails the precondition — re-read, recompute, and reapply; never blind-retry.
- `script_apply_edits` does **not** expose a precondition parameter — it captures the pre-edit SHA internally and verifies the write itself. To guard it explicitly, `get_sha` before and after and confirm the hash moved.
- `create_script` / `delete_script` take no SHA.

**Stale-file / disconnect recovery.** Script mutations trigger a domain reload that drops the TCP connection mid-call; the tools already handle this — they wait for the editor to come back and re-verify the write by SHA, returning `"...(verified after domain reload)"`. Treat a `connection closed` surfaced *without* that verification as "unknown" — re-read the file and check its SHA before assuming success. On an `overlap` error from `apply_text_edits`, your spans collide: split them or use one atomic batch.

**Never** use a generic file-write (host `Write`, editors outside MCP) on Unity C# — it bypasses the asset database and desyncs `.meta` files.

**Multiple files.** For one logical change spanning ≥3 files, wrap the edits in `batch_execute` (up to 25 commands; classified by its most severe command; a `delete_script` inside still needs its own `confirm:true`). Batch across *distinct* files — serial edits to the *same* file should stay serial so each SHA guard sees the prior write.

---

## Step 3 — Validate the touched files

```python
validate_script(uri="Assets/Scripts/Foo.cs", level="standard", include_diagnostics=True)
# → {diagnostics:[...], summary:{warnings, errors}}
```

`level` is only `basic` or `standard` (there is no "comprehensive"/"strict" level). `basic` checks interior syntax; `standard` adds structural checks. Fix any reported errors now, before compilation — do not carry known validation failures into Step 4.

`validate_script` is a static check, not the compiler — it is necessary but not sufficient. The authoritative pass/fail is the post-compile console (Step 5).

To discover what the server supports before a tricky edit, `manage_script_capabilities()` returns the supported structured/text ops, the `max_edit_payload_bytes` cap (default 256 KB), and active guards — use it to right-size or reshape an edit, not as a validation gate.

---

## Step 4 — Wait for compilation (do NOT call refresh_unity)

`create_script`, `script_apply_edits`, and `apply_text_edits` **already trigger import + compilation** and wait server-side for the editor to become ready. Calling `refresh_unity` after an edit is wrong — it forces a redundant reload. Do not add it.

Confirm compilation has actually settled before you trust anything:

```text
mcpforunity://editor/state      # read the resource; check compilation.is_compiling
```

Re-read until `compilation.is_compiling` is `false` (the field is `compilation.is_compiling`, snake_case — not `isCompiling`). Do not assume a fixed wait; different edits reload for different durations. A newly created type is unusable until this succeeds.

**Stall handling.** If `is_compiling` has not gone `false` after roughly 90 seconds, stop polling. Read `read_console(types=["error"], count=20)` — a compile failure or a modal Editor dialog can pin the flag — and report the stall with whatever the console shows plus the editor state. Never claim a result while a stall is unresolved.

---

## Step 5 — Check the console

```python
read_console(action="get", types=["error"], count=30)
# floods: format="summary" groups by error signature (count + one sample + file:line)
```

Compare against the error counts you saw in the Step-1 editor context.

- **Pass:** zero *new* compile errors. Pre-existing errors that predate your edit do not block completion — but call them out separately if relevant.
- **Fail:** any new `CS####` error, any `MissingReferenceException` from the reload, or any error naming a type/method you just changed. If the project builds warnings as errors (a `-warnaserror` in a `csc.rsp`), new warnings fail too — treat that as advisory and confirm before relying on it.

When errors flood with near-identical lines, `format="summary"` collapses them to distinct signatures — 500 identical NullReference lines are one bug. Pull `format="detailed"` with `include_stacktrace=True` only for the one signature you are chasing. Never `read_console(action="clear")` to "fix" a failure — clearing is a WRITE that destroys evidence.

If new errors appear, return to Step 2, fix the root cause, and repeat 3–5. Do not report complete until this step is clean.

**Deleting a script:** there is no MCP tool that greps the whole project for a C# symbol (`find_in_file` is single-file; `manage_project(action="find_references")` walks *asset* references, not source usages). Before `delete_script`, search the repo's `.cs` files for the type name with your own text search if you have file access. Either way, the compile in Step 4–5 is the real safety net — a delete that breaks a reference surfaces here as a `CS0246`/`CS0103`.

---

## Step 6 — Report what you actually observed

State it plainly; cite reads, not intentions:

- **Files:** paths under `Assets/`.
- **Tool used:** `create_script` / `script_apply_edits` / `apply_text_edits` / `delete_script` / `batch_execute` (N inner edits).
- **Hash-guarded?** yes (`precondition_sha256` on `apply_text_edits`) / internal-SHA verify (`script_apply_edits`) / no.
- **`validate_script`:** errors/warnings per file.
- **Compilation:** `is_compiling` reached `false` — or the stall you hit.
- **`read_console`:** "X errors, Y warnings — no new errors" or "new errors found and fixed: …", with pre-existing errors listed separately so they are not confused with regressions.

Never write "should compile" or "compiles cleanly" without a `read_console` result from this session behind it. If a step was skipped because the mode forbade it, say "skipped (`read_only`)" — never imply it ran. To verify runtime behaviour or run the test suite after a green compile, hand off to **unity-test-pilot** (its `testing` group is disabled by default and must be activated first).
