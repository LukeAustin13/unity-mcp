"""Plumbing tests for the validate_scene_contracts tool.

These verify the Python side: exactly-one-of contract/contract_path enforcement,
parameter forwarding, response wrapping, and READ safety classification. The
actual scene traversal + rule evaluation (and the contract-file guards) is C#
(ValidateSceneContracts.cs) and is verified in Unity EditMode tests.

Note: the "unknown top-level key must be refused" rule is enforced C#-side (the
Python layer is a thin passthrough that does not inspect contract contents), so
that behaviour is asserted in the EditMode tests. Here we assert the wiring and
the exactly-one-of contract-source rule.
"""
from unittest.mock import AsyncMock

import pytest

import services.tools.validate_scene_contracts as vsc
from services.tools.validate_scene_contracts import validate_scene_contracts


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
        return {"success": True, "data": {"passed": True, "findings": []}}

    monkeypatch.setattr(vsc, "send_with_unity_instance", fake_send)
    return sent


class TestRegistration:
    def test_tool_is_registered(self):
        from services.registry import get_registered_tools
        names = {t["name"] for t in get_registered_tools()}
        assert "validate_scene_contracts" in names

    def test_tool_is_in_core_group(self):
        from services.registry import get_registered_tools
        tool = next(t for t in get_registered_tools() if t["name"] == "validate_scene_contracts")
        assert tool["group"] == "core"


class TestContractSourceValidation:
    @pytest.mark.asyncio
    async def test_inline_contract_forwarded(self, captured):
        await validate_scene_contracts(FakeCtx(), contract={"required_objects": ["Player"]})
        assert captured["command"] == "validate_scene_contracts"
        assert captured["params"]["contract"] == {"required_objects": ["Player"]}
        assert "contract_path" not in captured["params"]

    @pytest.mark.asyncio
    async def test_contract_path_forwarded_and_trimmed(self, captured):
        await validate_scene_contracts(FakeCtx(), contract_path="  Assets/Contracts/main.json  ")
        assert captured["params"]["contract_path"] == "Assets/Contracts/main.json"
        assert "contract" not in captured["params"]

    @pytest.mark.asyncio
    async def test_both_given_is_error(self, captured):
        result = await validate_scene_contracts(
            FakeCtx(), contract={"required_objects": []}, contract_path="Assets/c.json")
        assert result["success"] is False
        assert "exactly one" in result["message"].lower()
        assert "command" not in captured  # never dispatched

    @pytest.mark.asyncio
    async def test_neither_given_is_error(self, captured):
        result = await validate_scene_contracts(FakeCtx())
        assert result["success"] is False
        assert "required" in result["message"].lower()
        assert "command" not in captured

    @pytest.mark.asyncio
    async def test_empty_contract_path_treated_as_absent(self, captured):
        # An all-whitespace path counts as "not given"; with no inline contract this errors.
        result = await validate_scene_contracts(FakeCtx(), contract_path="   ")
        assert result["success"] is False
        assert "command" not in captured

    @pytest.mark.asyncio
    async def test_non_dict_contract_rejected(self, captured):
        result = await validate_scene_contracts(FakeCtx(), contract="not a dict")
        assert result["success"] is False
        assert "object" in result["message"].lower()
        assert "command" not in captured

    @pytest.mark.asyncio
    async def test_empty_dict_contract_is_valid_source(self, captured):
        # An empty inline object is a legitimate (if trivial) contract source; the
        # C# side decides what an empty contract means. Python must forward it.
        await validate_scene_contracts(FakeCtx(), contract={})
        assert captured["params"]["contract"] == {}


class TestSceneForwarding:
    @pytest.mark.asyncio
    async def test_scene_forwarded_and_trimmed(self, captured):
        await validate_scene_contracts(FakeCtx(), contract={}, scene="  Level2  ")
        assert captured["params"]["scene"] == "Level2"

    @pytest.mark.asyncio
    async def test_scene_omitted_not_sent(self, captured):
        await validate_scene_contracts(FakeCtx(), contract={})
        assert "scene" not in captured["params"]

    @pytest.mark.asyncio
    async def test_empty_scene_not_sent(self, captured):
        await validate_scene_contracts(FakeCtx(), contract={}, scene="   ")
        assert "scene" not in captured["params"]


class TestResponse:
    @pytest.mark.asyncio
    async def test_non_dict_response_wrapped(self, monkeypatch):
        monkeypatch.setattr(vsc, "send_with_unity_instance", AsyncMock(return_value="weird"))
        result = await validate_scene_contracts(FakeCtx(), contract={})
        assert result["success"] is False
        assert "weird" in result["message"]


class TestClassification:
    def test_validate_scene_contracts_is_read(self):
        from core.safety import classify_call, ToolClass
        assert classify_call("validate_scene_contracts", {}).tool_class is ToolClass.READ

    def test_validate_scene_contracts_allowed_in_read_only(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        assert evaluate_call(
            SafetyMode.READ_ONLY, "validate_scene_contracts", {}
        ).decision is Decision.ALLOW

    def test_validate_scene_contracts_allowed_in_review_only(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        assert evaluate_call(
            SafetyMode.REVIEW_ONLY, "validate_scene_contracts", {}
        ).decision is Decision.ALLOW
