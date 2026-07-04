"""Read-only build pre-flight — would a build for target X succeed?

This inspects build settings and reports problems WITHOUT producing any
artifacts or mutating project state. Unlike manage_build(action=build) — which
is DESTRUCTIVE (runs build hooks, writes to disk) — validate_build is a pure
READ and is safe to run in read_only/review_only before a real build.
"""

from typing import Annotated, Any, Optional

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


@mcp_for_unity_tool(
    group="core",
    description=(
        "Read-only build pre-flight (READ; produces no artifacts, mutates nothing). "
        "Answers 'would a build for target X succeed?' before running the DESTRUCTIVE "
        "manage_build. Checks: the target resolves; its build support is installed; "
        "Build Settings scenes exist on disk; scripts compile; and PlayerSettings "
        "(productName / companyName / applicationIdentifier) are non-empty. Returns "
        "{would_build, target, checks: [{name, ok, detail}], issues: [...]}. "
        "Target defaults to the active build target when omitted."
    ),
    annotations=ToolAnnotations(
        title="Validate Build",
        readOnlyHint=True,
    ),
)
async def validate_build(
    ctx: Context,
    target: Annotated[
        Optional[str],
        "Build target to validate: windows64, osx, linux64, android, ios, webgl, "
        "uwp, tvos, visionos. Omit to validate the active build target.",
    ] = None,
) -> dict[str, Any]:
    params_dict: dict[str, Any] = {}
    if target is not None:
        params_dict["target"] = target

    unity_instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "validate_build", params_dict
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
