"""query_scene — "SQL for the scene": filter + projection over all loaded scenes.

READ-only. One call replaces the find-object -> get-components -> get-properties
loop: apply AND-combined filters, choose which projections to include, and get
back compact rows. The heavy lifting (scene traversal, bounds, mesh stats) is
C# (QueryScene.cs); this module validates/forwards parameters and is verified by
Python plumbing tests plus Unity EditMode tests.
"""
from typing import Annotated, Any

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import coerce_bool, coerce_int
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

# Valid projections for the `include` parameter. Validated Python-side so an
# unknown projection is rejected with a clear message before hitting Unity.
VALID_INCLUDES = ("transform", "world_bounds", "components", "materials", "mesh_stats")

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 500


def _normalize_includes(value: Any) -> tuple[list[str] | None, str | None]:
    """Coerce the `include` projection list to a validated list of known names.

    Accepts a list, a comma-separated string, or a JSON array string. Returns
    (list, None) on success or (None, error) if an unknown projection is given.
    """
    if value is None:
        return None, None

    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None, None
        if s.startswith("["):
            import json
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    value = parsed
                else:
                    return None, "include must be a list of projection names."
            except (ValueError, TypeError):
                return None, f"include is not valid JSON: '{value}'."
        else:
            value = [part.strip() for part in s.split(",") if part.strip()]

    if not isinstance(value, (list, tuple)):
        return None, f"include must be a list of projection names, got {type(value).__name__}."

    cleaned: list[str] = []
    for item in value:
        name = str(item).strip().lower()
        if not name:
            continue
        if name not in VALID_INCLUDES:
            return None, (
                f"Unknown projection '{name}'. Valid projections: {', '.join(VALID_INCLUDES)}."
            )
        if name not in cleaned:
            cleaned.append(name)

    return (cleaned or None), None


@mcp_for_unity_tool(
    group="core",
    description=(
        "Query loaded scenes like a database: one call = filter + projection over every "
        "loaded scene, replacing the find-object -> get-components -> get-properties loop. "
        "READ-only.\n"
        "FILTERS (all optional, AND-combined): name_contains (case-insensitive substring), "
        "tag, layer (name or index), component_type (short or namespaced name), root_path "
        "(hierarchy path prefix scope), include_inactive (default false), scene (scene name; "
        "default all loaded). With no filters, returns everything (paged).\n"
        "PROJECTION (`include`, default ['transform']): any of transform (world "
        "pos/rotation-euler/scale + local pos), world_bounds (renderer/collider/RectTransform "
        "bounds as center+size — the 'how big is it' answer), components (component type short "
        "names), materials (sharedMaterials + shader names), mesh_stats (vertex/triangle counts). "
        "Every row always includes name, hierarchy path, instance_id, and active state.\n"
        "Paged (page_size default 50, cap 500; cursor is a flat index) in deterministic "
        "depth-first hierarchy order. Returns {total_matched, next_cursor?, rows:[...]}."
    ),
    annotations=ToolAnnotations(
        title="Query Scene",
        readOnlyHint=True,
    ),
)
async def query_scene(
    ctx: Context,
    name_contains: Annotated[
        str | None,
        "Case-insensitive substring match against GameObject name.",
    ] = None,
    tag: Annotated[str | None, "Exact tag name to match."] = None,
    layer: Annotated[
        str | int | None,
        "Layer to match, by name (e.g. 'UI') or index (0-31).",
    ] = None,
    component_type: Annotated[
        str | None,
        "Component type to match, short (e.g. 'Rigidbody') or namespaced (e.g. 'UnityEngine.Rigidbody').",
    ] = None,
    root_path: Annotated[
        str | None,
        "Restrict to objects at or under this hierarchy path prefix (e.g. 'Canvas/Panel').",
    ] = None,
    include_inactive: Annotated[
        bool | str | None,
        "Include inactive GameObjects (default false).",
    ] = None,
    scene: Annotated[
        str | None,
        "Restrict to a single loaded scene by name (default: all loaded scenes).",
    ] = None,
    include: Annotated[
        list[str] | str | None,
        "Projections to include per row (default ['transform']): any of "
        "transform, world_bounds, components, materials, mesh_stats.",
    ] = None,
    page_size: Annotated[
        int | str | None,
        "Rows per page (default 50, max 500).",
    ] = None,
    cursor: Annotated[
        int | str | None,
        "Flat index cursor from a previous page's next_cursor.",
    ] = None,
) -> dict[str, Any]:
    includes, err = _normalize_includes(include)
    if err:
        return {"success": False, "message": err}

    params: dict[str, Any] = {}

    name_contains_clean = name_contains.strip() if isinstance(name_contains, str) else None
    if name_contains_clean:
        params["name_contains"] = name_contains_clean

    tag_clean = tag.strip() if isinstance(tag, str) else None
    if tag_clean:
        params["tag"] = tag_clean

    # layer may legitimately be 0, so forward whenever a non-empty value was given.
    if layer is not None and not (isinstance(layer, str) and not layer.strip()):
        params["layer"] = layer.strip() if isinstance(layer, str) else layer

    component_type_clean = component_type.strip() if isinstance(component_type, str) else None
    if component_type_clean:
        params["component_type"] = component_type_clean

    root_path_clean = root_path.strip() if isinstance(root_path, str) else None
    if root_path_clean:
        params["root_path"] = root_path_clean

    scene_clean = scene.strip() if isinstance(scene, str) else None
    if scene_clean:
        params["scene"] = scene_clean

    include_inactive_coerced = coerce_bool(include_inactive, default=None)
    if include_inactive_coerced is not None:
        params["include_inactive"] = include_inactive_coerced

    if includes is not None:
        params["include"] = includes

    page_size_coerced = coerce_int(page_size, default=None)
    if page_size_coerced is not None:
        params["page_size"] = max(1, min(page_size_coerced, _MAX_PAGE_SIZE))

    cursor_coerced = coerce_int(cursor, default=None)
    if cursor_coerced is not None:
        params["cursor"] = max(0, cursor_coerced)

    unity_instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "query_scene", params
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
