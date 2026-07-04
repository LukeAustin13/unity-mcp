"""Plumbing tests for the validate_build tool.

These verify the Python side: command name, parameter forwarding, response
wrapping, and READ safety classification. The actual build pre-flight is C#
(ValidateBuild.cs) and is verified in Unity EditMode tests.
"""
from unittest.mock import AsyncMock

import pytest

import services.tools.validate_build as vb
from services.tools.validate_build import validate_build


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
        return {"success": True, "data": {"would_build": True}}

    monkeypatch.setattr(vb, "send_with_unity_instance", fake_send)
    monkeypatch.setattr(
        vb, "get_unity_instance_from_context", AsyncMock(return_value="unity-1")
    )
    return sent


class TestDispatch:
    @pytest.mark.asyncio
    async def test_sends_to_validate_build_command(self, captured):
        await validate_build(FakeCtx())
        assert captured["command"] == "validate_build"

    @pytest.mark.asyncio
    async def test_target_forwarded(self, captured):
        await validate_build(FakeCtx(), target="android")
        assert captured["params"]["target"] == "android"

    @pytest.mark.asyncio
    async def test_target_omitted_when_none(self, captured):
        await validate_build(FakeCtx())
        assert captured["params"] == {}

    @pytest.mark.asyncio
    async def test_non_dict_response_wrapped(self, monkeypatch):
        monkeypatch.setattr(
            vb, "get_unity_instance_from_context", AsyncMock(return_value="unity-1")
        )
        monkeypatch.setattr(
            vb, "send_with_unity_instance", AsyncMock(return_value="weird")
        )
        result = await validate_build(FakeCtx())
        assert result["success"] is False
        assert "weird" in result["message"]


class TestClassification:
    def test_validate_build_is_read(self):
        from core.safety import classify_call, ToolClass
        assert classify_call("validate_build", {}).tool_class is ToolClass.READ
        assert (
            classify_call("validate_build", {"target": "android"}).tool_class
            is ToolClass.READ
        )

    def test_allowed_in_read_only(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        assert (
            evaluate_call(SafetyMode.READ_ONLY, "validate_build", {}).decision
            is Decision.ALLOW
        )
