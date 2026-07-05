"""
Safety modes and destructive-action classification for MCP tool calls.

Every MCP tool call is classified into one of four classes and checked
against the server's configured safety mode before it reaches Unity:

Classes (ordered by severity):
  READ        No project or editor state change.
  VALIDATE    Transient editor state only (run tests, refresh assets,
              enter/exit play mode, screenshots). Nothing persistent is
              modified and nothing is deleted.
  WRITE       Persistent project/editor mutation that is part of normal
              editing (create/modify assets, scripts, scenes, settings).
  DESTRUCTIVE Deletes data, executes arbitrary code or menu items, runs
              builds, or installs/removes packages (which executes code
              on import).

Modes:
  read_only    Only READ calls are allowed.
  review_only  READ and VALIDATE calls are allowed (inspect + run tests).
  write        Everything is allowed, but DESTRUCTIVE calls must carry
               "confirm": true in their arguments.

Classification is deliberately an explicit table, not a heuristic: a
misclassified write is a safety hole, a misclassified read is only
friction. Unknown tools and unknown actions fall back to the most
restrictive sensible class (WRITE), so new tools are blocked outside
write mode until someone classifies them here.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("mcp-for-unity-server")


class SafetyMode(str, Enum):
    READ_ONLY = "read_only"
    REVIEW_ONLY = "review_only"
    WRITE = "write"


class ToolClass(str, Enum):
    READ = "read"
    VALIDATE = "validate"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


# Severity ordering used when escalating (e.g. batch_execute).
_CLASS_SEVERITY: dict[ToolClass, int] = {
    ToolClass.READ: 0,
    ToolClass.VALIDATE: 1,
    ToolClass.WRITE: 2,
    ToolClass.DESTRUCTIVE: 3,
}

_ALLOWED_CLASSES: dict[SafetyMode, set[ToolClass]] = {
    SafetyMode.READ_ONLY: {ToolClass.READ},
    SafetyMode.REVIEW_ONLY: {ToolClass.READ, ToolClass.VALIDATE},
    SafetyMode.WRITE: {ToolClass.READ, ToolClass.VALIDATE,
                       ToolClass.WRITE, ToolClass.DESTRUCTIVE},
}


class Decision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    CONFIRM_REQUIRED = "confirm_required"


@dataclass(frozen=True)
class ToolPolicy:
    """Classification for one tool: a default class plus per-action overrides."""
    default: ToolClass
    actions: dict[str, ToolClass] = field(default_factory=dict)


# Fallback for tools not listed below (including project custom tools that
# are registered dynamically): treated as WRITE so they are blocked outside
# write mode but do not demand confirmation.
UNKNOWN_TOOL_CLASS = ToolClass.WRITE

TOOL_POLICIES: dict[str, ToolPolicy] = {
    # --- Pure reads / server meta-tools ---
    "find_gameobjects": ToolPolicy(ToolClass.READ),
    "find_in_file": ToolPolicy(ToolClass.READ),
    "unity_docs": ToolPolicy(ToolClass.READ),
    "unity_reflect": ToolPolicy(ToolClass.READ),
    "debug_request_context": ToolPolicy(ToolClass.READ),
    "get_test_job": ToolPolicy(ToolClass.READ),
    "validate_script": ToolPolicy(ToolClass.READ),
    "get_sha": ToolPolicy(ToolClass.READ),
    "manage_script_capabilities": ToolPolicy(ToolClass.READ),
    # Session-level meta-tools: no project/editor state is touched.
    "set_active_instance": ToolPolicy(ToolClass.READ),
    "manage_tools": ToolPolicy(ToolClass.READ),
    # Low-level probe/introspection commands the CLI/API may issue directly.
    "ping": ToolPolicy(ToolClass.READ),
    "get_tool_states": ToolPolicy(ToolClass.READ),
    # Server-side safety-posture reporter (no Unity call).
    "safety_status": ToolPolicy(ToolClass.READ),
    # Whole-project health/intelligence scans — all actions are read-only.
    "manage_project": ToolPolicy(ToolClass.READ),
    # One-read situational-awareness snapshot (no mutation).
    "get_editor_context": ToolPolicy(ToolClass.READ),
    # Filter+projection query over loaded scenes (traverses in-memory objects; no mutation).
    "query_scene": ToolPolicy(ToolClass.READ),
    # Analytic uGUI layout audit across a resolution matrix (pure math; mutates nothing).
    "audit_ui_layout": ToolPolicy(ToolClass.READ),
    # Read-only build pre-flight (inspects settings; produces no artifacts).
    "validate_build": ToolPolicy(ToolClass.READ),
    # Read-only scene-contract validator (traverses a loaded scene; opens/mutates nothing).
    "validate_scene_contracts": ToolPolicy(ToolClass.READ),

    # --- Composed test flow ---
    "run_tests_and_summarize": ToolPolicy(ToolClass.VALIDATE),

    "read_console": ToolPolicy(ToolClass.READ, {
        "clear": ToolClass.WRITE,
    }),

    # --- Validation / transient editor state ---
    "run_tests": ToolPolicy(ToolClass.VALIDATE),
    # Enters play mode, runs the project, then exits — like run_tests it executes
    # project code but mutates nothing persistent (play exit restores scene state).
    "play_smoke_test": ToolPolicy(ToolClass.VALIDATE),
    "refresh_unity": ToolPolicy(ToolClass.VALIDATE),
    "manage_profiler": ToolPolicy(ToolClass.VALIDATE, {
        "ping": ToolClass.READ,
        "profiler_status": ToolClass.READ,
        "get_frame_timing": ToolClass.READ,
        "get_counters": ToolClass.READ,
        "get_object_memory": ToolClass.READ,
        "memory_list_snapshots": ToolClass.READ,
        "memory_compare_snapshots": ToolClass.READ,
        "frame_debugger_get_events": ToolClass.READ,
        # Writes a snapshot file to a caller-supplied path.
        "memory_take_snapshot": ToolClass.WRITE,
    }),

    # --- Scene / hierarchy ---
    "manage_scene": ToolPolicy(ToolClass.WRITE, {
        "get_hierarchy": ToolClass.READ,
        "get_active": ToolClass.READ,
        "get_build_settings": ToolClass.READ,
        "get_loaded_scenes": ToolClass.READ,
        "validate": ToolClass.READ,
        "scene_view_frame": ToolClass.VALIDATE,
    }),
    "manage_gameobject": ToolPolicy(ToolClass.WRITE, {
        "delete": ToolClass.DESTRUCTIVE,
    }),
    "manage_components": ToolPolicy(ToolClass.WRITE),

    # --- Assets ---
    "manage_asset": ToolPolicy(ToolClass.WRITE, {
        "search": ToolClass.READ,
        "get_info": ToolClass.READ,
        "get_components": ToolClass.READ,
        "delete": ToolClass.DESTRUCTIVE,
    }),
    "manage_prefabs": ToolPolicy(ToolClass.WRITE, {
        "get_info": ToolClass.READ,
        "get_hierarchy": ToolClass.READ,
        "open_prefab_stage": ToolClass.VALIDATE,
        # close_prefab_stage is VALIDATE (discard) but WRITE when
        # save_before_close is set — see _classify_manage_prefabs.
        "close_prefab_stage": ToolClass.VALIDATE,
    }),
    "manage_material": ToolPolicy(ToolClass.WRITE, {
        "ping": ToolClass.READ,
        "get_material_info": ToolClass.READ,
    }),
    "manage_texture": ToolPolicy(ToolClass.WRITE, {
        "delete": ToolClass.DESTRUCTIVE,
    }),
    "manage_shader": ToolPolicy(ToolClass.WRITE, {
        "read": ToolClass.READ,
        "delete": ToolClass.DESTRUCTIVE,
    }),
    "manage_scriptable_object": ToolPolicy(ToolClass.WRITE),

    # --- Scripts ---
    "manage_script": ToolPolicy(ToolClass.WRITE, {
        "read": ToolClass.READ,
        "delete": ToolClass.DESTRUCTIVE,
    }),
    "create_script": ToolPolicy(ToolClass.WRITE),
    "apply_text_edits": ToolPolicy(ToolClass.WRITE),
    "script_apply_edits": ToolPolicy(ToolClass.WRITE),
    "delete_script": ToolPolicy(ToolClass.DESTRUCTIVE),

    # --- Editor state / settings ---
    "manage_editor": ToolPolicy(ToolClass.WRITE, {
        "telemetry_status": ToolClass.READ,
        "telemetry_ping": ToolClass.READ,
        "play": ToolClass.VALIDATE,
        "pause": ToolClass.VALIDATE,
        "stop": ToolClass.VALIDATE,
        "set_active_tool": ToolClass.VALIDATE,
        # Overwrites installed package files and triggers a recompile.
        "deploy_package": ToolClass.DESTRUCTIVE,
        "restore_package": ToolClass.DESTRUCTIVE,
    }),
    "manage_physics": ToolPolicy(ToolClass.WRITE, {
        "ping": ToolClass.READ,
        "get_settings": ToolClass.READ,
        "get_collision_matrix": ToolClass.READ,
        "raycast": ToolClass.READ,
        "raycast_all": ToolClass.READ,
        "linecast": ToolClass.READ,
        "shapecast": ToolClass.READ,
        "overlap": ToolClass.READ,
        "validate": ToolClass.READ,
        "get_rigidbody": ToolClass.READ,
    }),
    "manage_graphics": ToolPolicy(ToolClass.WRITE, {
        "ping": ToolClass.READ,
        "volume_get_info": ToolClass.READ,
        "volume_list_effects": ToolClass.READ,
        "stats_get": ToolClass.READ,
        "stats_list_counters": ToolClass.READ,
        "stats_get_memory": ToolClass.READ,
        "pipeline_get_info": ToolClass.READ,
        "pipeline_get_settings": ToolClass.READ,
        "feature_list": ToolClass.READ,
        "skybox_get": ToolClass.READ,
        "bake_status": ToolClass.READ,
        # bake_get_settings creates+assigns a LightingSettings asset when the
        # scene has none (EnsureLightingSettings), so it can mutate → WRITE.
        "bake_get_settings": ToolClass.WRITE,
    }),
    # NOTE: manage_camera is dispatched through _classify_manage_camera (a
    # payload-aware branch in classify_call), so the capture/analysis actions are
    # classified there. This flat table still supplies the READ overrides and the
    # WRITE default for the configuration actions.
    "manage_camera": ToolPolicy(ToolClass.WRITE, {
        "ping": ToolClass.READ,
        "get_brain_status": ToolClass.READ,
        "list_cameras": ToolClass.READ,
        # visibility_report is a zero-pixel read; classified in _classify_manage_camera.
        "visibility_report": ToolClass.READ,
        # Screenshots (incl. screenshot_compare) create new image files but never
        # damage existing state → VALIDATE (see _classify_manage_camera).
        "screenshot": ToolClass.VALIDATE,
        "screenshot_multiview": ToolClass.VALIDATE,
        "screenshot_compare": ToolClass.VALIDATE,
    }),
    "manage_ui": ToolPolicy(ToolClass.WRITE, {
        "ping": ToolClass.READ,
        "read": ToolClass.READ,
        "get_visual_tree": ToolClass.READ,
        "list": ToolClass.READ,
        # render_ui persists a RenderTexture asset and can create/dirty a
        # PanelSettings asset, so it is a WRITE, not a read.
        "render_ui": ToolClass.WRITE,
        "delete": ToolClass.DESTRUCTIVE,
    }),
    "manage_animation": ToolPolicy(ToolClass.WRITE, {
        "ping": ToolClass.READ,
        "animator_get_info": ToolClass.READ,
        "animator_get_parameter": ToolClass.READ,
        "controller_get_info": ToolClass.READ,
        "clip_get_info": ToolClass.READ,
    }),
    "manage_probuilder": ToolPolicy(ToolClass.WRITE, {
        "ping": ToolClass.READ,
        "get_mesh_info": ToolClass.READ,
        "validate_mesh": ToolClass.READ,
    }),
    "manage_vfx": ToolPolicy(ToolClass.WRITE, {
        "ping": ToolClass.READ,
        "particle_get_info": ToolClass.READ,
        "vfx_get_info": ToolClass.READ,
        "vfx_list_templates": ToolClass.READ,
        "vfx_list_assets": ToolClass.READ,
        "line_get_info": ToolClass.READ,
        "trail_get_info": ToolClass.READ,
    }),

    # --- Packages: install/remove executes package code on import ---
    "manage_packages": ToolPolicy(ToolClass.WRITE, {
        "ping": ToolClass.READ,
        "status": ToolClass.READ,
        "list_packages": ToolClass.READ,
        "search_packages": ToolClass.READ,
        "get_package_info": ToolClass.READ,
        "list_registries": ToolClass.READ,
        "add_package": ToolClass.DESTRUCTIVE,
        "remove_package": ToolClass.DESTRUCTIVE,
        "embed_package": ToolClass.DESTRUCTIVE,
        "add_registry": ToolClass.DESTRUCTIVE,
        "remove_registry": ToolClass.DESTRUCTIVE,
    }),

    # --- Builds: run arbitrary build hooks and write anywhere on disk ---
    "manage_build": ToolPolicy(ToolClass.WRITE, {
        "status": ToolClass.READ,
        "platform": ToolClass.READ,
        "settings": ToolClass.READ,
        "scenes": ToolClass.READ,
        "profiles": ToolClass.READ,
        "build": ToolClass.DESTRUCTIVE,
        "batch": ToolClass.DESTRUCTIVE,
    }),

    # --- Arbitrary execution surfaces ---
    "execute_menu_item": ToolPolicy(ToolClass.DESTRUCTIVE),
    "execute_code": ToolPolicy(ToolClass.DESTRUCTIVE, {
        "get_history": ToolClass.READ,
        "clear_history": ToolClass.WRITE,
    }),
    # Project-authored custom tools: cannot be classified statically.
    "execute_custom_tool": ToolPolicy(ToolClass.WRITE),

    # --- Asset generation (external APIs + asset writes) ---
    "generate_image": ToolPolicy(ToolClass.WRITE, {
        "status": ToolClass.READ,
        "list_providers": ToolClass.READ,
    }),
    "generate_model": ToolPolicy(ToolClass.WRITE, {
        "status": ToolClass.READ,
        "list_providers": ToolClass.READ,
    }),
    "import_model": ToolPolicy(ToolClass.WRITE, {
        "search": ToolClass.READ,
        "preview": ToolClass.READ,
        "status": ToolClass.READ,
        "list_providers": ToolClass.READ,
    }),
    "import_model_file": ToolPolicy(ToolClass.WRITE),
}


@dataclass(frozen=True)
class Classification:
    tool_class: ToolClass
    action: str | None
    known_tool: bool


# Guard against pathological nesting: a batch nested deeper than this fails
# closed to the most severe class rather than being under-counted.
MAX_BATCH_DEPTH = 6


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    if isinstance(value, (int, float)):
        return value == 1
    return False


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def classify_call(tool_name: str, arguments: dict[str, Any] | None) -> Classification:
    """Classify a tool call by name and (optionally) its 'action' argument.

    batch_execute is classified as the most severe class among its nested
    commands, so a batch cannot smuggle a destructive call past the gate.
    manage_build is payload-aware: several of its actions read or mutate
    depending on which arguments are present.
    """
    args = arguments if isinstance(arguments, dict) else {}

    if tool_name == "batch_execute":
        return _classify_batch(args)

    if tool_name == "manage_build":
        return _classify_manage_build(args)

    if tool_name == "manage_camera":
        return _classify_manage_camera(args)

    if tool_name == "manage_scene":
        return _classify_manage_scene(args)

    if tool_name == "manage_prefabs":
        return _classify_manage_prefabs(args)

    if tool_name == "manage_checkpoint":
        return _classify_manage_checkpoint(args)

    policy = TOOL_POLICIES.get(tool_name)
    action = args.get("action")
    action_str = action.strip().lower() if isinstance(action, str) else None

    if policy is None:
        return Classification(UNKNOWN_TOOL_CLASS, action_str, known_tool=False)

    if action_str and action_str in policy.actions:
        return Classification(policy.actions[action_str], action_str, known_tool=True)

    return Classification(policy.default, action_str, known_tool=True)


def _classify_manage_build(args: dict[str, Any]) -> Classification:
    """Payload-aware classification for manage_build.

    Its ``settings``/``platform``/``scenes``/``profiles`` actions are dual-mode:
    they read when no mutating payload is present and persistently mutate the
    project when one is. Since the classifier keys on the action string, these
    would otherwise be flat-classified READ and slip through read_only — so we
    inspect the mutating argument for exactly these actions. ``value``/``target``/
    ``scenes``/``activate`` have no camelCase spelling (no underscore), so the C#
    ``ToolParams`` snake/camel fallback cannot smuggle the payload past this check.
    """
    action = action_of(args)
    if action == "status":
        cls = ToolClass.READ
    elif action in ("build", "batch"):
        cls = ToolClass.DESTRUCTIVE
    elif action == "settings":
        cls = ToolClass.WRITE if args.get("value") is not None else ToolClass.READ
    elif action == "platform":
        cls = ToolClass.WRITE if _nonempty_str(args.get("target")) else ToolClass.READ
    elif action == "scenes":
        cls = ToolClass.WRITE if args.get("scenes") is not None else ToolClass.READ
    elif action == "profiles":
        cls = ToolClass.WRITE if _is_truthy(args.get("activate")) else ToolClass.READ
    else:
        # cancel, unknown actions, or missing action: default to WRITE.
        cls = ToolClass.WRITE
    return Classification(cls, action, known_tool=True)


def _classify_manage_checkpoint(args: dict[str, Any]) -> Classification:
    """Payload-aware classification for manage_checkpoint.

    Each action has a distinct severity: listing reads manifests (READ), creating
    writes snapshot copies under Library/ (VALIDATE), deleting removes a checkpoint
    directory (WRITE), and restoring OVERWRITES the original scene files on disk and
    discards unsaved changes (DESTRUCTIVE). An unknown/missing action fails closed to
    DESTRUCTIVE so a new or malformed action can never slip past the gate.
    """
    action = action_of(args)
    if action == "list":
        cls = ToolClass.READ
    elif action == "create":
        cls = ToolClass.VALIDATE
    elif action == "delete":
        cls = ToolClass.WRITE
    elif action == "restore":
        cls = ToolClass.DESTRUCTIVE
    else:
        # unknown or missing action: fail closed.
        cls = ToolClass.DESTRUCTIVE
    return Classification(cls, action, known_tool=True)


def _classify_manage_camera(args: dict[str, Any]) -> Classification:
    """Payload-aware classification for manage_camera.

    The visual-audit tier splits the tool by severity:
      - visibility_report enumerates renderers and returns geometry only — a pure
        READ (no artifact, no state change).
      - screenshot / screenshot_multiview / screenshot_compare each capture a frame
        and write an artifact file (a screenshot, and for compare an optional diff
        PNG). Nothing persistent in the project is modified or deleted → VALIDATE.
      - Every other known action keeps the tool's flat class from TOOL_POLICIES
        (READ for ping/get_brain_status/list_cameras, WRITE otherwise).
      - Unknown or missing action fails closed to the most dangerous class this
        tool can reach — WRITE (manage_camera has no destructive action).
    """
    action = action_of(args)
    if action == "visibility_report":
        return Classification(ToolClass.READ, action, known_tool=True)
    if action in ("screenshot", "screenshot_multiview", "screenshot_compare"):
        return Classification(ToolClass.VALIDATE, action, known_tool=True)
    policy = TOOL_POLICIES["manage_camera"]
    if action and action in policy.actions:
        return Classification(policy.actions[action], action, known_tool=True)
    if action and action in _MANAGE_CAMERA_KNOWN_ACTIONS:
        return Classification(policy.default, action, known_tool=True)
    # Unknown or missing action: fail closed to the tool's most dangerous class.
    return Classification(ToolClass.WRITE, action, known_tool=True)


# Non-capture, non-analysis actions the tool accepts. Used so an UNKNOWN action
# still fails closed to WRITE rather than being treated as a known default.
_MANAGE_CAMERA_KNOWN_ACTIONS = frozenset({
    "ping", "ensure_brain", "get_brain_status",
    "create_camera",
    "set_target", "set_priority", "set_lens", "set_body", "set_aim", "set_noise",
    "add_extension", "remove_extension",
    "set_blend", "force_camera", "release_override", "list_cameras",
})


def _classify_manage_scene(args: dict[str, Any]) -> Classification:
    """Payload-aware classification for manage_scene.

    The ``validate`` action is dual-mode: read-only unless ``auto_repair`` is
    set, in which case it removes missing-script components and dirties the
    scene (a WRITE). The C# side reads both ``auto_repair`` and ``autoRepair``,
    so both spellings are checked (belt-and-suspenders; the enforcement layer
    also canonicalises keys before this runs).
    """
    action = action_of(args)
    policy = TOOL_POLICIES["manage_scene"]
    if action == "validate" and (
        _is_truthy(args.get("auto_repair")) or _is_truthy(args.get("autoRepair"))
    ):
        return Classification(ToolClass.WRITE, action, known_tool=True)
    if action and action in policy.actions:
        return Classification(policy.actions[action], action, known_tool=True)
    return Classification(policy.default, action, known_tool=True)


def _classify_manage_prefabs(args: dict[str, Any]) -> Classification:
    """Payload-aware classification for manage_prefabs.

    ``close_prefab_stage`` is a transient VALIDATE (exit the prefab stage)
    unless ``save_before_close`` is set, in which case it persists the prefab
    (a WRITE). Unity reads the flag as ``saveBeforeClose``; both spellings are
    checked (the enforcement layer also canonicalises keys first).
    """
    action = action_of(args)
    policy = TOOL_POLICIES["manage_prefabs"]
    if action == "close_prefab_stage" and (
        _is_truthy(args.get("save_before_close")) or _is_truthy(args.get("saveBeforeClose"))
    ):
        return Classification(ToolClass.WRITE, action, known_tool=True)
    if action and action in policy.actions:
        return Classification(policy.actions[action], action, known_tool=True)
    return Classification(policy.default, action, known_tool=True)


def _classify_batch(args: dict[str, Any], depth: int = 0) -> Classification:
    commands = args.get("commands")
    if not isinstance(commands, list) or not commands:
        # Malformed batches are rejected by the tool itself; classify
        # conservatively so the gate never under-counts.
        return Classification(ToolClass.WRITE, "batch", known_tool=True)

    if depth >= MAX_BATCH_DEPTH:
        # Absurd nesting: fail closed to the most severe class.
        return Classification(ToolClass.DESTRUCTIVE, "batch", known_tool=True)

    worst = ToolClass.READ
    for command in commands:
        if not isinstance(command, dict):
            nested = Classification(ToolClass.WRITE, None, known_tool=False)
        else:
            nested_tool = command.get("tool")
            nested_params = command.get("params")
            np = nested_params if isinstance(nested_params, dict) else {}
            if not isinstance(nested_tool, str):
                nested = Classification(ToolClass.WRITE, None, known_tool=False)
            elif nested_tool == "batch_execute":
                # Recurse so a destructive action buried in a nested batch
                # propagates its class outward (do NOT downgrade to WRITE).
                nested = _classify_batch(np, depth + 1)
            else:
                nested = classify_call(nested_tool, np)
        if _CLASS_SEVERITY[nested.tool_class] > _CLASS_SEVERITY[worst]:
            worst = nested.tool_class

    return Classification(worst, "batch", known_tool=True)


@dataclass(frozen=True)
class SafetyDecision:
    decision: Decision
    mode: SafetyMode
    classification: Classification
    reason: str


def evaluate_call(
    mode: SafetyMode,
    tool_name: str,
    arguments: dict[str, Any] | None,
    confirmed: bool = False,
) -> SafetyDecision:
    """Decide whether a tool call may proceed under the given safety mode."""
    classification = classify_call(tool_name, arguments)
    tool_class = classification.tool_class
    call_desc = tool_name if classification.action is None else (
        f"{tool_name}(action='{classification.action}')"
    )

    if tool_class not in _ALLOWED_CLASSES[mode]:
        allowed = ", ".join(
            sorted(c.value for c in _ALLOWED_CLASSES[mode]))
        return SafetyDecision(
            Decision.BLOCK, mode, classification,
            f"Safety mode '{mode.value}' blocks this call: {call_desc} is "
            f"classified as '{tool_class.value}' (allowed classes: {allowed}). "
            "The server operator can change the mode via UNITY_MCP_SAFETY_MODE "
            "or --safety-mode.",
        )

    if tool_class is ToolClass.DESTRUCTIVE and not confirmed:
        return SafetyDecision(
            Decision.CONFIRM_REQUIRED, mode, classification,
            f"{call_desc} is classified as destructive (it may delete data, "
            "execute code, run a build, or install packages). Re-send the "
            'same call with "confirm": true to proceed.',
        )

    return SafetyDecision(Decision.ALLOW, mode, classification, "allowed")


def parse_safety_mode(value: str) -> SafetyMode:
    """Parse a safety mode string (case-insensitive, '-' tolerated)."""
    normalized = value.strip().lower().replace("-", "_")
    try:
        return SafetyMode(normalized)
    except ValueError:
        valid = ", ".join(m.value for m in SafetyMode)
        raise ValueError(
            f"Invalid safety mode '{value}'. Valid modes: {valid}.") from None


def get_safety_mode() -> SafetyMode:
    """Read the active safety mode from server config, failing closed."""
    from core.config import config
    raw = getattr(config, "safety_mode", SafetyMode.WRITE.value)
    if isinstance(raw, SafetyMode):
        return raw
    try:
        return parse_safety_mode(str(raw))
    except ValueError:
        logger.warning(
            "Invalid safety_mode %r in config; failing closed to read_only", raw)
        return SafetyMode.READ_ONLY


def describe_safety_posture() -> dict[str, Any]:
    """Return the server's active safety posture as stable machine-readable JSON.

    Exposed to agents (via the safety_status tool and the server/safety resource)
    so they can see what they are allowed to do BEFORE a call is refused, instead
    of discovering the mode only from a block. Pure read of config + safety state;
    performs no Unity call.
    """
    from core.config import config
    from core.audit import audit_enabled, resolve_audit_path

    mode = get_safety_mode()
    allowed = sorted(c.value for c in _ALLOWED_CLASSES[mode])
    extra = getattr(config, "menu_item_allowlist", None) or []
    menu_allowlist = sorted(
        set(DEFAULT_MENU_ITEM_ALLOWLIST)
        | {str(item).strip() for item in extra if str(item).strip()}
    )
    enabled = audit_enabled()
    audit_path = None
    if enabled:
        try:
            audit_path = resolve_audit_path()
        except Exception:  # pragma: no cover - defensive
            audit_path = None

    return {
        "mode": mode.value,
        "allowed_classes": allowed,
        "destructive_requires_confirm": mode is SafetyMode.WRITE,
        "execute_code_enabled": bool(getattr(config, "allow_execute_code", False)),
        "arbitrary_menu_items_enabled": bool(
            getattr(config, "allow_arbitrary_menu_items", False)),
        "menu_item_allowlist": menu_allowlist,
        "external_build_output_allowed": bool(
            getattr(config, "allow_external_build_output", False)),
        "audit_log_enabled": enabled,
        "audit_log_path": audit_path,
        "confirm_semantics": (
            "confirm:true is destructive-operation acknowledgement metadata, not "
            "human confirmation; it never relaxes the safety mode."
        ),
    }


# ---------------------------------------------------------------------------
# Tool-specific guards (pure functions; config is passed in by the caller)
#
# These run *in addition to* the mode/class checks above and apply even in
# write mode. They are the per-surface hardening for the most dangerous tools:
# execute_code (un-sandboxed), execute_menu_item (arbitrary menu execution),
# and manage_build (arbitrary filesystem output).
# ---------------------------------------------------------------------------

def action_of(arguments: dict[str, Any] | None) -> str | None:
    """Return the normalized (lower, stripped) 'action' argument, if present."""
    if not isinstance(arguments, dict):
        return None
    action = arguments.get("action")
    return action.strip().lower() if isinstance(action, str) else None


# Actions of execute_code that actually compile+run C# in the Editor process.
EXECUTE_CODE_RUN_ACTIONS = frozenset({"execute", "replay"})


def execute_code_guard(arguments: dict[str, Any] | None, *, allow: bool) -> str | None:
    """Block execute_code's code-running actions unless explicitly enabled.

    Returns an error reason when the call must be blocked, else None. Only the
    code-running actions (execute/replay) are gated; history reads/clears are
    harmless and left to the ordinary class/mode checks.
    """
    action = action_of(arguments)
    # action is required on execute_code; treat a missing action as the
    # dangerous default rather than assuming a benign one.
    is_run = action is None or action in EXECUTE_CODE_RUN_ACTIONS
    if is_run and not allow:
        return (
            "execute_code is disabled. It compiles and runs arbitrary C# in the "
            "Unity Editor process and is NOT sandboxed — its in-editor blocklist "
            "is bypassable. Enable it only in a trusted environment by setting "
            "allow_execute_code (UNITY_MCP_ALLOW_EXECUTE_CODE=1)."
        )
    return None


# A conservative, genuinely-safe default set: these menu items only open or
# focus Editor windows and cannot mutate or delete project data. Everything
# else is denied unless the operator opts in.
DEFAULT_MENU_ITEM_ALLOWLIST: frozenset[str] = frozenset({
    "Window/General/Console",
    "Window/General/Hierarchy",
    "Window/General/Inspector",
    "Window/General/Project",
    "Window/General/Scene",
    "Window/General/Game",
    "Window/Analysis/Profiler",
})


# Both spellings Unity's ExecuteMenuItem handler accepts. The Python guard and
# the C# handler read these in OPPOSITE precedence, so the guard must validate
# EVERY provided spelling — otherwise an attacker sends an allowlisted value
# under the guard's preferred key and a dangerous value under the other, and
# the C# side runs the dangerous one. See adversarial-review finding #6.
MENU_PATH_KEYS = ("menuPath", "menu_path")


def menu_item_values(arguments: dict[str, Any] | None) -> list[str]:
    """Return every menu path present under any accepted key spelling."""
    if not isinstance(arguments, dict):
        return []
    values: list[str] = []
    for key in MENU_PATH_KEYS:
        raw = arguments.get(key)
        if isinstance(raw, str) and raw.strip():
            values.append(raw.strip())
    return values


def menu_item_arg(arguments: dict[str, Any] | None) -> str | None:
    values = menu_item_values(arguments)
    return values[0] if values else None


def execute_menu_item_guard(
    arguments: dict[str, Any] | None,
    *,
    allowlist: frozenset[str] | set[str],
    allow_arbitrary: bool,
) -> str | None:
    """Allow only allowlisted menu paths unless arbitrary execution is enabled.

    EVERY provided path spelling must be allowlisted — if any is not, the call
    is refused. Returns an error reason when blocked, else None.
    """
    if allow_arbitrary:
        return None
    values = menu_item_values(arguments)
    if not values:
        # No path to execute: still block by default so an empty/unknown call
        # can't slip through when arbitrary execution is off.
        return (
            "execute_menu_item requires an explicit, allowlisted menu path. "
            "Arbitrary menu execution is disabled."
        )
    for menu_path in values:
        if menu_path not in allowlist:
            return (
                f"Menu path '{menu_path}' is not on the execute_menu_item allowlist. "
                "Add it via menu_item_allowlist (UNITY_MCP_MENU_ITEM_ALLOWLIST=\"...\") "
                "or, to permit any menu item, set allow_arbitrary_menu_items "
                "(UNITY_MCP_ALLOW_ARBITRARY_MENU_ITEMS=1 — not recommended)."
            )
    return None


_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


def _looks_absolute_or_external(raw: str) -> bool:
    """Host-independent check for absolute/external paths.

    Detects POSIX roots, Windows drive letters, UNC paths, and ~ home
    expansion regardless of the OS the server runs on, so a Windows-style
    path is rejected even when the server runs on Linux and vice versa.
    """
    r = raw.strip()
    if not r:
        return False
    if r.startswith("/") or r.startswith("\\"):
        return True
    if _DRIVE_LETTER.match(r):
        return True
    if r.startswith("~"):
        return True
    return False


def _check_build_path(raw: str, *, allow_external: bool) -> str | None:
    normalized = raw.replace("\\", "/")
    segments = normalized.split("/")
    if any(seg == ".." for seg in segments):
        return (
            f"'{raw}' contains a parent-directory ('..') segment; path "
            "traversal outside the project is not allowed."
        )
    if _looks_absolute_or_external(raw) and not allow_external:
        return (
            f"'{raw}' is an absolute/external path. Build output must be a "
            "project-relative path by default. To write builds outside the "
            "project, set allow_external_build_output "
            "(UNITY_MCP_ALLOW_EXTERNAL_BUILD_OUTPUT=1)."
        )
    return None


# Both snake_case and camelCase spellings must be checked: Unity's ToolParams
# resolves output_path AND outputPath (snake/camel fallback), so a guard that
# only inspected the snake key could be bypassed with the camelCase variant.
# See adversarial-review finding #7.
BUILD_PATH_KEYS = ("output_path", "output_dir", "outputPath", "outputDir")


def build_output_guard(arguments: dict[str, Any] | None, *, allow_external: bool) -> str | None:
    """Validate manage_build output paths for traversal/external escapes.

    Returns an error reason when the call must be blocked, else None. Only the
    build-producing actions carry output paths; other actions have none and
    pass through.
    """
    if not isinstance(arguments, dict):
        return None
    for key in BUILD_PATH_KEYS:
        raw = arguments.get(key)
        if isinstance(raw, str) and raw.strip():
            reason = _check_build_path(raw, allow_external=allow_external)
            if reason:
                return f"{key}: {reason}"
    return None
