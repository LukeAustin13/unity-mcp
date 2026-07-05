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
    "get_dependencies",
    "find_references",
    "unused_assets",
    "audit_mobile",
    "prefab_health",
]

# Actions that require a non-empty target (asset path or GUID).
_TARGET_REQUIRED_ACTIONS = {"get_dependencies", "find_references"}


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
        "- get_dependencies: 'what does this asset use?' — the assets a target (path or "
        "GUID) depends on; set recursive=true to walk transitively.\n"
        "- find_references: 'what breaks if I touch this?' — the assets under folder_scope "
        "that directly depend on the target (path or GUID) (reverse lookup; capped scan).\n"
        "- unused_assets: ADVISORY list of assets under folder_scope not reachable from any "
        "root (build scenes, Resources/, StreamingAssets). Cannot see Addressables/"
        "AssetBundles or reflection loads — read the returned 'caveats'. Never deletes.\n"
        "- audit_mobile: ADVISORY mobile-performance heuristic scan (static rules, not a "
        "profiler). Flags texture/audio/model import settings, Android/iOS PlayerSettings and "
        "QualitySettings, and the active scene's realtime lights/cameras. Returns "
        "findings[{rule_id, category, severity, asset_path?, message, fix}] + a summary; "
        "read the returned 'caveats'. Never modifies anything.\n"
        "- prefab_health: deep READ-only validation of prefab assets on disk (never opens/"
        "instantiates them). Pass prefab_path for one prefab (also returns its direct "
        "dependencies) or folder to scan every .prefab under it. Checks missing scripts, "
        "dangling references, broken variants, duplicate components, invalid tags, unnamed "
        "layers, material/shader issues, disabled colliders/renderers, deep nesting and huge "
        "bounds. Returns findings[{check, severity, advisory, prefab_path, object_path, reason, "
        "suggested_fix}] + summary + rule_errors. Set include_variants=false to skip variants.\n"
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
        Literal[
            "validate_assets",
            "validate_materials",
            "asset_inventory",
            "project_health",
            "get_dependencies",
            "find_references",
            "unused_assets",
            "audit_mobile",
            "prefab_health",
        ],
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
    target: Annotated[
        str | None,
        "For get_dependencies/find_references: the asset path or GUID to inspect.",
    ] = None,
    recursive: Annotated[
        bool | str | None,
        "For get_dependencies: walk transitive dependencies (default false).",
    ] = None,
    prefab_path: Annotated[
        str | None,
        "For prefab_health: a single prefab to inspect (project-relative under Assets/). "
        "Takes precedence over 'folder'.",
    ] = None,
    folder: Annotated[
        str | None,
        "For prefab_health: folder to scan all .prefab under (default 'Assets').",
    ] = None,
    include_variants: Annotated[
        bool | str | None,
        "For prefab_health: include prefab variants (default true; false skips them).",
    ] = None,
) -> dict[str, Any]:
    action_lower = action.lower() if isinstance(action, str) else action
    if action_lower not in ALL_ACTIONS:
        return {
            "success": False,
            "message": f"Unknown action '{action}'. Valid actions: {', '.join(ALL_ACTIONS)}",
        }

    target_clean = target.strip() if isinstance(target, str) else target
    if action_lower in _TARGET_REQUIRED_ACTIONS and not target_clean:
        return {
            "success": False,
            "message": f"Action '{action_lower}' requires a non-empty 'target' (asset path or GUID).",
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
        "target": target_clean,
        "recursive": coerce_bool(recursive, default=None),
        "prefab_path": prefab_path.strip() if isinstance(prefab_path, str) else prefab_path,
        "folder": folder.strip() if isinstance(folder, str) else folder,
        "include_variants": coerce_bool(include_variants, default=None),
    }
    for key, val in param_map.items():
        if val is not None:
            params_dict[key] = val

    return await _send_project_command(ctx, params_dict)
