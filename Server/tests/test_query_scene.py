"""Plumbing tests for the query_scene tool.

These verify the Python side: parameter forwarding/coercion, projection
validation, page_size clamping, and READ safety classification. The actual
scene traversal + projection is C# (QueryScene.cs) and is verified in Unity
EditMode tests.
"""
from unittest.mock import AsyncMock

import pytest

import services.tools.query_scene as qs
from services.tools.query_scene import VALID_INCLUDES, query_scene


class FakeCtx:
    client_id = "test"

    async def get_state(self, key):
        return None


@pytest.fixture
def captured(monkeypatch):
    sent = {}

    async def fake_send(send_fn, unity_instance, command, params):
        sent["command"] = command
        sent["params"] = params
        return {"success": True, "data": {"rows": [], "total_matched": 0}}

    monkeypatch.setattr(qs, "send_with_unity_instance", fake_send)
    return sent


class TestRegistration:
    def test_tool_is_registered(self):
        from services.registry import get_registered_tools
        names = {t["name"] for t in get_registered_tools()}
        assert "query_scene" in names

    def test_tool_is_in_core_group(self):
        from services.registry import get_registered_tools
        tool = next(t for t in get_registered_tools() if t["name"] == "query_scene")
        assert tool["group"] == "core"


class TestFilterForwarding:
    @pytest.mark.asyncio
    async def test_no_filters_dispatches_with_empty_params(self, captured):
        # A filterless call is allowed (server pages the full scene).
        await query_scene(FakeCtx())
        assert captured["command"] == "query_scene"
        assert captured["params"] == {}

    @pytest.mark.asyncio
    async def test_name_contains_forwarded_and_trimmed(self, captured):
        await query_scene(FakeCtx(), name_contains="  Player  ")
        assert captured["params"]["name_contains"] == "Player"

    @pytest.mark.asyncio
    async def test_tag_forwarded(self, captured):
        await query_scene(FakeCtx(), tag="Enemy")
        assert captured["params"]["tag"] == "Enemy"

    @pytest.mark.asyncio
    async def test_layer_name_forwarded(self, captured):
        await query_scene(FakeCtx(), layer="UI")
        assert captured["params"]["layer"] == "UI"

    @pytest.mark.asyncio
    async def test_layer_index_zero_forwarded(self, captured):
        # Layer 0 (Default) is a legitimate value and must not be dropped.
        await query_scene(FakeCtx(), layer=0)
        assert captured["params"]["layer"] == 0

    @pytest.mark.asyncio
    async def test_component_type_forwarded(self, captured):
        await query_scene(FakeCtx(), component_type="UnityEngine.Rigidbody")
        assert captured["params"]["component_type"] == "UnityEngine.Rigidbody"

    @pytest.mark.asyncio
    async def test_root_path_forwarded(self, captured):
        await query_scene(FakeCtx(), root_path="Canvas/Panel")
        assert captured["params"]["root_path"] == "Canvas/Panel"

    @pytest.mark.asyncio
    async def test_scene_forwarded(self, captured):
        await query_scene(FakeCtx(), scene="Main")
        assert captured["params"]["scene"] == "Main"

    @pytest.mark.asyncio
    async def test_include_inactive_coerced(self, captured):
        await query_scene(FakeCtx(), include_inactive="true")
        assert captured["params"]["include_inactive"] is True

    @pytest.mark.asyncio
    async def test_include_inactive_omitted_not_sent(self, captured):
        await query_scene(FakeCtx(), name_contains="X")
        assert "include_inactive" not in captured["params"]

    @pytest.mark.asyncio
    async def test_empty_string_filters_not_sent(self, captured):
        await query_scene(FakeCtx(), name_contains="   ", tag="", layer="  ")
        assert captured["params"] == {}


class TestProjectionValidation:
    @pytest.mark.asyncio
    async def test_include_list_forwarded(self, captured):
        await query_scene(FakeCtx(), include=["transform", "world_bounds"])
        assert captured["params"]["include"] == ["transform", "world_bounds"]

    @pytest.mark.asyncio
    async def test_include_csv_string_forwarded(self, captured):
        await query_scene(FakeCtx(), include="components, materials")
        assert captured["params"]["include"] == ["components", "materials"]

    @pytest.mark.asyncio
    async def test_include_json_string_forwarded(self, captured):
        await query_scene(FakeCtx(), include='["mesh_stats"]')
        assert captured["params"]["include"] == ["mesh_stats"]

    @pytest.mark.asyncio
    async def test_include_dedupes(self, captured):
        await query_scene(FakeCtx(), include=["transform", "transform"])
        assert captured["params"]["include"] == ["transform"]

    @pytest.mark.asyncio
    async def test_unknown_projection_rejected(self, captured):
        result = await query_scene(FakeCtx(), include=["transform", "bogus"])
        assert result["success"] is False
        assert "bogus" in result["message"]
        assert "command" not in captured  # never dispatched

    @pytest.mark.asyncio
    async def test_all_valid_includes_accepted(self, captured):
        await query_scene(FakeCtx(), include=list(VALID_INCLUDES))
        assert captured["params"]["include"] == list(VALID_INCLUDES)

    @pytest.mark.asyncio
    async def test_include_omitted_not_sent(self, captured):
        # Default projection is applied C#-side; Python omits it when unset.
        await query_scene(FakeCtx(), name_contains="X")
        assert "include" not in captured["params"]


class TestPaging:
    @pytest.mark.asyncio
    async def test_page_size_forwarded(self, captured):
        await query_scene(FakeCtx(), page_size=25)
        assert captured["params"]["page_size"] == 25

    @pytest.mark.asyncio
    async def test_page_size_clamped_to_max(self, captured):
        await query_scene(FakeCtx(), page_size=10000)
        assert captured["params"]["page_size"] == 500

    @pytest.mark.asyncio
    async def test_page_size_clamped_to_min(self, captured):
        await query_scene(FakeCtx(), page_size=0)
        assert captured["params"]["page_size"] == 1

    @pytest.mark.asyncio
    async def test_page_size_string_coerced(self, captured):
        await query_scene(FakeCtx(), page_size="30")
        assert captured["params"]["page_size"] == 30

    @pytest.mark.asyncio
    async def test_cursor_forwarded(self, captured):
        await query_scene(FakeCtx(), cursor=100)
        assert captured["params"]["cursor"] == 100

    @pytest.mark.asyncio
    async def test_negative_cursor_clamped_to_zero(self, captured):
        await query_scene(FakeCtx(), cursor=-5)
        assert captured["params"]["cursor"] == 0


class TestResponse:
    @pytest.mark.asyncio
    async def test_non_dict_response_wrapped(self, monkeypatch):
        monkeypatch.setattr(qs, "send_with_unity_instance", AsyncMock(return_value="weird"))
        result = await query_scene(FakeCtx(), name_contains="X")
        assert result["success"] is False
        assert "weird" in result["message"]


class TestClassification:
    def test_query_scene_is_read(self):
        from core.safety import classify_call, ToolClass
        assert classify_call("query_scene", {}).tool_class is ToolClass.READ

    def test_query_scene_allowed_in_read_only(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        assert evaluate_call(SafetyMode.READ_ONLY, "query_scene", {}).decision is Decision.ALLOW
