"""Plumbing tests for the manage_checkpoint tool.

These verify the Python side: action validation, parameter forwarding, safety
classification for every action, unknown/missing-action fail-closed behaviour,
snake_case/camelCase action-key canonicalisation, and CLI registration. The
actual snapshot/restore is C# (ManageCheckpoint.cs) and is verified in Unity
EditMode tests.
"""
from unittest.mock import AsyncMock

import pytest

import services.tools.manage_checkpoint as mc
from services.tools.manage_checkpoint import ALL_ACTIONS, manage_checkpoint


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

    monkeypatch.setattr(mc, "send_with_unity_instance", fake_send)
    return sent


class TestActionValidation:
    @pytest.mark.asyncio
    async def test_unknown_action_rejected(self, captured):
        result = await manage_checkpoint(FakeCtx(), action="nope")
        assert result["success"] is False
        assert "Unknown action" in result["message"]
        assert "command" not in captured  # never dispatched

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action", ALL_ACTIONS)
    async def test_valid_actions_dispatch(self, captured, action):
        await manage_checkpoint(FakeCtx(), action=action)
        assert captured["command"] == "manage_checkpoint"
        assert captured["params"]["action"] == action


class TestParameterForwarding:
    @pytest.mark.asyncio
    async def test_label_forwarded_on_create(self, captured):
        await manage_checkpoint(FakeCtx(), action="create", label="my_cp-1")
        assert captured["params"]["label"] == "my_cp-1"
        assert captured["params"]["action"] == "create"

    @pytest.mark.asyncio
    async def test_id_forwarded_on_restore(self, captured):
        await manage_checkpoint(FakeCtx(), action="restore", id="cp_123")
        assert captured["params"]["id"] == "cp_123"

    @pytest.mark.asyncio
    async def test_id_forwarded_on_delete(self, captured):
        await manage_checkpoint(FakeCtx(), action="delete", id="cp_123")
        assert captured["params"]["id"] == "cp_123"

    @pytest.mark.asyncio
    async def test_omitted_params_not_sent(self, captured):
        await manage_checkpoint(FakeCtx(), action="list")
        assert set(captured["params"].keys()) == {"action"}

    @pytest.mark.asyncio
    async def test_non_dict_response_wrapped(self, monkeypatch):
        monkeypatch.setattr(mc, "send_with_unity_instance", AsyncMock(return_value="weird"))
        result = await manage_checkpoint(FakeCtx(), action="list")
        assert result["success"] is False
        assert "weird" in result["message"]


class TestClassification:
    def test_per_action_classes(self):
        from core.safety import classify_call, ToolClass

        expected = {
            "list": ToolClass.READ,
            "create": ToolClass.VALIDATE,
            "delete": ToolClass.WRITE,
            "restore": ToolClass.DESTRUCTIVE,
        }
        for action, cls in expected.items():
            assert classify_call("manage_checkpoint", {"action": action}).tool_class is cls

    def test_unknown_action_fails_closed(self):
        from core.safety import classify_call, ToolClass
        assert classify_call("manage_checkpoint", {"action": "wipe"}).tool_class is ToolClass.DESTRUCTIVE

    def test_missing_action_fails_closed(self):
        from core.safety import classify_call, ToolClass
        assert classify_call("manage_checkpoint", {}).tool_class is ToolClass.DESTRUCTIVE

    def test_action_key_camelcase_and_snake_case(self):
        """Enforcement canonicalises keys to snake_case before classification; the
        classifier itself keys off a lower/stripped 'action' string, so both an
        already-snake 'action' and a mixed-case value resolve to the same class."""
        from core.safety import classify_call, ToolClass
        assert classify_call("manage_checkpoint", {"action": "RESTORE"}).tool_class is ToolClass.DESTRUCTIVE
        assert classify_call("manage_checkpoint", {"action": " List "}).tool_class is ToolClass.READ

    def test_enforcement_canonicalizes_action(self):
        """A camelCase spelling of the action key must still be classified once the
        enforcement layer canonicalises it (belt-and-suspenders across ingresses)."""
        from core.enforcement import canonicalize_keys
        from core.safety import classify_call, ToolClass
        args, ambiguous = canonicalize_keys({"action": "restore"})
        assert not ambiguous
        assert classify_call("manage_checkpoint", args).tool_class is ToolClass.DESTRUCTIVE

    def test_restore_requires_confirm_in_write_mode(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        decision = evaluate_call(SafetyMode.WRITE, "manage_checkpoint", {"action": "restore"})
        assert decision.decision is Decision.CONFIRM_REQUIRED

    def test_list_allowed_in_read_only(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        decision = evaluate_call(SafetyMode.READ_ONLY, "manage_checkpoint", {"action": "list"})
        assert decision.decision is Decision.ALLOW

    def test_create_blocked_in_read_only(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        decision = evaluate_call(SafetyMode.READ_ONLY, "manage_checkpoint", {"action": "create"})
        assert decision.decision is Decision.BLOCK

    def test_create_allowed_in_review_only(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        decision = evaluate_call(SafetyMode.REVIEW_ONLY, "manage_checkpoint", {"action": "create"})
        assert decision.decision is Decision.ALLOW


class TestCliRegistration:
    def test_checkpoint_group_registered(self):
        from cli.main import cli
        assert "checkpoint" in cli.commands

    def test_checkpoint_subcommands(self):
        from cli.commands.checkpoint import checkpoint
        assert set(checkpoint.commands.keys()) == {"create", "list", "restore", "delete"}
