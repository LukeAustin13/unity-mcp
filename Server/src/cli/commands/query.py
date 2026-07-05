"""Scene query CLI commands — SQL-for-the-scene filter + projection (read-only)."""

import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.connection import run_command, handle_unity_errors


@click.group()
def query():
    """Query loaded scenes - filter + projection over all loaded scenes (read-only)."""
    pass


@query.command("scene")
@click.option("--name-contains", help="Case-insensitive substring match on GameObject name")
@click.option("--tag", help="Exact tag name to match")
@click.option("--layer", help="Layer name or index (0-31) to match")
@click.option("--component", "component_type", help="Component type, short or namespaced")
@click.option("--root-path", help="Restrict to objects at/under this hierarchy path prefix")
@click.option("--scene", "scene_name", help="Restrict to a single loaded scene by name")
@click.option("--include-inactive", is_flag=True, help="Include inactive GameObjects")
@click.option(
    "--include",
    help="Projections, comma-separated: transform,world_bounds,components,materials,mesh_stats",
)
@click.option("--page-size", type=int, help="Rows per page (default 50, max 500)")
@click.option("--cursor", type=int, help="Flat index cursor from a previous page's next_cursor")
@handle_unity_errors
def scene(
    name_contains,
    tag,
    layer,
    component_type,
    root_path,
    scene_name,
    include_inactive,
    include,
    page_size,
    cursor,
):
    """Query loaded scenes: filter objects and project the fields you want in one call."""
    config = get_config()
    params = {}
    if name_contains:
        params["name_contains"] = name_contains
    if tag:
        params["tag"] = tag
    if layer:
        params["layer"] = layer
    if component_type:
        params["component_type"] = component_type
    if root_path:
        params["root_path"] = root_path
    if scene_name:
        params["scene"] = scene_name
    if include_inactive:
        params["include_inactive"] = True
    if include:
        params["include"] = [s.strip() for s in include.split(",") if s.strip()]
    if page_size is not None:
        params["page_size"] = page_size
    if cursor is not None:
        params["cursor"] = cursor
    click.echo(format_output(run_command("query_scene", params, config), config.format))
