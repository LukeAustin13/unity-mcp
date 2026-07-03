"""server_safety resource — the active safety posture as read-only state.

URI: mcpforunity://server/safety

Mirrors the safety_status tool (both call core.safety.describe_safety_posture),
exposed as a resource so agents that read state via resources can see the mode
without making a tool call. No Unity call.
"""
from typing import Any

from fastmcp import Context

from core.safety import describe_safety_posture
from services.registry import mcp_for_unity_resource


@mcp_for_unity_resource(
    uri="mcpforunity://server/safety",
    name="server_safety",
    description=(
        "Active safety mode, allowed action classes, execution-surface flags "
        "(execute_code / arbitrary menu items / external build output), the menu "
        "allowlist, and audit-log status. Read this to know what you may do.\n\n"
        "URI: mcpforunity://server/safety"
    ),
)
async def get_server_safety(ctx: Context) -> dict[str, Any]:
    return describe_safety_posture()
