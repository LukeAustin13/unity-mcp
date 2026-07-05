"""audit_ui_layout — analytic uGUI layout auditor across a resolution matrix.

READ-only. Simulates uGUI (Canvas/RectTransform/CanvasScaler) layout at several
resolutions using pure math — it NEVER mutates anything: no GameView resolution
switching, no play mode, no scene dirtying, no asset writes, no screenshots. The
heavy lifting (scene traversal, CanvasScaler scale-factor math, screen-rect
computation, overlap/off-screen predicates) is C# (AuditUiLayout.cs); this module
validates/forwards parameters and is verified by Python plumbing tests plus Unity
EditMode tests.
"""
from typing import Annotated, Any

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import coerce_bool, coerce_int
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

_DEFAULT_RESOLUTIONS = ["1920x1080", "1280x720", "2560x1440", "3440x1440"]
_MAX_RESOLUTIONS = 8
_MIN_DIMENSION = 16
_MAX_DIMENSION = 16384
_MIN_TARGET_FLOOR = 8
_MIN_TARGET_CEIL = 256
_MAX_FINDINGS_CEIL = 2000


def _normalize_resolutions(value: Any) -> tuple[list[str] | None, str | None]:
    """Coerce + strictly validate the resolution list.

    Accepts a list, a comma-separated string, or a JSON array string. Each entry
    must be strict ``WxH`` with integer dimensions in [16, 16384]; at most 8 entries.
    Returns (list, None) on success or (None, error) on any malformed entry.
    Returns (None, None) when unset so C# applies its default matrix.
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
                    return None, "resolutions must be a list of 'WxH' strings."
            except (ValueError, TypeError):
                return None, f"resolutions is not valid JSON: '{value}'."
        else:
            value = [part.strip() for part in s.split(",") if part.strip()]

    if not isinstance(value, (list, tuple)):
        return None, f"resolutions must be a list of 'WxH' strings, got {type(value).__name__}."

    if len(value) > _MAX_RESOLUTIONS:
        return None, f"Too many resolutions ({len(value)}); the maximum is {_MAX_RESOLUTIONS}."

    cleaned: list[str] = []
    for item in value:
        entry = str(item).strip()
        if not entry:
            return None, "Empty resolution entry. Use strict 'WxH' form, e.g. '1920x1080'."
        lowered = entry.lower()
        if "x" not in lowered:
            return None, f"Invalid resolution '{entry}'. Use strict 'WxH' form, e.g. '1920x1080'."
        w_str, _, h_str = lowered.partition("x")
        if not (w_str.isdigit() and h_str.isdigit()):
            return None, f"Invalid resolution '{entry}'. Both dimensions must be integers, e.g. '1920x1080'."
        w, h = int(w_str), int(h_str)
        if not (_MIN_DIMENSION <= w <= _MAX_DIMENSION and _MIN_DIMENSION <= h <= _MAX_DIMENSION):
            return None, (
                f"Resolution '{entry}' out of range. Each dimension must be "
                f"{_MIN_DIMENSION}..{_MAX_DIMENSION}."
            )
        canonical = f"{w}x{h}"
        if canonical not in cleaned:
            cleaned.append(canonical)

    return (cleaned or None), None


@mcp_for_unity_tool(
    group="core",
    description=(
        "Audit uGUI layout analytically across a resolution matrix (READ-only; mutates "
        "nothing — no GameView switching, no play mode, no scene dirtying, no screenshots). "
        "For each Canvas it simulates the CanvasScaler scale factor and every RectTransform's "
        "screen rect BY MATH (from anchors/pivot/offsets/localScale), then reports layout "
        "problems.\n"
        "STRUCTURAL checks (resolution-independent): missing_event_system, "
        "missing_graphic_raycaster, missing_listeners (advisory), invisible_interactable, "
        "suspicious_anchors (advisory), canvas_scaler (advisory).\n"
        "PER-RESOLUTION checks (analytic): off_screen, overlapping_interactive, tiny_target, "
        "clipped_text (advisory), blocked_interactable (advisory). Findings that could differ "
        "at runtime (LayoutGroup/ContentSizeFitter/AspectRatioFitter-driven sizes, ConstantPhysicalSize "
        "DPI) are marked advisory:true rather than dropped.\n"
        "PARAMS: resolutions (list of 'WxH', default 1920x1080/1280x720/2560x1440/3440x1440, max 8, "
        "each dim 16..16384); scene (must be a LOADED scene — never opened; omit for all loaded); "
        "include_inactive (default false); min_target_px (default 44, clamped 8..256); "
        "max_findings (default 500, cap 2000). Returns {findings:[{check, severity, advisory, scene, "
        "object_path, resolution, screen_rect, reason, suggested_fix}], summary:{by_severity, by_check, "
        "canvases_scanned, controls_scanned, truncated}, caveats:[...]}. No images — facts only."
    ),
    annotations=ToolAnnotations(
        title="Audit UI Layout",
        readOnlyHint=True,
    ),
)
async def audit_ui_layout(
    ctx: Context,
    resolutions: Annotated[
        list[str] | str | None,
        "Resolutions to audit as strict 'WxH' strings (default the standard 16:9/21:9 matrix; "
        "max 8 entries, each dimension 16..16384).",
    ] = None,
    scene: Annotated[
        str | None,
        "Restrict to a single LOADED scene by name (default: all loaded scenes). The scene "
        "must already be open — this tool never opens scenes.",
    ] = None,
    include_inactive: Annotated[
        bool | str | None,
        "Include inactive UI objects (default false).",
    ] = None,
    min_target_px: Annotated[
        int | str | None,
        "Minimum interactive touch-target size in pixels (default 44, clamped 8..256).",
    ] = None,
    max_findings: Annotated[
        int | str | None,
        "Cap on emitted findings before truncation (default 500, cap 2000).",
    ] = None,
) -> dict[str, Any]:
    normalized_resolutions, err = _normalize_resolutions(resolutions)
    if err:
        return {"success": False, "message": err}

    params: dict[str, Any] = {}

    if normalized_resolutions is not None:
        params["resolutions"] = normalized_resolutions

    scene_clean = scene.strip() if isinstance(scene, str) else None
    if scene_clean:
        params["scene"] = scene_clean

    include_inactive_coerced = coerce_bool(include_inactive, default=None)
    if include_inactive_coerced is not None:
        params["include_inactive"] = include_inactive_coerced

    min_target_coerced = coerce_int(min_target_px, default=None)
    if min_target_coerced is not None:
        params["min_target_px"] = max(_MIN_TARGET_FLOOR, min(min_target_coerced, _MIN_TARGET_CEIL))

    max_findings_coerced = coerce_int(max_findings, default=None)
    if max_findings_coerced is not None:
        params["max_findings"] = max(1, min(max_findings_coerced, _MAX_FINDINGS_CEIL))

    unity_instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "audit_ui_layout", params
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
