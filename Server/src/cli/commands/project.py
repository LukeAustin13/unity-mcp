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


@project.command("deps")
@click.argument("target")
@click.option("--recursive", "-r", is_flag=True, help="Walk transitive dependencies")
@click.option("--page-size", type=int, help="Dependencies per page")
@click.option("--cursor", help="Cursor from a previous page's next_cursor")
@handle_unity_errors
def deps(target, recursive, page_size, cursor):
    """What does TARGET (asset path or GUID) depend on?"""
    config = get_config()
    params = {"action": "get_dependencies", "target": target}
    if recursive:
        params["recursive"] = True
    if page_size is not None:
        params["page_size"] = page_size
    if cursor:
        params["cursor"] = cursor
    click.echo(format_output(run_command("manage_project", params, config), config.format))


@project.command("refs")
@click.argument("target")
@click.option("--folder", "-f", help="Folder(s) to scan, comma-separated (default: Assets)")
@click.option("--include-packages", is_flag=True, help="Include Packages/ in the scan")
@click.option("--page-size", type=int, help="Referencing assets per page")
@click.option("--cursor", help="Cursor from a previous page's next_cursor")
@click.option("--max-issues", type=int, help="Cap on candidate assets scanned")
@handle_unity_errors
def refs(target, folder, include_packages, page_size, cursor, max_issues):
    """What breaks if I touch TARGET? — assets that directly depend on it."""
    config = get_config()
    params = {"action": "find_references", "target": target}
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


@project.command("unused")
@click.option("--folder", "-f", help="Folder(s) to scan, comma-separated (default: Assets)")
@click.option("--include-packages", is_flag=True, help="Include Packages/ in the scan")
@click.option("--page-size", type=int, help="Unused candidates per page")
@click.option("--cursor", help="Cursor from a previous page's next_cursor")
@click.option("--max-issues", type=int, help="Cap on candidate assets scanned")
@handle_unity_errors
def unused(folder, include_packages, page_size, cursor, max_issues):
    """Advisory list of assets not reachable from any root (never deletes)."""
    config = get_config()
    params = {"action": "unused_assets"}
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


@project.command("prefab-health")
@click.option("--prefab-path", "-p", help="Single prefab to inspect (takes precedence over --folder)")
@click.option("--folder", "-f", help="Folder to scan all .prefab under (default: Assets)")
@click.option("--no-variants", "no_variants", is_flag=True, help="Skip prefab variants")
@click.option("--page-size", type=int, help="Prefabs to scan per page")
@click.option("--cursor", help="Cursor from a previous page's next_cursor")
@click.option("--max-issues", type=int, help="Cap on findings returned")
@handle_unity_errors
def prefab_health(prefab_path, folder, no_variants, page_size, cursor, max_issues):
    """Deep read-only prefab validation (never opens or modifies prefabs)."""
    config = get_config()
    params = {"action": "prefab_health"}
    if prefab_path:
        params["prefab_path"] = prefab_path
    if folder:
        params["folder"] = folder
    if no_variants:
        params["include_variants"] = False
    if page_size is not None:
        params["page_size"] = page_size
    if cursor:
        params["cursor"] = cursor
    if max_issues is not None:
        params["max_issues"] = max_issues
    click.echo(format_output(run_command("manage_project", params, config), config.format))


@project.command("audit-mobile")
@click.option("--folder", "-f", help="Folder(s) to scan, comma-separated (default: Assets)")
@click.option("--include-packages", is_flag=True, help="Include Packages/ in the scan")
@click.option("--page-size", type=int, help="Asset findings per page")
@click.option("--cursor", help="Cursor from a previous page's next_cursor")
@click.option("--max-issues", type=int, help="Cap on findings returned")
@handle_unity_errors
def audit_mobile(folder, include_packages, page_size, cursor, max_issues):
    """Advisory mobile-performance heuristic scan (static rules, never modifies)."""
    config = get_config()
    params = {"action": "audit_mobile"}
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
