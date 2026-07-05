from typing import Annotated, Any, Literal

from fastmcp import Context
from fastmcp.server.server import ToolResult
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import (
    build_screenshot_params,
    build_visual_audit_params,
    extract_screenshot_images,
)
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

# All possible actions grouped by category
SETUP_ACTIONS = ["ping", "ensure_brain", "get_brain_status"]

CREATION_ACTIONS = ["create_camera"]

CONFIGURATION_ACTIONS = [
    "set_target", "set_priority", "set_lens",
    "set_body", "set_aim", "set_noise",
]

EXTENSION_ACTIONS = ["add_extension", "remove_extension"]

CONTROL_ACTIONS = [
    "set_blend", "force_camera", "release_override", "list_cameras",
]

# Capture actions produce artifact files (screenshots / diff images).
CAPTURE_ACTIONS = ["screenshot", "screenshot_multiview", "screenshot_compare"]

# Zero-pixel visual facts (no artifact written).
ANALYSIS_ACTIONS = ["visibility_report"]

ALL_ACTIONS = (
    SETUP_ACTIONS + CREATION_ACTIONS + CONFIGURATION_ACTIONS + EXTENSION_ACTIONS
    + CONTROL_ACTIONS + CAPTURE_ACTIONS + ANALYSIS_ACTIONS
)


@mcp_for_unity_tool(
    group="core",
    description=(
        "Manage cameras (Unity Camera + Cinemachine). Works without Cinemachine using basic Camera; "
        "unlocks presets, pipelines, and blending when Cinemachine is installed. "
        "Use ping to check Cinemachine availability.\n\n"
        "SETUP:\n"
        "- ping: Check if Cinemachine is available\n"
        "- ensure_brain: Ensure CinemachineBrain exists on main camera\n"
        "- get_brain_status: Get Brain state (active camera, blend, etc.)\n\n"
        "CAMERA CREATION:\n"
        "- create_camera: Create camera with preset (third_person, freelook, "
        "follow, dolly, static, top_down, side_scroller). Falls back to basic Camera without Cinemachine.\n\n"
        "CAMERA CONFIGURATION:\n"
        "- set_target: Set Follow and/or LookAt targets on a camera\n"
        "- set_priority: Set camera priority for Brain selection\n"
        "- set_lens: Configure lens (fieldOfView, nearClipPlane, farClipPlane, orthographicSize, dutch)\n"
        "- set_body: Configure Body component (bodyType to swap, plus component properties)\n"
        "- set_aim: Configure Aim component (aimType to swap, plus component properties)\n"
        "- set_noise: Configure Noise component (amplitudeGain, frequencyGain)\n\n"
        "EXTENSIONS:\n"
        "- add_extension: Add extension (extensionType: CinemachineConfiner2D, CinemachineDeoccluder, "
        "CinemachineImpulseListener, CinemachineFollowZoom, CinemachineRecomposer, etc.)\n"
        "- remove_extension: Remove extension by type\n\n"
        "CAMERA CONTROL:\n"
        "- set_blend: Configure default blend (style: Cut/EaseInOut/Linear/etc., duration)\n"
        "- force_camera: Override Brain to use specific camera\n"
        "- release_override: Release camera override\n"
        "- list_cameras: List all cameras with status\n\n"
        "VISUAL AUDITING (ask before you look — cheapest tier first):\n"
        "- visibility_report: Zero-pixel visual facts for a camera. Frustum-tests every active "
        "Renderer and returns each in-frustum object's screen-space rect (as % of frame), approximate "
        "coverage_pct, and distance — sorted by coverage, paged. Optional check_occlusion does a single "
        "raycast per object to report an approximate occluder. Answers 'is X on screen / how big / behind "
        "what' for ~50 tokens with NO image. Prefer this over a screenshot whenever the question is "
        "geometric.\n"
        "- screenshot_compare: Capture the current frame and numerically diff it against baseline_path "
        "(a prior screenshot in the project's screenshot folder or under Assets/). Returns changed_pixel_pct, "
        "diff_bbox, mean_delta and dimensions — NO image by default (set save_diff=true to also write a "
        "diff-visualization PNG). Answers 'did my change affect only region Y' for ~50 tokens.\n\n"
        "CAPTURE:\n"
        "- screenshot: Capture a screenshot. By default (no camera specified) uses ScreenCapture API, "
        "which captures all render layers including Screen Space - Overlay UI canvases. "
        "Specifying a camera uses direct camera rendering, which EXCLUDES Screen Space - Overlay canvases "
        "(use only when you need a specific viewpoint without UI). "
        "Supports include_image=true for inline base64. The inline image is right-sized: it defaults to "
        "max_width=1280 (aspect preserved) so the payload is never a multi-MB image the model downscales "
        "away — the full-resolution file on disk is unchanged. Tune with max_width, image_format='jpg' "
        "(+jpg_quality) for smaller payloads, and crop_target (+crop_padding_px) to focus the inline image "
        "on one object's screen bounds. Also supports "
        "batch='surround' for 6-angle contact sheet, batch='orbit' for configurable grid, "
        "view_target/view_position for positioned capture, and capture_source='scene_view' to capture "
        "the active Unity Scene View viewport.\n"
        "- screenshot_multiview: Shorthand for screenshot with batch='surround' and include_image=true."
    ),
    annotations=ToolAnnotations(
        title="Manage Camera",
        destructiveHint=True,
    ),
)
async def manage_camera(
    ctx: Context,
    action: Annotated[str, "The camera action to perform."],
    target: Annotated[str | None, "Target camera (name, path, or instance ID)."] = None,
    search_method: Annotated[
        Literal["by_id", "by_name", "by_path"] | None,
        "How to find target.",
    ] = None,
    properties: Annotated[
        dict[str, Any] | str | None,
        "Action-specific parameters (dict or JSON string).",
    ] = None,
    # --- screenshot params ---
    screenshot_file_name: Annotated[str | None,
        "Screenshot file name (optional). Defaults to timestamp."] = None,
    screenshot_super_size: Annotated[int | str | None,
        "Screenshot supersize multiplier (integer >= 1)."] = None,
    camera: Annotated[str | None,
        "Camera to capture from (name, path, or instance ID). "
        "Omit to use ScreenCapture API (captures all layers including Screen Space Overlay UI). "
        "Specify only when you need a particular camera viewpoint; note that Screen Space - Overlay "
        "canvases will NOT appear in camera-rendered captures."] = None,
    include_image: Annotated[bool | str | None,
        "If true, return screenshot as inline base64 image. Default false."] = None,
    max_resolution: Annotated[int | str | None,
        "Max resolution (longest edge px) for batch/contact-sheet tiles. "
        "For a single image prefer max_width."] = None,
    max_width: Annotated[int | str | None,
        "Downscale the INLINE image to this width (aspect preserved) before encoding. "
        "Defaults to 1280 when include_image is true and no width is given; the disk file is unaffected."] = None,
    image_format: Annotated[Literal["png", "jpg"] | None,
        "Inline image encoding: 'png' (default) or 'jpg' (smaller, lossy)."] = None,
    jpg_quality: Annotated[int | str | None,
        "JPEG quality 1-100 when image_format='jpg'. Default 80."] = None,
    crop_target: Annotated[str | None,
        "GameObject name or hierarchy path. Crops the inline image to that object's "
        "renderer-bounds screen rect (+ crop_padding_px), clamped to the frame."] = None,
    crop_padding_px: Annotated[int | str | None,
        "Padding in pixels around crop_target's screen rect. Default 32."] = None,
    baseline_path: Annotated[str | None,
        "screenshot_compare only: path to a prior screenshot to diff against. Must resolve "
        "inside the project's screenshot folder or under Assets/."] = None,
    diff_threshold: Annotated[int | str | None,
        "screenshot_compare only: per-channel delta (0-255) above which a pixel counts as changed. Default 8."] = None,
    save_diff: Annotated[bool | str | None,
        "screenshot_compare only: if true, write a diff-visualization PNG and return its path. Default false."] = None,
    check_occlusion: Annotated[bool | str | None,
        "visibility_report only: if true, raycast each in-frustum object and report an approximate occluder."] = None,
    page_size: Annotated[int | str | None,
        "visibility_report only: max items per page (default 25, capped at 100)."] = None,
    cursor: Annotated[int | str | None,
        "visibility_report only: paging offset into the sorted item list."] = None,
    capture_source: Annotated[Literal["game_view", "scene_view"] | None,
        "Screenshot source. 'game_view' (default) captures the game/camera path; "
        "'scene_view' captures the active Unity Scene View viewport."] = None,
    batch: Annotated[str | None,
        "Batch capture mode: 'surround' (6 angles) or 'orbit' (configurable grid)."] = None,
    view_target: Annotated[str | int | list[float] | None,
        "Target to focus on. GameObject name/path/ID or [x,y,z]. "
        "For game_view: aims camera at target. For scene_view: frames the Scene View on the target."] = None,
    view_position: Annotated[list[float] | str | None,
        "World position [x,y,z] to place camera for positioned capture."] = None,
    view_rotation: Annotated[list[float] | str | None,
        "Euler rotation [x,y,z] for camera. Overrides view_target if both provided."] = None,
    orbit_angles: Annotated[int | str | None,
        "Number of azimuth samples for batch='orbit' (default 8, max 36)."] = None,
    orbit_elevations: Annotated[list[float] | str | None,
        "Elevation angles in degrees for batch='orbit' (default [0, 30, -15])."] = None,
    orbit_distance: Annotated[float | str | None,
        "Camera distance from target for batch='orbit' (default auto)."] = None,
    orbit_fov: Annotated[float | str | None,
        "Camera FOV in degrees for batch='orbit' (default 60)."] = None,
    output_folder: Annotated[str | None,
        "Optional folder for screenshot output. Project-relative (e.g. 'Assets/Screenshots' or 'Captures') "
        "or absolute path inside the project. Overrides the user's Editor preference. "
        "If omitted, falls back to the Editor preference, then to the built-in default (Assets/Screenshots)."] = None,
) -> dict[str, Any] | ToolResult:
    """Unified camera management tool (Unity Camera + Cinemachine)."""

    action_normalized = action.lower()

    if action_normalized not in ALL_ACTIONS:
        categories = {
            "Setup": SETUP_ACTIONS,
            "Creation": CREATION_ACTIONS,
            "Configuration": CONFIGURATION_ACTIONS,
            "Extensions": EXTENSION_ACTIONS,
            "Control": CONTROL_ACTIONS,
            "Capture": CAPTURE_ACTIONS,
        }
        category_list = "; ".join(
            f"{cat}: {', '.join(actions)}" for cat, actions in categories.items()
        )
        return {
            "success": False,
            "message": (
                f"Unknown action '{action}'. Available actions by category — {category_list}. "
                "Run with action='ping' to check Cinemachine availability."
            ),
        }

    unity_instance = await get_unity_instance_from_context(ctx)

    params_dict: dict[str, Any] = {"action": action_normalized}
    if properties is not None:
        params_dict["properties"] = properties
    if target is not None:
        params_dict["target"] = target
    if search_method is not None:
        params_dict["searchMethod"] = search_method

    # Screenshot params — relevant for every capture action (screenshot,
    # screenshot_multiview, screenshot_compare all capture a frame).
    if action_normalized in CAPTURE_ACTIONS:
        err = build_screenshot_params(
            params_dict,
            screenshot_file_name=screenshot_file_name,
            screenshot_super_size=screenshot_super_size,
            camera=camera,
            include_image=include_image,
            max_resolution=max_resolution,
            max_width=max_width,
            image_format=image_format,
            jpg_quality=jpg_quality,
            crop_target=crop_target,
            crop_padding_px=crop_padding_px,
            capture_source=capture_source,
            batch=batch,
            view_target=view_target,
            orbit_angles=orbit_angles,
            orbit_elevations=orbit_elevations,
            orbit_distance=orbit_distance,
            orbit_fov=orbit_fov,
            view_position=view_position,
            view_rotation=view_rotation,
            output_folder=output_folder,
        )
        if err is not None:
            return err

    # Visual-audit params — screenshot_compare needs a baseline + diff knobs;
    # visibility_report needs camera + paging + occlusion. build_visual_audit_params
    # only sets the keys it was given, so it is safe to pass the whole bag for both.
    if action_normalized == "screenshot_compare" or action_normalized in ANALYSIS_ACTIONS:
        if action_normalized == "screenshot_compare" and not (
            baseline_path and str(baseline_path).strip()
        ):
            return {
                "success": False,
                "message": "screenshot_compare requires 'baseline_path' (a prior screenshot to diff against).",
            }
        err = build_visual_audit_params(
            params_dict,
            camera=camera,
            baseline_path=baseline_path,
            diff_threshold=diff_threshold,
            save_diff=save_diff,
            check_occlusion=check_occlusion,
            page_size=page_size,
            cursor=cursor,
        )
        if err is not None:
            return err

    result = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "manage_camera",
        params_dict,
    )

    if not isinstance(result, dict):
        return {"success": False, "message": str(result)}

    # For capture actions, check for inline images to return as ImageContent
    if action_normalized in CAPTURE_ACTIONS:
        image_result = extract_screenshot_images(result)
        if image_result is not None:
            return image_result

    return result
