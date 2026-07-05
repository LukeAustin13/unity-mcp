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
        # get_dependencies/find_references require a target; supply one so the
        # dispatch path is exercised for every action.
        kwargs = {"action": action}
        if action in ("get_dependencies", "find_references"):
            kwargs["target"] = "Assets/Foo.prefab"
        await manage_project(FakeCtx(), **kwargs)
        assert captured["command"] == "manage_project"
        assert captured["params"]["action"] == action

    @pytest.mark.asyncio
    async def test_new_actions_are_listed(self):
        for action in ("get_dependencies", "find_references", "unused_assets"):
            assert action in ALL_ACTIONS

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["get_dependencies", "find_references"])
    async def test_target_required_for_dependency_actions(self, captured, action):
        result = await manage_project(FakeCtx(), action=action)
        assert result["success"] is False
        assert "target" in result["message"].lower()
        assert "command" not in captured  # never dispatched

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ["get_dependencies", "find_references"])
    async def test_blank_target_rejected(self, captured, action):
        result = await manage_project(FakeCtx(), action=action, target="   ")
        assert result["success"] is False
        assert "command" not in captured

    @pytest.mark.asyncio
    async def test_unused_assets_does_not_require_target(self, captured):
        await manage_project(FakeCtx(), action="unused_assets")
        assert captured["params"]["action"] == "unused_assets"
        assert "target" not in captured["params"]

    @pytest.mark.asyncio
    async def test_audit_mobile_is_listed(self):
        assert "audit_mobile" in ALL_ACTIONS

    @pytest.mark.asyncio
    async def test_audit_mobile_does_not_require_target(self, captured):
        await manage_project(FakeCtx(), action="audit_mobile")
        assert captured["params"]["action"] == "audit_mobile"
        assert "target" not in captured["params"]

    @pytest.mark.asyncio
    async def test_audit_mobile_forwards_paging(self, captured):
        await manage_project(
            FakeCtx(), action="audit_mobile", folder_scope="Assets/Art",
            page_size=50, cursor="100", max_issues=25,
        )
        p = captured["params"]
        assert p["action"] == "audit_mobile"
        assert p["folder_scope"] == ["Assets/Art"]
        assert p["page_size"] == 50
        assert p["cursor"] == "100"
        assert p["max_issues"] == 25

    @pytest.mark.asyncio
    async def test_prefab_health_is_listed(self):
        assert "prefab_health" in ALL_ACTIONS

    @pytest.mark.asyncio
    async def test_prefab_health_dispatches_without_target(self, captured):
        await manage_project(FakeCtx(), action="prefab_health")
        assert captured["params"]["action"] == "prefab_health"
        assert "target" not in captured["params"]

    @pytest.mark.asyncio
    async def test_prefab_health_forwards_single_prefab_params(self, captured):
        await manage_project(
            FakeCtx(), action="prefab_health",
            prefab_path="  Assets/Foo.prefab  ", include_variants="false",
            page_size=25, cursor="50", max_issues=100,
        )
        p = captured["params"]
        assert p["action"] == "prefab_health"
        assert p["prefab_path"] == "Assets/Foo.prefab"  # trimmed
        assert p["include_variants"] is False
        assert p["page_size"] == 25
        assert p["cursor"] == "50"
        assert p["max_issues"] == 100

    @pytest.mark.asyncio
    async def test_prefab_health_forwards_folder(self, captured):
        await manage_project(FakeCtx(), action="prefab_health", folder="Assets/Prefabs")
        p = captured["params"]
        assert p["action"] == "prefab_health"
        assert p["folder"] == "Assets/Prefabs"

    @pytest.mark.asyncio
    async def test_prefab_health_omits_unset_params(self, captured):
        await manage_project(FakeCtx(), action="prefab_health")
        # Nothing beyond action when no prefab-health params supplied.
        assert set(captured["params"].keys()) == {"action"}


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
    async def test_target_forwarded_and_trimmed(self, captured):
        await manage_project(FakeCtx(), action="get_dependencies", target="  Assets/A.prefab  ")
        assert captured["params"]["target"] == "Assets/A.prefab"

    @pytest.mark.asyncio
    async def test_guid_target_forwarded(self, captured):
        guid = "abcdef0123456789abcdef0123456789"
        await manage_project(FakeCtx(), action="find_references", target=guid)
        assert captured["params"]["target"] == guid

    @pytest.mark.asyncio
    async def test_recursive_bool_coercion(self, captured):
        await manage_project(
            FakeCtx(), action="get_dependencies", target="Assets/A.prefab", recursive="true"
        )
        assert captured["params"]["recursive"] is True

    @pytest.mark.asyncio
    async def test_recursive_omitted_not_sent(self, captured):
        await manage_project(FakeCtx(), action="get_dependencies", target="Assets/A.prefab")
        assert "recursive" not in captured["params"]

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

    def test_prefab_health_is_read_and_allowed_in_read_only(self):
        from core.safety import classify_call, evaluate_call, ToolClass, SafetyMode, Decision
        assert classify_call("manage_project", {"action": "prefab_health"}).tool_class is ToolClass.READ
        assert evaluate_call(SafetyMode.READ_ONLY, "manage_project", {"action": "prefab_health"}).decision is Decision.ALLOW
