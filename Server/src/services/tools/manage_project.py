"""Project-wide health and intelligence — whole-project asset scanning.

All actions are READ-only: they inspect assets on disk and report problems, but
never modify the project. Unlike manage_scene(action=validate) — which only
checks the active scene's root objects — these scan the whole project.
"""
from typing import Annotated, Any, Literal, Optional

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import coerce_bool, parse_json_payload
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

ALL_ACTIONS = [
    "validate_assets",
    "validate_materials",
    "asset_inventory",
    "project_health",
]


async def _send_project_command(ctx: Context, params_dict: dict[str, Any]) -> dict[str, Any]:
    unity_instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "manage_project", params_dict
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}


def _coerce_scope(value: Any) -> Optional[list[str]]:
    if value is None:
        return None
    if isinstance(value, str):
        parsed = parse_json_payload(value)
        if isinstance(parsed, list):
            value = parsed
        else:
            value = [s.strip() for s in value.split(",") if s.strip()]
    if isinstance(value, list):
        cleaned = [str(v).strip() for v in value if str(v).strip()]
        return cleaned or None
    return None


def _coerce_str_list(value: Any) -> Optional[list[str]]:
    return _coerce_scope(value)


@mcp_for_unity_tool(
    group="core",
    description=(
        "Whole-project health and intelligence (READ-only; scans assets on disk). "
        "Use this to learn what is wrong with a project without guessing. Actions:\n"
        "- validate_assets: find missing scripts, broken prefab instances, and dangling "
        "serialized object references across every prefab/scene/ScriptableObject.\n"
        "- validate_materials: find materials with a missing shader, an error ('pink') "
        "shader, or an unsupported/errored shader.\n"
        "- asset_inventory: counts of assets rolled up by type (optionally with GUIDs).\n"
        "- project_health: one aggregate report of asset + material health with a "
        "per-category 'healthy' flag (bounded scan). For console errors use read_console; "
        "for test health use run_tests_and_summarize.\n"
        "Large scans are paged (page_size + cursor -> next_cursor) and capped (max_issues). "
        "Defaults to scanning 'Assets' only; set include_packages=true to include Packages/."
    ),
    annotations=ToolAnnotations(
        title="Manage Project",
        readOnlyHint=True,
    ),
)
async def manage_project(
    ctx: Context,
    action: Annotated[
        Literal["validate_assets", "validate_materials", "asset_inventory", "project_health"],
        "Read-only project scan to perform.",
    ],
    folder_scope: Annotated[
        str | list[str] | None,
        "Folder(s) to scan, e.g. 'Assets/Prefabs' or a JSON/comma list. Default: ['Assets'].",
    ] = None,
    include_packages: Annotated[
        bool | str | None,
        "Include read-only Packages/ assets in the scan (default false).",
    ] = None,
    page_size: Annotated[int | str | None, "Items per page for paged scans."] = None,
    cursor: Annotated[str | None, "Opaque cursor from a previous page's next_cursor."] = None,
    max_issues: Annotated[int | str | None, "Cap on issues returned per category."] = None,
    issue_types: Annotated[
        list[str] | str | None,
        "Filter for validate_assets: any of missing_scripts, missing_prefab, dangling_reference.",
    ] = None,
    include_guids: Annotated[
        bool | str | None,
        "For asset_inventory: include the GUID list per type (default false).",
    ] = None,
) -> dict[str, Any]:
    action_lower = action.lower() if isinstance(action, str) else action
    if action_lower not in ALL_ACTIONS:
        return {
            "success": False,
            "message": f"Unknown action '{action}'. Valid actions: {', '.join(ALL_ACTIONS)}",
        }

    params_dict: dict[str, Any] = {"action": action_lower}

    param_map: dict[str, Any] = {
        "folder_scope": _coerce_scope(folder_scope),
        "include_packages": coerce_bool(include_packages, default=None),
        "page_size": page_size,
        "cursor": cursor,
        "max_issues": max_issues,
        "issue_types": _coerce_str_list(issue_types),
        "include_guids": coerce_bool(include_guids, default=None),
    }
    for key, val in param_map.items():
        if val is not None:
            params_dict[key] = val

    return await _send_project_command(ctx, params_dict)
