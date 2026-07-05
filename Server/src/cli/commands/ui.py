"""UI CLI commands - placeholder for future implementation."""

import sys
import click
from typing import Optional, Any

from cli.utils.config import get_config
from cli.utils.output import format_output, print_error, print_success
from cli.utils.connection import run_command, handle_unity_errors


@click.group()
def ui():
    """UI operations - create and modify UI elements."""
    pass


@ui.command("create-canvas")
@click.argument("name")
@click.option(
    "--render-mode",
    type=click.Choice(
        ["ScreenSpaceOverlay", "ScreenSpaceCamera", "WorldSpace"]),
    default="ScreenSpaceOverlay",
    help="Canvas render mode."
)
@handle_unity_errors
def create_canvas(name: str, render_mode: str):
    """Create a new Canvas.

    \b
    Examples:
        unity-mcp ui create-canvas "MainUI"
        unity-mcp ui create-canvas "WorldUI" --render-mode WorldSpace
    """
    config = get_config()

    # Step 1: Create empty GameObject
    result = run_command("manage_gameobject", {
        "action": "create",
        "name": name,
    }, config)

    if not (result.get("success") or result.get("data") or result.get("result")):
        click.echo(format_output(result, config.format))
        return

    # Step 2: Add Canvas components
    failed_components = []
    for component in ["Canvas", "CanvasScaler", "GraphicRaycaster"]:
        comp_result = run_command("manage_components", {
            "action": "add",
            "target": name,
            "componentType": component,
        }, config)
        if not (comp_result.get("success") or comp_result.get("data")):
            failed_components.append((component, comp_result.get("error", "Unknown error")))

    if failed_components:
        error_details = "; ".join([f"{c}: {e}" for c, e in failed_components])
        print_error(f"Failed to add components: {error_details}")

    # Step 3: Set render mode
    render_mode_value = {"ScreenSpaceOverlay": 0,
                         "ScreenSpaceCamera": 1, "WorldSpace": 2}.get(render_mode, 0)
    run_command("manage_components", {
        "action": "set_property",
        "target": name,
        "componentType": "Canvas",
        "property": "renderMode",
        "value": render_mode_value,
    }, config)

    click.echo(format_output(result, config.format))
    print_success(f"Created Canvas: {name}")


@ui.command("create-text")
@click.argument("name")
@click.option(
    "--parent", "-p",
    required=True,
    help="Parent Canvas or UI element."
)
@click.option(
    "--text", "-t",
    default="New Text",
    help="Initial text content."
)
@click.option(
    "--position",
    nargs=2,
    type=float,
    default=(0, 0),
    help="Anchored position X Y."
)
@handle_unity_errors
def create_text(name: str, parent: str, text: str, position: tuple):
    """Create a UI Text element (TextMeshPro).

    \b
    Examples:
        unity-mcp ui create-text "TitleText" --parent "MainUI" --text "Hello World"
    """
    config = get_config()

    # Step 1: Create empty GameObject with parent
    result = run_command("manage_gameobject", {
        "action": "create",
        "name": name,
        "parent": parent,
        "position": list(position),
    }, config)

    if not (result.get("success") or result.get("data") or result.get("result")):
        click.echo(format_output(result, config.format))
        return

    # Step 2: Add RectTransform and TextMeshProUGUI
    run_command("manage_components", {
        "action": "add",
        "target": name,
        "componentType": "TextMeshProUGUI",
    }, config)

    # Step 3: Set text content
    run_command("manage_components", {
        "action": "set_property",
        "target": name,
        "componentType": "TextMeshProUGUI",
        "property": "text",
        "value": text,
    }, config)

    click.echo(format_output(result, config.format))
    print_success(f"Created Text: {name}")


@ui.command("create-button")
@click.argument("name")
@click.option(
    "--parent", "-p",
    required=True,
    help="Parent Canvas or UI element."
)
@click.option(
    "--text", "-t",
    default="Button",
    help="Button label text."
)
@handle_unity_errors
def create_button(name: str, parent: str, text: str):  # text current placeholder
    """Create a UI Button.

    \b
    Examples:
        unity-mcp ui create-button "StartButton" --parent "MainUI" --text "Start Game"
    """
    config = get_config()

    # Step 1: Create empty GameObject with parent
    result = run_command("manage_gameobject", {
        "action": "create",
        "name": name,
        "parent": parent,
    }, config)

    if not (result.get("success") or result.get("data") or result.get("result")):
        click.echo(format_output(result, config.format))
        return

    # Step 2: Add Button and Image components
    for component in ["Image", "Button"]:
        run_command("manage_components", {
            "action": "add",
            "target": name,
            "componentType": component,
        }, config)

    # Step 3: Create child label GameObject
    label_name = f"{name}_Label"
    run_command("manage_gameobject", {
        "action": "create",
        "name": label_name,
        "parent": name,
    }, config)

    # Step 4: Add TextMeshProUGUI to label and set text
    run_command("manage_components", {
        "action": "add",
        "target": label_name,
        "componentType": "TextMeshProUGUI",
    }, config)
    run_command("manage_components", {
        "action": "set_property",
        "target": label_name,
        "componentType": "TextMeshProUGUI",
        "property": "text",
        "value": text,
    }, config)

    click.echo(format_output(result, config.format))
    print_success(f"Created Button: {name} (with label '{text}')")


@ui.command("create-image")
@click.argument("name")
@click.option(
    "--parent", "-p",
    required=True,
    help="Parent Canvas or UI element."
)
@click.option(
    "--sprite", "-s",
    default=None,
    help="Sprite asset path."
)
@handle_unity_errors
def create_image(name: str, parent: str, sprite: Optional[str]):
    """Create a UI Image.

    \b
    Examples:
        unity-mcp ui create-image "Background" --parent "MainUI"
        unity-mcp ui create-image "Icon" --parent "MainUI" --sprite "Assets/Sprites/icon.png"
    """
    config = get_config()

    # Step 1: Create empty GameObject with parent
    result = run_command("manage_gameobject", {
        "action": "create",
        "name": name,
        "parent": parent,
    }, config)

    if not (result.get("success") or result.get("data") or result.get("result")):
        click.echo(format_output(result, config.format))
        return

    # Step 2: Add Image component
    run_command("manage_components", {
        "action": "add",
        "target": name,
        "componentType": "Image",
    }, config)

    # Step 3: Set sprite if provided
    if sprite:
        run_command("manage_components", {
            "action": "set_property",
            "target": name,
            "componentType": "Image",
            "property": "sprite",
            "value": sprite,
        }, config)

    click.echo(format_output(result, config.format))
    print_success(f"Created Image: {name}")


@ui.command("audit-layout")
@click.option(
    "--resolution", "-r", "resolutions",
    multiple=True,
    help="Resolution to audit as 'WxH' (repeatable, max 8). Defaults to the standard 16:9/21:9 matrix.",
)
@click.option(
    "--scene", "-s",
    default=None,
    help="Restrict to a single LOADED scene by name (never opens scenes).",
)
@click.option(
    "--include-inactive",
    is_flag=True,
    default=False,
    help="Include inactive UI objects.",
)
@click.option(
    "--min-target-px",
    type=int,
    default=None,
    help="Minimum interactive touch-target size in pixels (default 44, clamped 8..256).",
)
@click.option(
    "--max-findings",
    type=int,
    default=None,
    help="Cap on emitted findings before truncation (default 500, cap 2000).",
)
@handle_unity_errors
def audit_layout(resolutions: tuple, scene: Optional[str], include_inactive: bool,
                 min_target_px: Optional[int], max_findings: Optional[int]):
    """Audit uGUI layout analytically across a resolution matrix (READ-only).

    Simulates CanvasScaler scaling and RectTransform screen rects by math and
    reports off-screen, overlapping, tiny-target, invisible, and structural UI
    problems without mutating anything.

    \b
    Examples:
        unity-mcp ui audit-layout
        unity-mcp ui audit-layout -r 1920x1080 -r 1080x1920 --scene Main
        unity-mcp ui audit-layout --min-target-px 48 --include-inactive
    """
    config = get_config()

    params: dict[str, Any] = {}
    if resolutions:
        params["resolutions"] = list(resolutions)
    if scene:
        params["scene"] = scene
    if include_inactive:
        params["include_inactive"] = True
    if min_target_px is not None:
        params["min_target_px"] = min_target_px
    if max_findings is not None:
        params["max_findings"] = max_findings

    result = run_command("audit_ui_layout", params, config)
    click.echo(format_output(result, config.format))
