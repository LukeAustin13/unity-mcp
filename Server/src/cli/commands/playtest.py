"""Play-mode smoke test CLI - boot the game and report a pass/fail verdict."""

import click

from cli.utils.config import get_config
from cli.utils.output import format_output
from cli.utils.connection import run_command, handle_unity_errors


@click.group()
def playtest():
    """Play-mode smoke test - boot the game and verify it runs cleanly."""
    pass


@playtest.command("run")
@click.option("--seconds", "-s", type=int, default=15, show_default=True,
              help="How long to stay in play mode (max 120).")
@click.option("--timeout", "timeout_seconds", type=int, default=None,
              help="Max seconds to wait for the whole run (default: seconds + headroom).")
@handle_unity_errors
def run(seconds, timeout_seconds):
    """Enter play mode, run for N seconds, and report errors/exceptions + verdict."""
    config = get_config()
    params = {"action": "run", "seconds": seconds}
    if timeout_seconds is not None:
        params["timeout_seconds"] = timeout_seconds
    # Give the HTTP call enough headroom to cover the play duration + reload.
    call_timeout = max(config.timeout, seconds + 120)
    click.echo(format_output(
        run_command("play_smoke_test", params, config, timeout=call_timeout),
        config.format,
    ))
