"""Async Unity Test Runner jobs: start + poll."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from models import MCPResponse
from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.preflight import preflight
import transport.unity_transport as unity_transport
from transport.legacy.unity_connection import async_send_command_with_retry
from transport.plugin_hub import PluginHub
from utils.focus_nudge import nudge_unity_focus, should_nudge, reset_nudge_backoff

logger = logging.getLogger(__name__)

# Strong references to background fire-and-forget tasks to prevent premature GC.
_background_tasks: set[asyncio.Task] = set()


async def _get_unity_project_path(unity_instance: str | None) -> str | None:
    """Get the project root path for a Unity instance (for focus nudging).

    Args:
        unity_instance: Unity instance hash or "Name@hash" format or None

    Returns:
        Project root path (e.g., "/Users/name/project"), or falls back to project_name if path unavailable
    """
    if not unity_instance:
        return None

    try:
        registry = PluginHub._registry
        if not registry:
            return None

        # Parse Name@hash format if present (middleware stores instances as "Name@hash")
        target_hash = unity_instance
        if "@" in target_hash:
            _, _, target_hash = target_hash.rpartition("@")
        if not target_hash:
            return None

        # Get session by hash
        session_id = await registry.get_session_id_by_hash(target_hash)
        if not session_id:
            return None

        session = await registry.get_session(session_id)
        if not session:
            return None

    except Exception as e:
        # Re-raise cancellation errors so task cancellation propagates
        if isinstance(e, asyncio.CancelledError):
            raise
        logger.debug(f"Could not get Unity project path: {e}")
        return None
    else:
        # Return full path if available, otherwise fall back to project name
        if session.project_path:
            return session.project_path
        return session.project_name if session.project_name else None


class RunTestsSummary(BaseModel):
    total: int
    passed: int
    failed: int
    skipped: int
    durationSeconds: float
    resultState: str


class RunTestsTestResult(BaseModel):
    name: str
    fullName: str
    state: str
    durationSeconds: float
    message: str | None = None
    stackTrace: str | None = None
    output: str | None = None


class RunTestsResult(BaseModel):
    mode: str
    summary: RunTestsSummary
    results: list[RunTestsTestResult] | None = None


class RunTestsStartData(BaseModel):
    job_id: str
    status: str
    mode: str | None = None
    include_details: bool | None = None
    include_failed_tests: bool | None = None


class RunTestsStartResponse(MCPResponse):
    data: RunTestsStartData | None = None


class TestJobFailure(BaseModel):
    full_name: str | None = None
    message: str | None = None


class TestJobProgress(BaseModel):
    completed: int | None = None
    total: int | None = None
    current_test_full_name: str | None = None
    current_test_started_unix_ms: int | None = None
    last_finished_test_full_name: str | None = None
    last_finished_unix_ms: int | None = None
    stuck_suspected: bool | None = None
    editor_is_focused: bool | None = None
    blocked_reason: str | None = None
    failures_so_far: list[TestJobFailure] | None = None
    failures_capped: bool | None = None


class GetTestJobData(BaseModel):
    job_id: str
    status: str
    mode: str | None = None
    started_unix_ms: int | None = None
    finished_unix_ms: int | None = None
    last_update_unix_ms: int | None = None
    progress: TestJobProgress | None = None
    error: str | None = None
    result: RunTestsResult | None = None


class GetTestJobResponse(MCPResponse):
    data: GetTestJobData | None = None


@mcp_for_unity_tool(
    group="testing",
    description="Starts a Unity test run asynchronously and returns a job_id immediately. Poll with get_test_job for progress.",
    annotations=ToolAnnotations(
        title="Run Tests",
        destructiveHint=True,
    ),
)
async def run_tests(
    ctx: Context,
    mode: Annotated[Literal["EditMode", "PlayMode"],
                    "Unity test mode to run"] = "EditMode",
    test_names: Annotated[list[str] | str,
                          "Full names of specific tests to run"] | None = None,
    group_names: Annotated[list[str] | str,
                           "Same as test_names, except it allows for Regex"] | None = None,
    category_names: Annotated[list[str] | str,
                              "NUnit category names to filter by"] | None = None,
    assembly_names: Annotated[list[str] | str,
                              "Assembly names to filter tests by"] | None = None,
    include_failed_tests: Annotated[bool,
                                    "Include details for failed/skipped tests only (default: false)"] = False,
    include_details: Annotated[bool,
                               "Include details for all tests (default: false)"] = False,
    init_timeout: Annotated[int | None,
                            "Initialization timeout in milliseconds. PlayMode tests may need longer "
                            "due to domain reload (default: 15000). Recommended: 120000 for PlayMode."] = None,
) -> RunTestsStartResponse | MCPResponse:
    if init_timeout is not None and init_timeout <= 0:
        return MCPResponse(success=False, error="init_timeout must be a positive integer (milliseconds) or None")

    unity_instance = await get_unity_instance_from_context(ctx)

    gate = await preflight(ctx, requires_no_tests=True, wait_for_no_compile=True, refresh_if_dirty=True)
    if isinstance(gate, MCPResponse):
        return gate

    def _coerce_string_list(value) -> list[str] | None:
        if value is None:
            return None
        if isinstance(value, str):
            return [value] if value.strip() else None
        if isinstance(value, list):
            result = [str(v).strip() for v in value if v and str(v).strip()]
            return result if result else None
        return None

    params: dict[str, Any] = {"mode": mode}
    if (t := _coerce_string_list(test_names)):
        params["testNames"] = t
    if (g := _coerce_string_list(group_names)):
        params["groupNames"] = g
    if (c := _coerce_string_list(category_names)):
        params["categoryNames"] = c
    if (a := _coerce_string_list(assembly_names)):
        params["assemblyNames"] = a
    if include_failed_tests:
        params["includeFailedTests"] = True
    if include_details:
        params["includeDetails"] = True
    if init_timeout is not None and init_timeout > 0:
        params["initTimeout"] = init_timeout

    response = await unity_transport.send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "run_tests",
        params,
    )

    if isinstance(response, dict):
        if not response.get("success", True):
            return MCPResponse(**response)
        return RunTestsStartResponse(**response)
    return MCPResponse(success=False, error=str(response))


@mcp_for_unity_tool(
    group="testing",
    description="Polls an async Unity test job by job_id.",
    annotations=ToolAnnotations(
        title="Get Test Job",
        readOnlyHint=True,
    ),
)
async def get_test_job(
    ctx: Context,
    job_id: Annotated[str, "Job id returned by run_tests"],
    include_failed_tests: Annotated[bool,
                                    "Include details for failed/skipped tests only (default: false)"] = False,
    include_details: Annotated[bool,
                               "Include details for all tests (default: false)"] = False,
    wait_timeout: Annotated[int | None,
                            "If set, wait up to this many seconds for tests to complete before returning. "
                            "Reduces polling frequency and avoids client-side loop detection. "
                            "Recommended: 30-60 seconds. Returns immediately if tests complete sooner."] = None,
) -> GetTestJobResponse | MCPResponse:
    unity_instance = await get_unity_instance_from_context(ctx)

    params: dict[str, Any] = {"job_id": job_id}
    if include_failed_tests:
        params["includeFailedTests"] = True
    if include_details:
        params["includeDetails"] = True

    async def _fetch_status() -> dict[str, Any]:
        return await unity_transport.send_with_unity_instance(
            async_send_command_with_retry,
            unity_instance,
            "get_test_job",
            params,
        )

    # If wait_timeout is specified, poll server-side until complete or timeout
    if wait_timeout and wait_timeout > 0:
        deadline = asyncio.get_event_loop().time() + wait_timeout
        poll_interval = 2.0  # Poll Unity every 2 seconds
        prev_last_update_unix_ms = None

        # Get project path once for focus nudging (multi-instance support)
        project_path = await _get_unity_project_path(unity_instance)

        while True:
            response = await _fetch_status()

            if not isinstance(response, dict):
                return MCPResponse(success=False, error=str(response))

            if not response.get("success", True):
                return MCPResponse(**response)

            # Check if tests are done
            data = response.get("data", {})
            status = data.get("status", "")
            if status in ("succeeded", "failed", "cancelled"):
                return GetTestJobResponse(**response)

            # Detect progress and reset exponential backoff
            last_update_unix_ms = data.get("last_update_unix_ms")
            if prev_last_update_unix_ms is not None and last_update_unix_ms != prev_last_update_unix_ms:
                # Progress detected - reset exponential backoff for next potential stall
                reset_nudge_backoff()
                logger.debug(f"Test job {job_id} made progress - reset nudge backoff")
            prev_last_update_unix_ms = last_update_unix_ms

            # Check if Unity needs a focus nudge to make progress
            # This handles OS-level throttling (e.g., macOS App Nap) that can
            # stall PlayMode tests when Unity is in the background.
            # Uses exponential backoff: 1s, 2s, 4s, 8s, 10s max between nudges.
            progress = data.get("progress") or {}
            editor_is_focused = progress.get("editor_is_focused", True)
            current_time_ms = int(time.time() * 1000)

            if should_nudge(
                status=status,
                editor_is_focused=editor_is_focused,
                last_update_unix_ms=last_update_unix_ms,
                current_time_ms=current_time_ms,
                # Use default stall_threshold_ms (3s)
            ):
                logger.info(f"Test job {job_id} appears stalled (unfocused Unity), attempting nudge...")
                # Lazily resolve project path if not yet available (registry may have become ready)
                if project_path is None:
                    project_path = await _get_unity_project_path(unity_instance)
                # Pass project path for multi-instance support
                nudged = await nudge_unity_focus(unity_project_path=project_path)
                if nudged:
                    logger.info(f"Test job {job_id} nudge completed")

            # Check timeout
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                # Timeout reached, return current status
                return GetTestJobResponse(**response)

            # Wait before next poll (but don't exceed remaining time)
            await asyncio.sleep(min(poll_interval, remaining))
    
    # No wait_timeout - return immediately (original behavior)
    response = await _fetch_status()
    if not isinstance(response, dict):
        return MCPResponse(success=False, error=str(response))
    if not response.get("success", True):
        return MCPResponse(**response)

    # Fire-and-forget nudge check: even without wait_timeout, clients may poll
    # externally. Check if Unity needs a nudge on every call so stalls get
    # detected regardless of polling style.
    data = response.get("data", {})
    status = data.get("status", "")
    if status == "running":
        progress = data.get("progress") or {}
        editor_is_focused = progress.get("editor_is_focused", True)
        last_update_unix_ms = data.get("last_update_unix_ms")
        current_time_ms = int(time.time() * 1000)
        if should_nudge(
            status=status,
            editor_is_focused=editor_is_focused,
            last_update_unix_ms=last_update_unix_ms,
            current_time_ms=current_time_ms,
        ):
            logger.info(f"Test job {job_id} appears stalled (unfocused Unity), scheduling background nudge...")
            project_path = await _get_unity_project_path(unity_instance)
            task = asyncio.create_task(nudge_unity_focus(unity_project_path=project_path))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    return GetTestJobResponse(**response)


# --- test-failure triage helpers ---------------------------------------------
#
# Both features below are opt-in and never change the default output: they only
# add extra keys when their parameters are enabled.

# Match the file/line in a stack-trace frame. Handles the two common shapes:
#   "  at Ns.Type.Method () [0x0] in C:\proj\Foo.cs:42"   (Mono/Unity)
#   "  in /home/u/proj/Foo.cs:line 42"                    (POSIX "in <path>:line NN")
# The path capture stops at ":<digits>" or ":line <digits>".
_STACK_FRAME_RE = re.compile(
    r"(?:\bin\s+|\bat\s+.*?\bin\s+)(?P<path>.+?):(?:line\s+)?(?P<line>\d+)\b",
    re.IGNORECASE,
)

# Max failing tests to enrich with source context (bounds file I/O).
_MAX_SOURCE_CONTEXT = 10


def _parse_stack_location(stack_trace: str | None) -> tuple[str, int] | None:
    """Return the first (path, line) parsed from a stack trace, or None.

    Scans frames top-to-bottom and returns the first frame whose captured path
    exists on the local filesystem — that is the frame the caller can actually
    show. If no captured path exists locally (HTTP-remote case), returns the
    first syntactically-parsed frame so callers can still see where it failed.
    """
    if not stack_trace:
        return None
    first_parsed: tuple[str, int] | None = None
    for m in _STACK_FRAME_RE.finditer(stack_trace):
        path = m.group("path").strip().strip('"')
        try:
            line = int(m.group("line"))
        except (TypeError, ValueError):
            continue
        if line <= 0:
            continue
        if first_parsed is None:
            first_parsed = (path, line)
        if os.path.isfile(path):
            return (path, line)
    return first_parsed


def _extract_source_context(path: str, line: int, radius: int = 5) -> dict[str, Any] | None:
    """Read ±radius lines around ``line`` from ``path``. Returns None when the
    file is not locally readable (e.g. the server runs remote from the tests)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    if not lines:
        return None
    total = len(lines)
    if line < 1 or line > total:
        return None
    start = max(1, line - radius)
    end = min(total, line + radius)
    rendered = []
    for n in range(start, end):
        text = lines[n - 1].rstrip("\n")
        marker = ">" if n == line else " "
        rendered.append(f"{marker} {n}: {text}")
    return {
        "file": path,
        "line": line,
        "start_line": start,
        "end_line": end,
        "lines": rendered,
    }


def _attach_source_context(failing_results: list[Any], cap: int = _MAX_SOURCE_CONTEXT) -> None:
    """Mutate up to ``cap`` entries in ``failing_results`` (dicts with a matching
    RunTestsTestResult) to add a ``source_context`` key when locally readable."""
    enriched = 0
    for out_entry, src in failing_results:
        if enriched >= cap:
            break
        loc = _parse_stack_location(getattr(src, "stackTrace", None))
        if loc is None:
            continue
        ctx = _extract_source_context(loc[0], loc[1])
        if ctx is not None:
            out_entry["source_context"] = ctx
            enriched += 1


async def _rerun_and_classify(
    ctx: Context,
    mode: str,
    failed_full_names: list[str],
    retries: int,
    wait: int,
) -> tuple[list[str], list[str]] | None:
    """Re-run only ``failed_full_names`` up to ``retries`` times. A test that
    passes on any retry is flaky; one that fails every retry is deterministic.

    Returns (flaky, deterministic) sorted lists, or None if a retry run could
    not be started/completed (caller then omits the classification)."""
    still_failing = set(failed_full_names)
    passed_at_least_once: set[str] = set()

    for _ in range(retries):
        if not still_failing:
            break
        start = await run_tests(
            ctx,
            mode=mode,
            test_names=sorted(still_failing),
            include_failed_tests=True,
        )
        if not getattr(start, "success", False):
            return None
        start_data = getattr(start, "data", None)
        job_id = getattr(start_data, "job_id", None) if start_data else None
        if not job_id:
            return None
        job = await get_test_job(ctx, job_id, include_failed_tests=True, wait_timeout=wait)
        if not getattr(job, "success", False):
            return None
        jd = getattr(job, "data", None)
        status = getattr(jd, "status", "unknown") if jd else "unknown"
        if status not in ("succeeded", "failed", "cancelled"):
            # Retry run did not finish in time — cannot classify reliably.
            return None
        result = getattr(jd, "result", None) if jd else None
        this_run_failed = {
            r.fullName
            for r in (getattr(result, "results", None) or [])
            if str(getattr(r, "state", "")).lower().startswith("fail")
        }
        # Any originally-failing test not in this run's failures passed this time.
        newly_passed = {n for n in still_failing if n not in this_run_failed}
        passed_at_least_once |= newly_passed
        still_failing -= newly_passed

    flaky = sorted(passed_at_least_once)
    deterministic = sorted(still_failing)
    return flaky, deterministic


@mcp_for_unity_tool(
    group="testing",
    description=(
        "Run Unity tests and return a compact pass/fail summary in ONE call. "
        "Starts the run, waits server-side up to timeout_seconds, and returns "
        "counts plus a capped list of failing tests — instead of start + poll + "
        "poll + parse. If the run is still going when timeout_seconds elapses, "
        "returns timed_out=true with progress; poll get_test_job for the final "
        "result. Prefer this over run_tests+get_test_job for the common case. "
        "Optional triage: include_source_context attaches ±5 source lines around "
        "each failing test's stack frame (local runs only); retry_failed re-runs "
        "only the failed tests to split flaky from deterministic failures."
    ),
    annotations=ToolAnnotations(
        title="Run Tests and Summarize",
    ),
)
async def run_tests_and_summarize(
    ctx: Context,
    mode: Annotated[Literal["EditMode", "PlayMode"], "Unity test mode to run"] = "EditMode",
    test_names: Annotated[list[str] | str, "Full names of specific tests to run"] | None = None,
    group_names: Annotated[list[str] | str, "Regex-capable test name filters"] | None = None,
    category_names: Annotated[list[str] | str, "NUnit category names to filter by"] | None = None,
    assembly_names: Annotated[list[str] | str, "Assembly names to filter tests by"] | None = None,
    timeout_seconds: Annotated[int, "Max seconds to wait for completion (default 180)"] = 180,
    max_failures: Annotated[int, "Max failing tests to list (default 20)"] = 20,
    include_source_context: Annotated[
        bool,
        "For each failing test, parse its stack trace and attach ±5 source lines "
        "when the file is locally readable (stdio/local only). Capped at 10 failures. "
        "Silently skipped for files not on this machine (default false).",
    ] = False,
    retry_failed: Annotated[
        int,
        "After a run with failures, re-run ONLY the failed tests up to N times "
        "(cap 3) and classify each as 'flaky' (passed on a retry) or "
        "'deterministic' (failed every retry). 0 disables retries (default 0).",
    ] = 0,
) -> dict[str, Any]:
    start = await run_tests(
        ctx,
        mode=mode,
        test_names=test_names,
        group_names=group_names,
        category_names=category_names,
        assembly_names=assembly_names,
        include_failed_tests=True,
    )
    if not getattr(start, "success", False):
        return {
            "success": False,
            "error": getattr(start, "error", None) or "run_tests failed to start",
            "message": getattr(start, "message", None),
            "hint": getattr(start, "hint", None),
        }

    start_data = getattr(start, "data", None)
    job_id = getattr(start_data, "job_id", None) if start_data else None
    if not job_id:
        return {"success": False, "error": "run_tests did not return a job_id"}

    wait = max(1, int(timeout_seconds))
    job = await get_test_job(ctx, job_id, include_failed_tests=True, wait_timeout=wait)
    if not getattr(job, "success", False):
        return {
            "success": False,
            "error": getattr(job, "error", None) or "get_test_job failed",
            "job_id": job_id,
        }

    jd = getattr(job, "data", None)
    status = getattr(jd, "status", "unknown") if jd else "unknown"
    completed = status in ("succeeded", "failed", "cancelled")
    cap = max(0, int(max_failures))

    out: dict[str, Any] = {
        "success": True,
        "job_id": job_id,
        "mode": mode,
        "status": status,
        "completed": completed,
    }

    result = getattr(jd, "result", None) if jd else None
    result_summary = getattr(result, "summary", None) if result else None
    if result_summary is not None:
        out["summary"] = {
            "total": result_summary.total,
            "passed": result_summary.passed,
            "failed": result_summary.failed,
            "skipped": result_summary.skipped,
            "duration_seconds": result_summary.durationSeconds,
            "result_state": result_summary.resultState,
        }
        failing = [
            r for r in (result.results or [])
            if str(getattr(r, "state", "")).lower().startswith("fail")
        ]
        listed = failing[:cap]
        failing_entries = [
            {"full_name": r.fullName, "message": (r.message or "").strip()[:500]}
            for r in listed
        ]
        # Opt-in: enrich the listed failures with local source context. Zips each
        # output dict with its source RunTestsTestResult (for the stack trace).
        if include_source_context and failing_entries:
            _attach_source_context(list(zip(failing_entries, listed)))
        out["failing_tests"] = failing_entries
        out["failing_tests_truncated"] = len(failing) > cap
        out["all_passed"] = completed and result_summary.failed == 0

        # Opt-in: re-run the failing tests to separate flaky from deterministic.
        retries = max(0, min(3, int(retry_failed)))
        if retries and completed and failing:
            failed_names = [r.fullName for r in failing if r.fullName]
            classified = await _rerun_and_classify(
                ctx, mode, failed_names, retries, wait,
            )
            if classified is not None:
                flaky, deterministic = classified
                out["summary"]["flaky_tests"] = flaky
                out["summary"]["deterministic_failures"] = deterministic
    elif not completed:
        out["timed_out"] = True
        out["message"] = (
            f"Tests still running after {wait}s; poll get_test_job('{job_id}') "
            "for the final result."
        )
        prog = getattr(jd, "progress", None) if jd else None
        if prog is not None:
            out["progress"] = {
                "completed": getattr(prog, "completed", None),
                "total": getattr(prog, "total", None),
                "failures_so_far": [
                    {"full_name": f.full_name, "message": (f.message or "")[:300]}
                    for f in (getattr(prog, "failures_so_far", None) or [])[:cap]
                ],
            }

    return out
