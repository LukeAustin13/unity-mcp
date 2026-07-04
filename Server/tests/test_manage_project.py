"""Plumbing tests for the manage_project tool.

These verify the Python side: action validation, parameter forwarding/coercion,
paging passthrough, and READ safety classification. The actual whole-project
scan is C# (ManageProject.cs) and is verified in Unity EditMode tests.
"""
from unittest.mock import AsyncMock

import pytest

import services.tools.manage_project as mp
from services.tools.manage_project import ALL_ACTIONS, manage_project


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
        return {"success": True, "data": {"ok": True}}

    monkeypatch.setattr(mp, "send_with_unity_instance", fake_send)
    return sent


class TestActionValidation:
    @pytest.mark.asyncio
    async def test_unknown_action_rejected(self, captured):
        result = await manage_project(FakeCtx(), action="nope")
        assert result["success"] is False
        assert "Unknown action" in result["message"]
        assert "command" not in captured  # never dispatched

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ALL_ACTIONS)
    async def test_valid_actions_dispatch(self, captured, action):
        await manage_project(FakeCtx(), action=action)
        assert captured["command"] == "manage_project"
        assert captured["params"]["action"] == action


class TestParameterForwarding:
    @pytest.mark.asyncio
    async def test_scope_coercion_from_csv(self, captured):
        await manage_project(FakeCtx(), action="validate_assets", folder_scope="Assets/A, Assets/B")
        assert captured["params"]["folder_scope"] == ["Assets/A", "Assets/B"]

    @pytest.mark.asyncio
    async def test_scope_coercion_from_json(self, captured):
        await manage_project(FakeCtx(), action="validate_assets", folder_scope='["Assets/X"]')
        assert captured["params"]["folder_scope"] == ["Assets/X"]

    @pytest.mark.asyncio
    async def test_scope_list_passthrough(self, captured):
        await manage_project(FakeCtx(), action="asset_inventory", folder_scope=["Assets/Y"])
        assert captured["params"]["folder_scope"] == ["Assets/Y"]

    @pytest.mark.asyncio
    async def test_bool_coercion(self, captured):
        await manage_project(FakeCtx(), action="validate_assets", include_packages="true", include_guids=False)
        assert captured["params"]["include_packages"] is True
        # include_guids False is not None, so it is forwarded
        assert captured["params"]["include_guids"] is False

    @pytest.mark.asyncio
    async def test_paging_params_forwarded(self, captured):
        await manage_project(FakeCtx(), action="validate_assets", page_size=50, cursor="abc", max_issues=100)
        p = captured["params"]
        assert p["page_size"] == 50
        assert p["cursor"] == "abc"
        assert p["max_issues"] == 100

    @pytest.mark.asyncio
    async def test_issue_types_filter(self, captured):
        await manage_project(FakeCtx(), action="validate_assets", issue_types="missing_scripts,dangling_reference")
        assert captured["params"]["issue_types"] == ["missing_scripts", "dangling_reference"]

    @pytest.mark.asyncio
    async def test_omitted_params_not_sent(self, captured):
        await manage_project(FakeCtx(), action="project_health")
        # Only action should be present when nothing else is provided.
        assert set(captured["params"].keys()) == {"action"}

    @pytest.mark.asyncio
    async def test_non_dict_response_wrapped(self, monkeypatch):
        monkeypatch.setattr(mp, "send_with_unity_instance", AsyncMock(return_value="weird"))
        result = await manage_project(FakeCtx(), action="project_health")
        assert result["success"] is False
        assert "weird" in result["message"]


class TestClassification:
    def test_manage_project_is_read(self):
        from core.safety import classify_call, ToolClass
        for action in ALL_ACTIONS:
            assert classify_call("manage_project", {"action": action}).tool_class is ToolClass.READ

    def test_allowed_in_read_only(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        assert evaluate_call(SafetyMode.READ_ONLY, "manage_project", {"action": "project_health"}).decision is Decision.ALLOW
