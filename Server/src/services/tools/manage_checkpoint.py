"""Named scene-state checkpoints — snapshot, list, restore, and delete.

A checkpoint copies each currently-loaded scene that has a saved path into
Library/McpCheckpoints/<id>/, so an AI agent can snapshot the scene, experiment,
and roll back.

Safety classes (see core.safety):
- list:    READ (reads manifests only).
- create:  VALIDATE (writes snapshot copies under Library/, no project mutation).
- delete:  WRITE (removes a checkpoint directory).
- restore: DESTRUCTIVE — it OVERWRITES the original scene files on disk with the
  checkpointed state and discards current unsaved scene changes.
"""
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

ALL_ACTIONS = ["create", "list", "restore", "delete"]


async def _send_checkpoint_command(ctx: Context, params_dict: dict[str, Any]) -> dict[str, Any]:
    unity_instance = await get_unity_instance_from_context(ctx)
    result = await send_with_unity_instance(
        async_send_command_with_retry, unity_instance, "manage_checkpoint", params_dict
    )
    return result if isinstance(result, dict) else {"success": False, "message": str(result)}


@mcp_for_unity_tool(
    group="core",
    description=(
        "Named scene-state checkpoints — snapshot the loaded scenes to disk so you "
        "can experiment and roll back. Actions:\n"
        "- create: snapshot every currently-loaded scene that has a saved path into "
        "Library/McpCheckpoints/<id>/. Provide an optional 'label' (must match "
        "^[A-Za-z0-9_-]{1,64}$) to name the checkpoint; otherwise a server id is "
        "generated. Untitled/never-saved scenes are NOT supported in v1 and are "
        "returned in a 'skipped_untitled' list. Capped at 20 checkpoints.\n"
        "- list: id/label/createdUtc/scenes for every checkpoint.\n"
        "- restore: roll back to a checkpoint by 'id'. WARNING: this OVERWRITES the "
        "original scene files on disk with the checkpointed state and DISCARDS current "
        "unsaved scene changes. Refused in play mode or while compiling.\n"
        "- delete: remove a checkpoint directory by 'id'.\n"
        "Checkpoint ids are server-generated or a sanitized label; no filesystem paths "
        "are accepted and all file operations stay inside Library/McpCheckpoints."
    ),
    annotations=ToolAnnotations(
        title="Manage Checkpoint",
        destructiveHint=True,
    ),
)
async def manage_checkpoint(
    ctx: Context,
    action: Annotated[
        Literal["create", "list", "restore", "delete"],
        "Checkpoint operation to perform.",
    ],
    id: Annotated[
        str | None,
        "Checkpoint id for restore/delete (from create or list). Server-generated "
        "or a sanitized label; must match ^[A-Za-z0-9_-]{1,64}$.",
    ] = None,
    label: Annotated[
        str | None,
        "For create: optional name used as the checkpoint id. Must match "
        "^[A-Za-z0-9_-]{1,64}$ (letters, digits, '_' and '-').",
    ] = None,
) -> dict[str, Any]:
    action_lower = action.lower() if isinstance(action, str) else action
    if action_lower not in ALL_ACTIONS:
        return {
            "success": False,
            "message": f"Unknown action '{action}'. Valid actions: {', '.join(ALL_ACTIONS)}",
        }

    params_dict: dict[str, Any] = {"action": action_lower}

    param_map: dict[str, Any] = {
        "id": id,
        "label": label,
    }
    for key, val in param_map.items():
        if val is not None:
            params_dict[key] = val

    return await _send_checkpoint_command(ctx, params_dict)
