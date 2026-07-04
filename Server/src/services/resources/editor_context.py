from typing import Any

from fastmcp import Context
from pydantic import BaseModel

from models import MCPResponse
from models.unity_response import parse_resource_response
from services.registry import mcp_for_unity_resource
from services.tools import get_unity_instance_from_context
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry


class EditorContextSelection(BaseModel):
    """Summary of the current editor selection."""
    count: int = 0
    activeGameObject: str | None = None
    activeInstanceID: int = 0
    assetGUIDCount: int = 0


class EditorContextPrefabStage(BaseModel):
    """Whether a prefab stage is open and, if so, its asset path."""
    isOpen: bool = False
    assetPath: str | None = None


class EditorContextConsole(BaseModel):
    """Console counts by log type."""
    errors: int = 0
    warnings: int = 0
    logs: int = 0


class EditorContextData(BaseModel):
    """One-read situational-awareness snapshot fields."""
    editorState: Any | None = None
    selection: EditorContextSelection | None = None
    prefabStage: EditorContextPrefabStage | None = None
    console: EditorContextConsole | None = None


class EditorContextResponse(MCPResponse):
    """Union of editor state, selection, prefab stage, and console counts."""
    data: EditorContextData = EditorContextData()


@mcp_for_unity_resource(
    uri="mcpforunity://editor/context",
    name="editor_context",
    description="One-read situational-awareness snapshot: editor state, selection summary, prefab stage, and console counts by type. Read this once at task start instead of editor/state, editor/selection, and editor/prefab-stage separately.\n\nURI: mcpforunity://editor/context"
)
async def get_editor_context(ctx: Context) -> EditorContextResponse | MCPResponse:
    """Get a combined editor situational-awareness snapshot."""
    unity_instance = await get_unity_instance_from_context(ctx)
    response = await send_with_unity_instance(
        async_send_command_with_retry,
        unity_instance,
        "get_editor_context",
        {}
    )
    return parse_resource_response(response, EditorContextResponse)
