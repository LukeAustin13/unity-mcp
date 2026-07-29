---
name: unity-edit-verify
description: Executes the canonical script-edit-and-verify loop for any C# change in the Unity project — read, hash-guarded edit, validate, refresh domain, check console. Use this skill whenever you create, modify, or delete a C# script and need to confirm the project compiles cleanly before reporting the task complete.
license: MIT
---

Treat "it compiles cleanly" as a verifiable outcome, not an assumption. Never report a script change complete without a confirmed `read_console` showing zero new errors. Every step in this skill is mandatory; skipping any step makes the outcome unverifiable.

Before starting: if the editor is in Play Mode, exit it first. A script edit during play triggers a domain reload that discards runtime state and can leave the editor in an inconsistent state.

---

## Step 1 — Read Before Touching

Before editing any script, read the current file contents. Use `view` (for full-file inspection) or `find_in_file` (for targeted pattern lookup) — whichever gets you the minimum context needed to make a correct edit.

If you will make multiple edits to the same script in sequence (or if another agent may have edited it concurrently), call `get_sha` on the script first. Capture the returned hash — you will use it in Step 2 to guard against overwriting concurrent changes.

Hash-guard by default. Skip it only for a single trivial edit to a file read in this same turn. The cost is one tool call; the benefit is detecting a mid-edit conflict before it produces a silently wrong file.

---

## Step 2 — Edit With the Right Tool

The MCP exposes multiple C# editing tools with different use cases. Pick deliberately:

| Tool | Use when |
|---|---|
| `script_apply_edits` | Structural C# edits that operate at the method / class / member level — insert a method, rename a parameter, add an attribute. The tool understands C# structure and is safer for non-trivial changes. **Default choice for most edits.** |
| `apply_text_edits` | Raw text-level patches when you need byte-exact control — fixing a whitespace issue, editing a string literal precisely, patching a malformed file `script_apply_edits` refuses. Less safe; the tool does not validate C# structure. |
| `create_script` | Creating a new script file from scratch. Provide the full path relative to `Assets/`, the script contents, and the correct namespace matching the surrounding files. |
| `delete_script` | Removing a script. Before calling, run `find_in_file(pattern="<ClassName>", files="*.cs")` to confirm no remaining script references the deleted type. A delete without this check can produce hidden compile failures. |

**Multi-file edits.** If you are touching ≥3 files in a single logical change, wrap the edits in `batch_execute` rather than calling the edit tools serially. One round-trip is dramatically faster than N, and the batch arrives atomically as far as the editor is concerned.

**Hash-guarding.** For `script_apply_edits` and `apply_text_edits`, pass the `sha` captured in Step 1. If the hash has changed since you read the file, abort and re-read before proceeding — do not blindly retry.

Never use blind file-write tools (e.g. a generic `Write`) for Unity C# scripts if MCP script tools are available — they bypass Unity's asset database tracking and can produce out-of-sync `.meta` files.

---

## Step 3 — Validate

Call `validate_script` on each script you touched. If validation reports errors, fix them now, before refreshing Unity. Do not proceed to Step 4 with known validation failures.

If `manage_script_capabilities` reports that the current validation mode (Basic / Standard / Comprehensive / Strict) restricts certain edits, respect those restrictions and find an alternative approach rather than working around them. The validation level is set by the project owner deliberately.

---

## Step 4 — Refresh and Wait for Compilation

Call `refresh_unity` to trigger a domain reload.

Then poll the editor state until compilation is finished. The MCP exposes the editor's compile status as a resource — first discover the exact resource URI for your installed MCP version:

```
tool_search(query="editor state resource compiling")
```

The current Coplay MCP exposes this as a resource (typical URI format: `mcpforunity://editor-state` or `mcpforunity://editor/state` depending on version). Read the resource, check the `isCompiling` field, and re-read until it reports `false`. Do not assume a fixed wait time — different changes trigger different reload durations.

**Stall handling.** If `isCompiling` has not returned to `false` after roughly 90 seconds of polling, do not loop forever. Call `read_console(types=["error"], count=20)` — a compile failure or a modal editor dialog can leave the flag stuck — report the stall with whatever the console shows, and surface the editor state to the user rather than claiming any result.

Do not proceed to Step 5 while compilation is still in progress.

---

## Step 5 — Check the Console

Call `read_console(types=["error","warning"], count=30)`. Compare the results to any console state you observed before the edit.

**Pass criteria**: zero new compile errors. Pre-existing errors that were present before your edit do not block completion, but note them explicitly in your response if they are relevant to the task.

**Fail criteria**: any new `CS` compiler error, any `MissingReferenceException` trace from the domain reload, any error mentioning a type or method you just changed. If the project treats warnings as errors (check `manage_editor` settings or `csc.rsp` for `/warnaserror`), new warnings also fail.

If the console shows new errors, return to Step 2, fix the root cause, and repeat Steps 3–5. Do not report the task complete until this step passes.

---

## Step 6 — Report

State the outcome explicitly:

- **Files modified**: paths relative to `Assets/`.
- **Edit tool used**: `script_apply_edits` / `apply_text_edits` / `create_script` / `delete_script` / `batch_execute` (with N inner edits).
- **Hash-guarded?**: yes / no.
- **`validate_script` result**: passed for all modified files.
- **`read_console` result**: "X errors, Y warnings — no new errors introduced" or "the following new errors were found and fixed: ...".
- **Pre-existing errors**: if any prior compile error already existed before this change, list separately so it is not confused with regressions.

Never write "should compile" or "compiles cleanly" without citing an actual `read_console` result from this session.
