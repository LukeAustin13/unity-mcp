"""Checkpoint CLI commands — named scene-state snapshots (create/list/restore/delete)."""

import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.connection import run_command, handle_unity_errors


@click.group()
def checkpoint():
    """Scene-state checkpoints - snapshot, list, restore, and delete."""
    pass


@checkpoint.command("create")
@click.option("--label", "-l", help="Optional name (^[A-Za-z0-9_-]{1,64}$) used as the checkpoint id")
@handle_unity_errors
def create(label):
    """Snapshot the currently-loaded (saved) scenes into a new checkpoint."""
    config = get_config()
    params = {"action": "create"}
    if label:
        params["label"] = label
    click.echo(format_output(run_command("manage_checkpoint", params, config), config.format))


@checkpoint.command("list")
@handle_unity_errors
def list_checkpoints():
    """List all checkpoints (id, label, createdUtc, scenes)."""
    config = get_config()
    params = {"action": "list"}
    click.echo(format_output(run_command("manage_checkpoint", params, config), config.format))


@checkpoint.command("restore")
@click.argument("checkpoint_id")
@handle_unity_errors
def restore(checkpoint_id):
    """Roll back to a checkpoint. OVERWRITES scene files on disk and discards unsaved changes."""
    config = get_config()
    params = {"action": "restore", "id": checkpoint_id}
    click.echo(format_output(run_command("manage_checkpoint", params, config), config.format))


@checkpoint.command("delete")
@click.argument("checkpoint_id")
@handle_unity_errors
def delete(checkpoint_id):
    """Delete a checkpoint directory by id."""
    config = get_config()
    params = {"action": "delete", "id": checkpoint_id}
    click.echo(format_output(run_command("manage_checkpoint", params, config), config.format))
