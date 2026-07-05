"""Plumbing tests for the audit_ui_layout tool.

These verify the Python side: parameter forwarding/coercion, strict resolution
validation, clamping, and READ safety classification. The actual analytic layout
audit (scene traversal, CanvasScaler math, screen-rect computation, overlap/off-
screen predicates) is C# (AuditUiLayout.cs) and is verified in Unity EditMode tests.
"""
from unittest.mock import AsyncMock

import pytest

import services.tools.audit_ui_layout as aul
from services.tools.audit_ui_layout import audit_ui_layout


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
        return {"success": True, "data": {"findings": [], "summary": {}, "caveats": []}}

    monkeypatch.setattr(aul, "send_with_unity_instance", fake_send)
    return sent


class TestRegistration:
    def test_tool_is_registered(self):
        from services.registry import get_registered_tools
        names = {t["name"] for t in get_registered_tools()}
        assert "audit_ui_layout" in names

    def test_tool_is_in_core_group(self):
        from services.registry import get_registered_tools
        tool = next(t for t in get_registered_tools() if t["name"] == "audit_ui_layout")
        assert tool["group"] == "core"


class TestDefaults:
    @pytest.mark.asyncio
    async def test_no_args_dispatches_with_empty_params(self, captured):
        # With nothing set, C# applies all defaults (matrix, min_target_px, etc.).
        await audit_ui_layout(FakeCtx())
        assert captured["command"] == "audit_ui_layout"
        assert captured["params"] == {}


class TestResolutionValidation:
    @pytest.mark.asyncio
    async def test_resolution_list_forwarded(self, captured):
        await audit_ui_layout(FakeCtx(), resolutions=["1920x1080", "1280x720"])
        assert captured["params"]["resolutions"] == ["1920x1080", "1280x720"]

    @pytest.mark.asyncio
    async def test_resolution_csv_string_forwarded(self, captured):
        await audit_ui_layout(FakeCtx(), resolutions="1920x1080, 1280x720")
        assert captured["params"]["resolutions"] == ["1920x1080", "1280x720"]

    @pytest.mark.asyncio
    async def test_resolution_json_string_forwarded(self, captured):
        await audit_ui_layout(FakeCtx(), resolutions='["2560x1440"]')
        assert captured["params"]["resolutions"] == ["2560x1440"]

    @pytest.mark.asyncio
    async def test_resolution_uppercase_x_normalized(self, captured):
        await audit_ui_layout(FakeCtx(), resolutions=["1920X1080"])
        assert captured["params"]["resolutions"] == ["1920x1080"]

    @pytest.mark.asyncio
    async def test_resolution_dedupes(self, captured):
        await audit_ui_layout(FakeCtx(), resolutions=["1920x1080", "1920x1080"])
        assert captured["params"]["resolutions"] == ["1920x1080"]

    @pytest.mark.asyncio
    async def test_missing_x_rejected(self, captured):
        result = await audit_ui_layout(FakeCtx(), resolutions=["1920"])
        assert result["success"] is False
        assert "1920" in result["message"]
        assert "command" not in captured  # never dispatched

    @pytest.mark.asyncio
    async def test_non_integer_dimension_rejected(self, captured):
        result = await audit_ui_layout(FakeCtx(), resolutions=["axb"])
        assert result["success"] is False
        assert "command" not in captured

    @pytest.mark.asyncio
    async def test_dimension_too_small_rejected(self, captured):
        result = await audit_ui_layout(FakeCtx(), resolutions=["8x8"])
        assert result["success"] is False
        assert "range" in result["message"].lower()
        assert "command" not in captured

    @pytest.mark.asyncio
    async def test_dimension_too_large_rejected(self, captured):
        result = await audit_ui_layout(FakeCtx(), resolutions=["20000x1080"])
        assert result["success"] is False
        assert "command" not in captured

    @pytest.mark.asyncio
    async def test_too_many_resolutions_rejected(self, captured):
        many = [f"{100 + i}x100" for i in range(9)]
        result = await audit_ui_layout(FakeCtx(), resolutions=many)
        assert result["success"] is False
        assert "maximum" in result["message"].lower()
        assert "command" not in captured

    @pytest.mark.asyncio
    async def test_empty_entry_rejected(self, captured):
        result = await audit_ui_layout(FakeCtx(), resolutions=["1920x1080", ""])
        assert result["success"] is False
        assert "command" not in captured

    @pytest.mark.asyncio
    async def test_resolutions_omitted_not_sent(self, captured):
        # Default matrix is applied C#-side; Python omits it when unset.
        await audit_ui_layout(FakeCtx(), scene="Main")
        assert "resolutions" not in captured["params"]

    @pytest.mark.asyncio
    async def test_blank_resolutions_string_omitted(self, captured):
        await audit_ui_layout(FakeCtx(), resolutions="   ")
        assert "resolutions" not in captured["params"]


class TestParamForwarding:
    @pytest.mark.asyncio
    async def test_scene_forwarded_and_trimmed(self, captured):
        await audit_ui_layout(FakeCtx(), scene="  Main  ")
        assert captured["params"]["scene"] == "Main"

    @pytest.mark.asyncio
    async def test_empty_scene_not_sent(self, captured):
        await audit_ui_layout(FakeCtx(), scene="   ")
        assert "scene" not in captured["params"]

    @pytest.mark.asyncio
    async def test_include_inactive_coerced(self, captured):
        await audit_ui_layout(FakeCtx(), include_inactive="true")
        assert captured["params"]["include_inactive"] is True

    @pytest.mark.asyncio
    async def test_include_inactive_omitted_not_sent(self, captured):
        await audit_ui_layout(FakeCtx(), scene="Main")
        assert "include_inactive" not in captured["params"]


class TestClamping:
    @pytest.mark.asyncio
    async def test_min_target_px_forwarded(self, captured):
        await audit_ui_layout(FakeCtx(), min_target_px=48)
        assert captured["params"]["min_target_px"] == 48

    @pytest.mark.asyncio
    async def test_min_target_px_clamped_to_floor(self, captured):
        await audit_ui_layout(FakeCtx(), min_target_px=1)
        assert captured["params"]["min_target_px"] == 8

    @pytest.mark.asyncio
    async def test_min_target_px_clamped_to_ceil(self, captured):
        await audit_ui_layout(FakeCtx(), min_target_px=9999)
        assert captured["params"]["min_target_px"] == 256

    @pytest.mark.asyncio
    async def test_min_target_px_string_coerced(self, captured):
        await audit_ui_layout(FakeCtx(), min_target_px="60")
        assert captured["params"]["min_target_px"] == 60

    @pytest.mark.asyncio
    async def test_max_findings_forwarded(self, captured):
        await audit_ui_layout(FakeCtx(), max_findings=100)
        assert captured["params"]["max_findings"] == 100

    @pytest.mark.asyncio
    async def test_max_findings_clamped_to_cap(self, captured):
        await audit_ui_layout(FakeCtx(), max_findings=999999)
        assert captured["params"]["max_findings"] == 2000

    @pytest.mark.asyncio
    async def test_max_findings_clamped_to_min(self, captured):
        await audit_ui_layout(FakeCtx(), max_findings=0)
        assert captured["params"]["max_findings"] == 1


class TestResponse:
    @pytest.mark.asyncio
    async def test_non_dict_response_wrapped(self, monkeypatch):
        monkeypatch.setattr(aul, "send_with_unity_instance", AsyncMock(return_value="weird"))
        result = await audit_ui_layout(FakeCtx(), scene="Main")
        assert result["success"] is False
        assert "weird" in result["message"]


class TestClassification:
    def test_audit_ui_layout_is_read(self):
        from core.safety import classify_call, ToolClass
        assert classify_call("audit_ui_layout", {}).tool_class is ToolClass.READ

    def test_audit_ui_layout_is_read_with_params(self):
        from core.safety import classify_call, ToolClass
        args = {"resolutions": ["1920x1080"], "scene": "Main", "min_target_px": 44}
        assert classify_call("audit_ui_layout", args).tool_class is ToolClass.READ

    def test_audit_ui_layout_allowed_in_read_only(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        assert evaluate_call(SafetyMode.READ_ONLY, "audit_ui_layout", {}).decision is Decision.ALLOW

    def test_audit_ui_layout_allowed_in_read_only_with_params(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        args = {"resolutions": ["1920x1080", "1280x720"], "include_inactive": True}
        assert evaluate_call(SafetyMode.READ_ONLY, "audit_ui_layout", args).decision is Decision.ALLOW
