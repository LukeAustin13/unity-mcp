"""Audit log CLI - inspect the local MCP tool-call audit log.

Reads the JSONL audit file directly (written by the safety enforcement layer);
it does not need Unity or a running server.
"""
import json

import click

from core.audit import read_audit_records, summarize_audit, resolve_audit_path


@click.group()
def audit():
    """Inspect the local MCP tool-call audit log."""
    pass


@audit.command("tail")
@click.option("-n", "--count", default=20, show_default=True, help="How many recent records to show")
@click.option("--path", default=None, help="Audit log path (defaults to the standard location)")
@click.option("--blocked-only", is_flag=True, help="Show only blocked / confirmation-required calls")
def tail(count, path, blocked_only):
    """Show the most recent audited tool calls."""
    records = read_audit_records(path=path)
    if blocked_only:
        records = [r for r in records if r.get("decision") in ("block", "confirm_required")]
    records = records[-count:] if count and count >= 0 else records

    if not records:
        click.echo(f"No audit records at {path or resolve_audit_path()}")
        return

    for r in records:
        action = r.get("action") or ""
        suffix = f"({action})" if action else ""
        click.echo(
            f"{r.get('ts','?')}  {str(r.get('decision','?')):16}  "
            f"{str(r.get('mode','?')):11}  {r.get('tool','?')}{suffix}"
        )
        if r.get("decision") in ("block", "confirm_required") and r.get("reason"):
            click.echo(f"    reason: {str(r['reason'])[:200]}")


@audit.command("summary")
@click.option("--path", default=None, help="Audit log path (defaults to the standard location)")
def summary(path):
    """Aggregate the audit log (counts by decision/class/tool, recent blocks)."""
    records = read_audit_records(path=path)
    click.echo(json.dumps(summarize_audit(records), indent=2, default=str))
