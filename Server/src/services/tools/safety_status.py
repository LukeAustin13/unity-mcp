"""safety_status — report the server's active safety posture to the agent.

The fork's hardening gates tool calls by a configured safety mode, but an agent
could only *discover* the mode by having a call refused. This server-only tool
(and the mcpforunity://server/safety resource) lets an agent — or a skill —
read the posture up front and choose actions that will actually be allowed.

No Unity call: it reports server-side configuration only.
"""
from typing import Any

from fastmcp import Context
from mcp.types import ToolAnnotations

from core.safety import describe_safety_posture
from services.registry import mcp_for_unity_tool


@mcp_for_unity_tool(
    name="safety_status",
    unity_target=None,   # server-only; never routed to a Unity instance
    group=None,          # always visible meta-tool
    description=(
        "Report the server's active safety posture so you can pick actions that "
        "will be allowed BEFORE a call is refused. Returns: mode (read_only | "
        "review_only | write), allowed_classes (read/validate/write/destructive), "
        "whether destructive actions need confirm:true, and whether execute_code, "
        "arbitrary menu items, and external build output are enabled. In read_only "
        "only read actions run; in review_only reads plus tests/refresh/play/"
        "screenshots run; in write everything runs but destructive actions require "
        "confirm:true acknowledgement metadata (which never relaxes the mode)."
    ),
    annotations=ToolAnnotations(
        title="Safety Status",
        readOnlyHint=True,
    ),
)
async def safety_status(ctx: Context) -> dict[str, Any]:
    return {"success": True, "data": describe_safety_posture()}
