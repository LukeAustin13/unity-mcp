"""Project intelligence CLI commands — whole-project health scans (read-only)."""

import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.connection import run_command, handle_unity_errors


def _scope_param(params, folder):
    if folder:
        params["folder_scope"] = [f.strip() for f in folder.split(",") if f.strip()]


@click.group()
def project():
    """Project intelligence - whole-project health scans (read-only)."""
    pass


@project.command("health")
@click.option("--folder", "-f", help="Folder(s) to scan, comma-separated (default: Assets)")
@click.option("--include-packages", is_flag=True, help="Include Packages/ in the scan")
@handle_unity_errors
def health(folder, include_packages):
    """Aggregate asset + material health report."""
    config = get_config()
    params = {"action": "project_health"}
    _scope_param(params, folder)
    if include_packages:
        params["include_packages"] = True
    click.echo(format_output(run_command("manage_project", params, config), config.format))


@project.command("validate-assets")
@click.option("--folder", "-f", help="Folder(s) to scan, comma-separated (default: Assets)")
@click.option("--include-packages", is_flag=True, help="Include Packages/ in the scan")
@click.option("--page-size", type=int, help="Assets to scan per page")
@click.option("--cursor", help="Cursor from a previous page's next_cursor")
@click.option("--max-issues", type=int, help="Cap on issues returned")
@handle_unity_errors
def validate_assets(folder, include_packages, page_size, cursor, max_issues):
    """Scan prefabs/ScriptableObjects for missing scripts, broken prefabs, dangling refs."""
    config = get_config()
    params = {"action": "validate_assets"}
    _scope_param(params, folder)
    if include_packages:
        params["include_packages"] = True
    if page_size is not None:
        params["page_size"] = page_size
    if cursor:
        params["cursor"] = cursor
    if max_issues is not None:
        params["max_issues"] = max_issues
    click.echo(format_output(run_command("manage_project", params, config), config.format))


@project.command("validate-materials")
@click.option("--folder", "-f", help="Folder(s) to scan, comma-separated (default: Assets)")
@click.option("--include-packages", is_flag=True, help="Include Packages/ in the scan")
@click.option("--page-size", type=int, help="Materials to scan per page")
@click.option("--cursor", help="Cursor from a previous page's next_cursor")
@handle_unity_errors
def validate_materials(folder, include_packages, page_size, cursor):
    """Scan materials for missing/error/unsupported shaders (pink materials)."""
    config = get_config()
    params = {"action": "validate_materials"}
    _scope_param(params, folder)
    if include_packages:
        params["include_packages"] = True
    if page_size is not None:
        params["page_size"] = page_size
    if cursor:
        params["cursor"] = cursor
    click.echo(format_output(run_command("manage_project", params, config), config.format))


@project.command("inventory")
@click.option("--folder", "-f", help="Folder(s) to scan, comma-separated (default: Assets)")
@click.option("--include-packages", is_flag=True, help="Include Packages/ in the scan")
@click.option("--guids", "include_guids", is_flag=True, help="Include GUID lists per type")
@handle_unity_errors
def inventory(folder, include_packages, include_guids):
    """Asset counts rolled up by type."""
    config = get_config()
    params = {"action": "asset_inventory"}
    _scope_param(params, folder)
    if include_packages:
        params["include_packages"] = True
    if include_guids:
        params["include_guids"] = True
    click.echo(format_output(run_command("manage_project", params, config), config.format))
