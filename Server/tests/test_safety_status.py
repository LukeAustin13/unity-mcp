"""Tests for the safety_status tool, server/safety resource, and posture reporter."""
import pytest

from core.config import config
from core.safety import DEFAULT_MENU_ITEM_ALLOWLIST, describe_safety_posture
from services.tools.safety_status import safety_status
from services.resources.server_safety import get_server_safety


class FakeCtx:
    client_id = "test"

    async def get_state(self, key):
        return None


class TestDescribePosture:
    def test_reports_mode_and_allowed_classes(self):
        config.safety_mode = "review_only"
        p = describe_safety_posture()
        assert p["mode"] == "review_only"
        assert set(p["allowed_classes"]) == {"read", "validate"}
        assert p["destructive_requires_confirm"] is False

    def test_write_mode_flags_confirm(self):
        config.safety_mode = "write"
        p = describe_safety_posture()
        assert p["mode"] == "write"
        assert p["destructive_requires_confirm"] is True
        assert "acknowledgement metadata" in p["confirm_semantics"]

    def test_read_only_allowed_classes(self):
        config.safety_mode = "read_only"
        assert describe_safety_posture()["allowed_classes"] == ["read"]

    def test_execution_surface_flags_reflect_config(self):
        config.safety_mode = "write"
        config.allow_execute_code = True
        config.allow_arbitrary_menu_items = False
        config.allow_external_build_output = True
        p = describe_safety_posture()
        assert p["execute_code_enabled"] is True
        assert p["arbitrary_menu_items_enabled"] is False
        assert p["external_build_output_allowed"] is True

    def test_menu_allowlist_includes_defaults_and_config(self):
        config.menu_item_allowlist = ["MyTools/Safe"]
        p = describe_safety_posture()
        for item in DEFAULT_MENU_ITEM_ALLOWLIST:
            assert item in p["menu_item_allowlist"]
        assert "MyTools/Safe" in p["menu_item_allowlist"]

    def test_audit_fields_present(self):
        p = describe_safety_posture()
        assert "audit_log_enabled" in p
        assert "audit_log_path" in p


class TestSafetyStatusTool:
    @pytest.mark.asyncio
    async def test_tool_returns_posture(self):
        config.safety_mode = "read_only"
        result = await safety_status(FakeCtx())
        assert result["success"] is True
        assert result["data"]["mode"] == "read_only"

    @pytest.mark.asyncio
    async def test_resource_returns_posture(self):
        config.safety_mode = "write"
        result = await get_server_safety(FakeCtx())
        assert result["mode"] == "write"
        assert "allowed_classes" in result


class TestSafetyStatusClassification:
    def test_safety_status_is_read_classified(self):
        from core.safety import classify_call, ToolClass
        assert classify_call("safety_status", {}).tool_class is ToolClass.READ

    @pytest.mark.asyncio
    async def test_safety_status_allowed_in_read_only(self):
        # The tool that reports the mode must itself be usable in read_only.
        from core.enforcement import enforce_tool_call
        config.safety_mode = "read_only"
        decision = enforce_tool_call(
            "safety_status", {}, confirmed=False, client="t", source="mcp")
        assert decision.record["decision"] == "allow"
