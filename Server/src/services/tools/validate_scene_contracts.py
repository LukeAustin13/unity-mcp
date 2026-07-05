"""validate_scene_contracts — READ-only scene-contract validator.

A project declares, as JSON, what a scene MUST and MUST NOT contain (required /
forbidden objects, required components, camera/light caps, tags/layers,
build-settings membership, UI wiring). This tool validates the CURRENTLY-LOADED
scene against that contract and returns structured pass/fail findings.

Strictly READ: it never opens/closes/saves/modifies scenes, never touches assets
or settings, and never enters play mode. The heavy lifting (scene traversal, rule
evaluation, the contract-file guards) is C# (ValidateSceneContracts.cs); this
module validates/forwards parameters. Fails closed — supplying both or neither of
`contract` / `contract_path` is an error before anything reaches Unity.
"""
from typing import Annotated, Any, Optional

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


@mcp_for_unity_tool(
    group="core",
    description=(
        "Validate a LOADED scene against a project-defined JSON contract (READ; "
        "opens nothing, mutates nothing, no play mode). Provide EXACTLY ONE of "
        "`contract` (inline JSON object) or `contract_path` (project-relative .json "
        "path, read Unity-side). Optional `scene` targets a loaded scene by name "
        "(must already be open — this tool never opens one); default is the active "
        "scene.\n"
        "Supported contract keys (unknown keys are REJECTED so a typo can't pass): "
        "required_objects (path or bare-name list that must exist), forbidden_objects "
        "(must NOT exist), required_components ([{object, components}]), max_cameras / "
        "max_lights (int caps on enabled Camera/Light), forbid_missing_references "
        "(bool; scans for missing scripts + dangling refs), required_tags "
        "([{object, tag}]), required_layers ([{object, layer}]), "
        "require_in_build_settings (bool; scene present AND enabled), ui "
        "({require_event_system?, require_graphic_raycaster?}).\n"
        "Returns {passed, scene, findings: [{rule, status, severity, object_path?, "
        "details, suggested_fix?}], summary: {rules_evaluated, failed, by_rule}, "
        "caveats: [...]}. passed is true only when zero findings failed; a full "
        "pass/fail checklist is emitted, one finding per rule-entry."
    ),
    annotations=ToolAnnotations(
        title="Validate Scene Contracts",
        readOnlyHint=True,
    ),
)
async def validate_scene_contracts(
    ctx: Context,
    contract: Annotated[
        Optional[dict],
        "Inline contract as a JSON object. Provide this OR contract_path, not both.",
    ] = None,
    contract_path: Annotated[
        Optional[str],
        "Project-relative path to a .json contract file (read Unity-side). "
        "Provide this OR contract, not both.",
    ] = None,
    scene: Annotated[
        Optional[str],
        "Name of a LOADED scene to validate. Omit to use the active scene. "
        "The scene must already be open; this tool never opens one.",
    ] = None,
) -> dict[str, Any]:
    has_contract = contract is not None
    has_path = isinstance(contract_path, str) and bool(contract_path.strip())

    if has_contract and has_path:
        return {
            "success": False,
            "message": (
                "Provide exactly one of 'contract' (inline JSON) or 'contract_path' "
                "(project-relative path), not both."
            ),
        }
    if not has_contract and not has_path:
        return {
            "success": False,
            "message": (
                "A contract is required. Provide either 'contract' (inline JSON object) "
                "or 'contract_path' (project-relative .json path)."
            ),
        }

    if has_contract and not isinstance(contract, dict):
        return {"success": False, "message": "'contract' must be a JSON object."}

    params: dict[str, Any] = {}
    if has_contract:
        params["contract"] = contract
    if has_path:
        params["contract_path"] = contract_path.strip()

    scene_clean = scene.strip() if isinstance(scene, str) else None
    if scene_clean:
        params["scene"] = scene_clean

    unity_instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "validate_scene_contracts", params
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}
