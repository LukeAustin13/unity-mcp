"""Play-mode smoke test: enter play, run N seconds, collect errors, exit, verdict.

Closes the agentic run-and-verify loop that run_tests does not: run_tests only
covers the test framework, not "does the game actually boot when you press Play".

Entering play mode triggers a Unity domain reload, so the C# side cannot await a
single call across play entry — it is a job (start + poll). This module exposes:

  - action="start"  -> validate preconditions and enter play mode, return job_id
  - action="status" -> poll the persisted job's state/result
  - action="run"    -> convenience composition: start, then poll with backoff
                       until done or an overall timeout, returning a compact
                       verdict. Prefer this for the common case.
"""
from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
import transport.unity_transport as unity_transport
from transport.legacy.unity_connection import async_send_command_with_retry

ALL_ACTIONS = ["run", "start", "status"]

DEFAULT_DURATION_SECONDS = 15
MAX_DURATION_SECONDS = 120
MAX_SAMPLE_ERRORS = 10


async def _send(ctx: Context, params: dict[str, Any]) -> dict[str, Any]:
    unity_instance = await get_unity_instance_from_context(ctx)
    result = await unity_transport.send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "play_smoke_test", params
    )
    return result if isinstance(result, dict) else {"success": False, "error": str(result)}


def _clamp_duration(seconds: int) -> int:
    if seconds <= 0:
        return DEFAULT_DURATION_SECONDS
    return min(seconds, MAX_DURATION_SECONDS)


@mcp_for_unity_tool(
    group="testing",
    description=(
        "Boot-check the game in play mode in ONE call. Enters play mode, runs for "
        "duration_seconds, collects console errors/exceptions raised while playing, "
        "exits play mode, and returns a pass/fail verdict. This is the run-and-verify "
        "step run_tests does NOT cover (run_tests only runs the test framework).\n"
        "Actions:\n"
        "- run (default): start + poll until done, returns {completed, error_count, "
        "exception_count, warning_count, sample_errors, verdict, reason}. Use this.\n"
        "- start: enter play mode and return a job_id immediately (advanced).\n"
        "- status: poll a job_id started by 'start' (advanced).\n"
        "verdict is 'fail' if any errors/exceptions were raised or the job failed to "
        "boot (e.g. a compile error prevented play mode)."
    ),
    annotations=ToolAnnotations(
        title="Play Smoke Test",
    ),
)
async def play_smoke_test(
    ctx: Context,
    action: Annotated[
        Literal["run", "start", "status"],
        "run (default, one-call verdict), start (return job_id), or status (poll).",
    ] = "run",
    seconds: Annotated[
        int | str,
        "How long to stay in play mode (default 15, max 120).",
    ] | None = None,
    job_id: Annotated[str, "Job id to poll (required for action='status')."] | None = None,
    timeout_seconds: Annotated[
        int,
        "For action='run': max seconds to wait for the whole run before giving up.",
    ] | None = None,
) -> dict[str, Any]:
    action_lower = action.lower() if isinstance(action, str) else action
    if action_lower not in ALL_ACTIONS:
        return {
            "success": False,
            "error": f"Unknown action '{action}'. Valid actions: {', '.join(ALL_ACTIONS)}",
        }

    try:
        duration = int(seconds) if seconds is not None else DEFAULT_DURATION_SECONDS
    except (TypeError, ValueError):
        return {"success": False, "error": "seconds must be an integer"}
    duration = _clamp_duration(duration)

    if action_lower == "start":
        return await _send(ctx, {"action": "start", "duration_seconds": duration})

    if action_lower == "status":
        if not job_id:
            return {"success": False, "error": "action='status' requires job_id"}
        return await _send(ctx, {"action": "status", "job_id": job_id})

    # action == "run": compose start -> poll status -> verdict.
    return await _run_and_verify(ctx, duration, timeout_seconds)


async def _run_and_verify(
    ctx: Context,
    duration: int,
    timeout_seconds: int | None,
) -> dict[str, Any]:
    # Overall budget: the play duration plus generous headroom for the domain
    # reload on play entry/exit and finalization.
    if timeout_seconds is None or timeout_seconds <= 0:
        overall_timeout = duration + 90
    else:
        overall_timeout = int(timeout_seconds)

    start = await _send(ctx, {"action": "start", "duration_seconds": duration})
    if not start.get("success", False):
        return {
            "success": False,
            "completed": False,
            "verdict": "fail",
            "reason": start.get("error") or start.get("message") or "failed to start",
            "error": start.get("error"),
        }

    job_id = (start.get("data") or {}).get("job_id")
    if not job_id:
        return {
            "success": False,
            "completed": False,
            "verdict": "fail",
            "reason": "start did not return a job_id",
        }

    loop = asyncio.get_event_loop()
    deadline = loop.time() + overall_timeout
    # Backoff schedule (seconds) between status polls. Play entry triggers a domain
    # reload, so early polls may transiently fail — that is tolerated below.
    delay = 1.0
    max_delay = 4.0

    last_data: dict[str, Any] = {}
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return _verdict_from(
                job_id, last_data, duration,
                completed=False,
                timed_out=True,
            )

        await asyncio.sleep(min(delay, max(0.1, remaining)))
        delay = min(max_delay, delay * 1.5)

        status = await _send(ctx, {"action": "status", "job_id": job_id})
        if not status.get("success", False):
            # During the play-entry domain reload the bridge can briefly be
            # unavailable; keep polling until the deadline instead of failing.
            continue

        last_data = status.get("data") or {}
        job_status = str(last_data.get("status", "")).lower()
        if job_status in ("done", "failed"):
            return _verdict_from(
                job_id, last_data, duration,
                completed=True,
                timed_out=False,
            )


def _verdict_from(
    job_id: str,
    data: dict[str, Any],
    duration: int,
    *,
    completed: bool,
    timed_out: bool,
) -> dict[str, Any]:
    error_count = int(data.get("error_count", 0) or 0)
    exception_count = int(data.get("exception_count", 0) or 0)
    warning_count = int(data.get("warning_count", 0) or 0)
    sample_errors = list(data.get("sample_errors") or [])[:MAX_SAMPLE_ERRORS]
    job_status = str(data.get("status", "")).lower()
    reason = data.get("reason")

    failed = (
        job_status == "failed"
        or error_count > 0
        or exception_count > 0
        or not completed
    )
    verdict = "fail" if failed else "pass"

    if reason is None:
        if timed_out:
            reason = f"timed out waiting for smoke test (job {job_id})"
        elif verdict == "fail":
            reason = "errors or exceptions were raised during play"
        else:
            reason = "no errors or exceptions during play"

    return {
        "success": True,
        "job_id": job_id,
        "completed": completed,
        "duration_seconds": duration,
        "error_count": error_count,
        "exception_count": exception_count,
        "warning_count": warning_count,
        "sample_errors": sample_errors,
        "verdict": verdict,
        "reason": reason,
    }
