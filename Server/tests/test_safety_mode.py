"""Tests for safety modes, destructive-action classification, and audit logging."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import ToolError

from core.audit import AuditLogger, summarize_arguments
from core.config import config
from core.safety import (
    DEFAULT_MENU_ITEM_ALLOWLIST,
    Decision,
    SafetyMode,
    ToolClass,
    TOOL_POLICIES,
    build_output_guard,
    classify_call,
    evaluate_call,
    execute_code_guard,
    execute_menu_item_guard,
    get_safety_mode,
    parse_safety_mode,
)
from core.enforcement import (
    PolicyViolation,
    coerce_confirm,
    enforce_tool_call,
)
from transport.safety_middleware import SafetyMiddleware


class TestClassification:
    def test_read_only_tools(self):
        assert classify_call("find_gameobjects", {}).tool_class is ToolClass.READ
        assert classify_call("unity_reflect", {"action": "search"}).tool_class is ToolClass.READ
        assert classify_call("get_test_job", {}).tool_class is ToolClass.READ

    def test_action_level_reads_on_write_tools(self):
        assert classify_call("manage_asset", {"action": "search"}).tool_class is ToolClass.READ
        assert classify_call("manage_scene", {"action": "get_hierarchy"}).tool_class is ToolClass.READ
        assert classify_call("manage_packages", {"action": "list_packages"}).tool_class is ToolClass.READ

    def test_write_actions(self):
        assert classify_call("manage_asset", {"action": "create"}).tool_class is ToolClass.WRITE
        assert classify_call("create_script", {}).tool_class is ToolClass.WRITE
        assert classify_call("manage_scene", {"action": "save"}).tool_class is ToolClass.WRITE

    def test_destructive_actions(self):
        assert classify_call("manage_asset", {"action": "delete"}).tool_class is ToolClass.DESTRUCTIVE
        assert classify_call("delete_script", {}).tool_class is ToolClass.DESTRUCTIVE
        assert classify_call("execute_menu_item", {"menu_path": "File/Save"}).tool_class is ToolClass.DESTRUCTIVE
        assert classify_call("execute_code", {"action": "execute"}).tool_class is ToolClass.DESTRUCTIVE
        assert classify_call("manage_build", {"action": "build"}).tool_class is ToolClass.DESTRUCTIVE
        assert classify_call("manage_packages", {"action": "add_package"}).tool_class is ToolClass.DESTRUCTIVE

    def test_validate_actions(self):
        assert classify_call("run_tests", {"mode": "EditMode"}).tool_class is ToolClass.VALIDATE
        assert classify_call("refresh_unity", {}).tool_class is ToolClass.VALIDATE
        assert classify_call("manage_editor", {"action": "play"}).tool_class is ToolClass.VALIDATE
        assert classify_call("manage_camera", {"action": "screenshot"}).tool_class is ToolClass.VALIDATE

    def test_unknown_tool_falls_back_to_write(self):
        result = classify_call("some_future_tool", {"action": "whatever"})
        assert result.tool_class is ToolClass.WRITE
        assert result.known_tool is False

    def test_unknown_action_falls_back_to_tool_default(self):
        result = classify_call("manage_asset", {"action": "brand_new_action"})
        assert result.tool_class is ToolClass.WRITE
        assert result.known_tool is True

    def test_action_case_and_whitespace_normalized(self):
        assert classify_call("manage_asset", {"action": " DELETE "}).tool_class is ToolClass.DESTRUCTIVE

    def test_none_arguments(self):
        assert classify_call("find_gameobjects", None).tool_class is ToolClass.READ


class TestBatchClassification:
    def test_batch_of_reads_is_read(self):
        args = {"commands": [
            {"tool": "manage_asset", "params": {"action": "search"}},
            {"tool": "find_gameobjects", "params": {}},
        ]}
        assert classify_call("batch_execute", args).tool_class is ToolClass.READ

    def test_batch_escalates_to_worst_member(self):
        args = {"commands": [
            {"tool": "manage_asset", "params": {"action": "search"}},
            {"tool": "manage_asset", "params": {"action": "delete"}},
        ]}
        assert classify_call("batch_execute", args).tool_class is ToolClass.DESTRUCTIVE

    def test_batch_with_unknown_nested_tool_is_write(self):
        args = {"commands": [{"tool": "mystery_tool", "params": {}}]}
        assert classify_call("batch_execute", args).tool_class is ToolClass.WRITE

    def test_malformed_batch_is_write(self):
        assert classify_call("batch_execute", {}).tool_class is ToolClass.WRITE
        assert classify_call("batch_execute", {"commands": "nope"}).tool_class is ToolClass.WRITE

    def test_nested_batch_is_write(self):
        args = {"commands": [{"tool": "batch_execute", "params": {}}]}
        assert classify_call("batch_execute", args).tool_class is ToolClass.WRITE


class TestPolicyMatrix:
    def test_read_only_allows_reads(self):
        d = evaluate_call(SafetyMode.READ_ONLY, "manage_scene", {"action": "get_hierarchy"})
        assert d.decision is Decision.ALLOW

    def test_read_only_blocks_validate_write_destructive(self):
        for tool, args in (
            ("run_tests", {}),
            ("create_script", {}),
            ("manage_asset", {"action": "delete"}),
        ):
            d = evaluate_call(SafetyMode.READ_ONLY, tool, args)
            assert d.decision is Decision.BLOCK, tool
            assert "read_only" in d.reason

    def test_review_only_allows_reads_and_validation(self):
        assert evaluate_call(SafetyMode.REVIEW_ONLY, "run_tests", {}).decision is Decision.ALLOW
        assert evaluate_call(SafetyMode.REVIEW_ONLY, "manage_asset", {"action": "get_info"}).decision is Decision.ALLOW

    def test_review_only_blocks_writes_and_destructive(self):
        assert evaluate_call(SafetyMode.REVIEW_ONLY, "create_script", {}).decision is Decision.BLOCK
        assert evaluate_call(SafetyMode.REVIEW_ONLY, "delete_script", {}).decision is Decision.BLOCK

    def test_write_allows_writes_without_confirmation(self):
        assert evaluate_call(SafetyMode.WRITE, "create_script", {}).decision is Decision.ALLOW

    def test_write_requires_confirmation_for_destructive(self):
        d = evaluate_call(SafetyMode.WRITE, "manage_asset", {"action": "delete"})
        assert d.decision is Decision.CONFIRM_REQUIRED
        assert "confirm" in d.reason

    def test_write_allows_confirmed_destructive(self):
        d = evaluate_call(SafetyMode.WRITE, "manage_asset", {"action": "delete"}, confirmed=True)
        assert d.decision is Decision.ALLOW

    def test_confirmation_does_not_bypass_mode_block(self):
        d = evaluate_call(SafetyMode.READ_ONLY, "manage_asset", {"action": "delete"}, confirmed=True)
        assert d.decision is Decision.BLOCK


class TestModeParsing:
    def test_valid_modes(self):
        assert parse_safety_mode("read_only") is SafetyMode.READ_ONLY
        assert parse_safety_mode("REVIEW_ONLY") is SafetyMode.REVIEW_ONLY
        assert parse_safety_mode(" write ") is SafetyMode.WRITE
        assert parse_safety_mode("read-only") is SafetyMode.READ_ONLY

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Valid modes"):
            parse_safety_mode("yolo")

    def test_get_safety_mode_reads_config(self):
        config.safety_mode = "review_only"
        assert get_safety_mode() is SafetyMode.REVIEW_ONLY

    def test_get_safety_mode_fails_closed_on_garbage(self):
        config.safety_mode = "not_a_mode"
        assert get_safety_mode() is SafetyMode.READ_ONLY


class TestPolicyTableIntegrity:
    def test_every_destructive_capable_tool_is_listed(self):
        # Tools the threat model calls out must never fall back to the
        # unknown-tool default.
        for tool in (
            "execute_code", "execute_menu_item", "manage_build",
            "manage_packages", "manage_asset", "delete_script",
        ):
            assert tool in TOOL_POLICIES, tool

    def test_no_destructive_defaults_on_multiplexed_read_tools(self):
        # A tool whose default is DESTRUCTIVE must be an execution surface,
        # not a CRUD tool where reads would get confirm friction.
        for name, policy in TOOL_POLICIES.items():
            if policy.default is ToolClass.DESTRUCTIVE:
                assert name in ("execute_menu_item", "execute_code", "delete_script"), name


def make_context(tool_name: str, arguments, client_id: str | None = "client-1"):
    return SimpleNamespace(
        message=SimpleNamespace(name=tool_name, arguments=arguments),
        fastmcp_context=SimpleNamespace(client_id=client_id),
    )


@pytest.fixture
def audit_file(tmp_path):
    return tmp_path / "audit.jsonl"


@pytest.fixture
def middleware(audit_file):
    return SafetyMiddleware(audit_logger=AuditLogger(path=str(audit_file), enabled=True))


def read_audit(audit_file):
    lines = audit_file.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


class TestSafetyMiddleware:
    @pytest.mark.asyncio
    async def test_read_allowed_in_read_only(self, middleware, audit_file):
        config.safety_mode = "read_only"
        ctx = make_context("manage_scene", {"action": "get_hierarchy"})
        call_next = AsyncMock(return_value={"success": True})

        result = await middleware.on_call_tool(ctx, call_next)

        assert result == {"success": True}
        call_next.assert_awaited_once()
        records = read_audit(audit_file)
        assert records[-1]["decision"] == "allow"
        assert records[-1]["outcome"] == "ok"

    @pytest.mark.asyncio
    async def test_write_blocked_in_read_only(self, middleware, audit_file):
        config.safety_mode = "read_only"
        ctx = make_context("create_script", {"name": "Foo", "path": "Assets/"})
        call_next = AsyncMock()

        with pytest.raises(ToolError, match="read_only"):
            await middleware.on_call_tool(ctx, call_next)

        call_next.assert_not_awaited()
        records = read_audit(audit_file)
        assert records[-1]["decision"] == "block"
        assert records[-1]["outcome"] == "blocked"

    @pytest.mark.asyncio
    async def test_tests_allowed_in_review_only(self, middleware):
        config.safety_mode = "review_only"
        ctx = make_context("run_tests", {"mode": "EditMode"})
        call_next = AsyncMock(return_value={"success": True})

        await middleware.on_call_tool(ctx, call_next)
        call_next.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_write_blocked_in_review_only(self, middleware):
        config.safety_mode = "review_only"
        ctx = make_context("manage_gameobject", {"action": "create", "name": "Cube"})
        call_next = AsyncMock()

        with pytest.raises(ToolError, match="review_only"):
            await middleware.on_call_tool(ctx, call_next)
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_destructive_requires_confirm_in_write_mode(self, middleware, audit_file):
        config.safety_mode = "write"
        ctx = make_context("manage_asset", {"action": "delete", "path": "Assets/Old.mat"})
        call_next = AsyncMock()

        with pytest.raises(ToolError, match='"confirm": true'):
            await middleware.on_call_tool(ctx, call_next)

        call_next.assert_not_awaited()
        assert read_audit(audit_file)[-1]["decision"] == "confirm_required"

    @pytest.mark.asyncio
    async def test_confirmed_destructive_proceeds_and_confirm_is_stripped(self, middleware, audit_file):
        config.safety_mode = "write"
        arguments = {"action": "delete", "path": "Assets/Old.mat", "confirm": True}
        ctx = make_context("manage_asset", arguments)
        call_next = AsyncMock(return_value={"success": True})

        await middleware.on_call_tool(ctx, call_next)

        call_next.assert_awaited_once()
        # confirm must be popped before FastMCP validates the tool schema
        assert "confirm" not in arguments
        record = read_audit(audit_file)[-1]
        assert record["confirmed"] is True
        assert record["decision"] == "allow"

    @pytest.mark.asyncio
    async def test_confirm_stripped_even_on_non_destructive_calls(self, middleware):
        config.safety_mode = "write"
        arguments = {"action": "search", "path": "Assets", "confirm": True}
        ctx = make_context("manage_asset", arguments)
        call_next = AsyncMock(return_value={"success": True})

        await middleware.on_call_tool(ctx, call_next)
        assert "confirm" not in arguments

    @pytest.mark.asyncio
    async def test_string_confirm_values(self, middleware):
        config.safety_mode = "write"
        ctx = make_context("delete_script", {"uri": "unity://path/Assets/A.cs", "confirm": "true"})
        call_next = AsyncMock(return_value={"success": True})

        await middleware.on_call_tool(ctx, call_next)
        call_next.assert_awaited_once()

        ctx = make_context("delete_script", {"uri": "unity://path/Assets/A.cs", "confirm": "false"})
        with pytest.raises(ToolError):
            await middleware.on_call_tool(ctx, AsyncMock())

    @pytest.mark.asyncio
    async def test_batch_with_destructive_member_gated(self, middleware):
        config.safety_mode = "write"
        ctx = make_context("batch_execute", {"commands": [
            {"tool": "manage_gameobject", "params": {"action": "create", "name": "A"}},
            {"tool": "manage_gameobject", "params": {"action": "delete", "target": "B"}},
        ]})
        with pytest.raises(ToolError, match="confirm"):
            await middleware.on_call_tool(ctx, AsyncMock())

    @pytest.mark.asyncio
    async def test_tool_failure_is_audited_and_reraised(self, middleware, audit_file):
        config.safety_mode = "write"
        ctx = make_context("manage_scene", {"action": "save"})
        call_next = AsyncMock(side_effect=RuntimeError("unity exploded"))

        with pytest.raises(RuntimeError, match="unity exploded"):
            await middleware.on_call_tool(ctx, call_next)

        record = read_audit(audit_file)[-1]
        assert record["outcome"] == "error"
        assert "unity exploded" in record["error"]

    @pytest.mark.asyncio
    async def test_audit_records_client_and_params_summary(self, middleware, audit_file):
        config.safety_mode = "write"
        ctx = make_context("create_script", {
            "name": "Big", "path": "Assets/", "contents": "x" * 5000,
        }, client_id="session-42")
        await middleware.on_call_tool(ctx, AsyncMock(return_value={}))

        record = read_audit(audit_file)[-1]
        assert record["client"] == "session-42"
        assert len(record["params"]["contents"]) < 300  # truncated, not embedded


class TestAuditLogger:
    def test_disabled_logger_writes_nothing(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=str(path), enabled=False)
        logger.record({"event": "tool_call"})
        assert not path.exists()

    def test_records_are_timestamped_jsonl(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        logger = AuditLogger(path=str(path), enabled=True)
        logger.record({"event": "tool_call", "tool": "x"})
        logger.record({"event": "tool_call", "tool": "y"})
        records = [json.loads(l) for l in path.read_text(encoding="utf-8").strip().splitlines()]
        assert [r["tool"] for r in records] == ["x", "y"]
        assert all("ts" in r for r in records)

    def test_summarize_truncates_long_strings(self):
        summary = summarize_arguments({"code": "a" * 1000, "n": 5, "flag": True})
        assert summary["n"] == 5
        assert summary["flag"] is True
        assert len(summary["code"]) < 300
        assert "+800 chars" in summary["code"]

    def test_summarize_collapses_collections(self):
        summary = summarize_arguments({"items": [1] * 50, "nested": {"a": 1, "b": 2}})
        assert summary["items"] == "<list len=50>"
        assert "keys=" in summary["nested"]


# ---------------------------------------------------------------------------
# Phase-2 hardening: tool-specific guards
# ---------------------------------------------------------------------------

class TestExecuteCodeGuard:
    def test_run_actions_blocked_when_disabled(self):
        assert execute_code_guard({"action": "execute"}, allow=False) is not None
        assert execute_code_guard({"action": "replay"}, allow=False) is not None

    def test_missing_action_fails_safe(self):
        assert execute_code_guard({}, allow=False) is not None

    def test_history_actions_not_gated(self):
        assert execute_code_guard({"action": "get_history"}, allow=False) is None
        assert execute_code_guard({"action": "clear_history"}, allow=False) is None

    def test_allowed_when_enabled(self):
        assert execute_code_guard({"action": "execute"}, allow=True) is None


class TestMenuItemGuard:
    def test_allowlisted_path_ok(self):
        r = execute_menu_item_guard(
            {"menuPath": "Window/General/Console"},
            allowlist=DEFAULT_MENU_ITEM_ALLOWLIST, allow_arbitrary=False)
        assert r is None

    def test_non_allowlisted_blocked(self):
        r = execute_menu_item_guard(
            {"menuPath": "File/Build And Run"},
            allowlist=DEFAULT_MENU_ITEM_ALLOWLIST, allow_arbitrary=False)
        assert r is not None

    def test_missing_path_blocked(self):
        r = execute_menu_item_guard(
            {}, allowlist=DEFAULT_MENU_ITEM_ALLOWLIST, allow_arbitrary=False)
        assert r is not None

    def test_arbitrary_bypass(self):
        r = execute_menu_item_guard(
            {"menuPath": "Anything/Goes"}, allowlist=set(), allow_arbitrary=True)
        assert r is None

    def test_snake_case_arg_accepted(self):
        r = execute_menu_item_guard(
            {"menu_path": "Window/General/Console"},
            allowlist=DEFAULT_MENU_ITEM_ALLOWLIST, allow_arbitrary=False)
        assert r is None


class TestBuildOutputGuard:
    def test_project_relative_ok(self):
        assert build_output_guard({"output_path": "Builds/win/game.exe"}, allow_external=False) is None
        assert build_output_guard({"output_dir": "Builds"}, allow_external=False) is None

    def test_no_path_ok(self):
        assert build_output_guard({"action": "build"}, allow_external=False) is None

    def test_traversal_blocked(self):
        assert build_output_guard({"output_path": "../outside"}, allow_external=False) is not None
        assert build_output_guard({"output_path": "Builds/../../etc"}, allow_external=False) is not None

    def test_posix_absolute_blocked(self):
        assert build_output_guard({"output_path": "/tmp/evil"}, allow_external=False) is not None

    def test_windows_absolute_blocked(self):
        assert build_output_guard({"output_path": "C:/Windows/System32"}, allow_external=False) is not None
        assert build_output_guard({"output_path": "\\\\server\\share"}, allow_external=False) is not None

    def test_home_expansion_blocked(self):
        assert build_output_guard({"output_path": "~/builds"}, allow_external=False) is not None

    def test_external_allowed_when_configured(self):
        assert build_output_guard({"output_path": "/tmp/ok"}, allow_external=True) is None

    def test_traversal_blocked_even_when_external_allowed(self):
        # Escaping via .. is always refused; the config only relaxes absolute paths.
        assert build_output_guard({"output_path": "Builds/../../etc"}, allow_external=True) is not None

    def test_output_dir_also_validated(self):
        assert build_output_guard({"output_dir": "/etc"}, allow_external=False) is not None


# ---------------------------------------------------------------------------
# Phase-2 hardening: shared enforcement layer (used by MCP + API ingress)
# ---------------------------------------------------------------------------

@pytest.fixture
def enf_audit(tmp_path):
    return AuditLogger(path=str(tmp_path / "enf.jsonl"), enabled=True)


def enf_records(logger):
    import os
    if not os.path.exists(logger.path):
        return []
    with open(logger.path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def call_enforce(tool, args, *, confirmed=False, source="api", audit=None):
    return enforce_tool_call(
        tool, args, confirmed=confirmed, client="test", source=source,
        audit_logger=audit, audit_allow=(source == "api"),
    )


class TestEnforcementLayer:
    def test_read_only_blocks_destructive(self, enf_audit):
        config.safety_mode = "read_only"
        with pytest.raises(PolicyViolation) as exc:
            call_enforce("manage_asset", {"action": "delete", "path": "Assets/x.mat"}, audit=enf_audit)
        assert exc.value.decision is Decision.BLOCK
        assert enf_records(enf_audit)[-1]["outcome"] == "blocked"

    def test_review_only_blocks_write(self):
        config.safety_mode = "review_only"
        with pytest.raises(PolicyViolation):
            call_enforce("create_script", {"name": "X", "path": "Assets/"})

    def test_write_destructive_requires_confirm(self):
        config.safety_mode = "write"
        with pytest.raises(PolicyViolation) as exc:
            call_enforce("manage_asset", {"action": "delete", "path": "Assets/x.mat"})
        assert exc.value.decision is Decision.CONFIRM_REQUIRED

    def test_write_destructive_confirmed_allowed(self):
        config.safety_mode = "write"
        decision = call_enforce("manage_asset", {"action": "delete", "path": "Assets/x.mat"}, confirmed=True)
        assert decision.record["decision"] == "allow"

    def test_api_allow_is_audited_immediately(self, enf_audit):
        config.safety_mode = "write"
        call_enforce("manage_scene", {"action": "get_active"}, audit=enf_audit)
        rec = enf_records(enf_audit)[-1]
        assert rec["decision"] == "allow"
        assert rec["source"] == "api"

    # --- execute_code gate (task 3) ---
    def test_execute_code_blocked_when_disabled_even_with_confirm(self):
        config.safety_mode = "write"
        config.allow_execute_code = False
        with pytest.raises(PolicyViolation) as exc:
            call_enforce("execute_code", {"action": "execute", "code": "return 1;"}, confirmed=True)
        assert "not sandboxed" in exc.value.reason.lower()

    def test_execute_code_blocked_in_review_only(self):
        config.safety_mode = "review_only"
        config.allow_execute_code = True
        with pytest.raises(PolicyViolation):
            call_enforce("execute_code", {"action": "execute", "code": "return 1;"}, confirmed=True)

    def test_execute_code_allowed_when_enabled_and_confirmed(self):
        config.safety_mode = "write"
        config.allow_execute_code = True
        decision = call_enforce("execute_code", {"action": "execute", "code": "return 1;"}, confirmed=True)
        assert decision.record["decision"] == "allow"

    def test_execute_code_enabled_still_needs_confirm(self):
        config.safety_mode = "write"
        config.allow_execute_code = True
        with pytest.raises(PolicyViolation) as exc:
            call_enforce("execute_code", {"action": "execute", "code": "return 1;"})
        assert exc.value.decision is Decision.CONFIRM_REQUIRED

    def test_execute_code_history_allowed_in_read_only(self):
        config.safety_mode = "read_only"
        config.allow_execute_code = False
        decision = call_enforce("execute_code", {"action": "get_history"})
        assert decision.record["decision"] == "allow"

    # --- execute_menu_item allowlist (task 4) ---
    def test_menu_item_non_allowlisted_blocked_even_with_confirm(self):
        config.safety_mode = "write"
        config.allow_arbitrary_menu_items = False
        with pytest.raises(PolicyViolation):
            call_enforce("execute_menu_item", {"menuPath": "File/Build And Run"}, confirmed=True)

    def test_menu_item_allowlisted_allowed_when_confirmed(self):
        config.safety_mode = "write"
        decision = call_enforce("execute_menu_item", {"menuPath": "Window/General/Console"}, confirmed=True)
        assert decision.record["decision"] == "allow"

    def test_menu_item_config_allowlist_extends(self):
        config.safety_mode = "write"
        config.menu_item_allowlist = ["MyTools/Do Safe Thing"]
        decision = call_enforce("execute_menu_item", {"menuPath": "MyTools/Do Safe Thing"}, confirmed=True)
        assert decision.record["decision"] == "allow"

    def test_menu_item_arbitrary_config_allows(self):
        config.safety_mode = "write"
        config.allow_arbitrary_menu_items = True
        decision = call_enforce("execute_menu_item", {"menuPath": "File/Whatever"}, confirmed=True)
        assert decision.record["decision"] == "allow"

    # --- manage_build path sandbox (task 2) ---
    def test_build_external_path_blocked_even_with_confirm(self):
        config.safety_mode = "write"
        config.allow_external_build_output = False
        with pytest.raises(PolicyViolation):
            call_enforce("manage_build", {"action": "build", "target": "windows64", "output_path": "/tmp/evil"}, confirmed=True)

    def test_build_relative_path_allowed_when_confirmed(self):
        config.safety_mode = "write"
        decision = call_enforce("manage_build", {"action": "build", "target": "windows64", "output_path": "Builds/win/game.exe"}, confirmed=True)
        assert decision.record["decision"] == "allow"

    def test_build_status_not_path_checked(self):
        config.safety_mode = "review_only"
        # status is READ-classed; no path validation, allowed to inspect.
        decision = call_enforce("manage_build", {"action": "status", "job_id": "abc"})
        assert decision.record["decision"] == "allow"

    def test_build_external_allowed_when_configured(self):
        config.safety_mode = "write"
        config.allow_external_build_output = True
        decision = call_enforce("manage_build", {"action": "build", "target": "windows64", "output_path": "/tmp/ok"}, confirmed=True)
        assert decision.record["decision"] == "allow"


class TestBatchGuardRecursion:
    def test_batch_cannot_smuggle_execute_code(self):
        config.safety_mode = "write"
        config.allow_execute_code = False
        with pytest.raises(PolicyViolation) as exc:
            call_enforce("batch_execute", {"commands": [
                {"tool": "manage_gameobject", "params": {"action": "create", "name": "A"}},
                {"tool": "execute_code", "params": {"action": "execute", "code": "return 1;"}},
            ]}, confirmed=True)
        assert "execute_code" in exc.value.reason

    def test_batch_cannot_smuggle_external_build_path(self):
        config.safety_mode = "write"
        with pytest.raises(PolicyViolation) as exc:
            call_enforce("batch_execute", {"commands": [
                {"tool": "manage_build", "params": {"action": "build", "output_path": "/tmp/evil"}},
            ]}, confirmed=True)
        assert "manage_build" in exc.value.reason

    def test_batch_cannot_smuggle_non_allowlisted_menu(self):
        config.safety_mode = "write"
        with pytest.raises(PolicyViolation):
            call_enforce("batch_execute", {"commands": [
                {"tool": "execute_menu_item", "params": {"menuPath": "File/Build And Run"}},
            ]}, confirmed=True)

    def test_clean_batch_allowed(self):
        config.safety_mode = "write"
        decision = call_enforce("batch_execute", {"commands": [
            {"tool": "manage_gameobject", "params": {"action": "create", "name": "A"}},
            {"tool": "manage_gameobject", "params": {"action": "modify", "target": "A"}},
        ]})
        assert decision.record["decision"] == "allow"


class TestConfirmCoercion:
    def test_bool(self):
        assert coerce_confirm(True) is True
        assert coerce_confirm(False) is False

    def test_strings(self):
        assert coerce_confirm("true") is True
        assert coerce_confirm("YES") is True
        assert coerce_confirm("1") is True
        assert coerce_confirm("false") is False
        assert coerce_confirm("no") is False

    def test_other(self):
        assert coerce_confirm(None) is False
        assert coerce_confirm(1) is True
        assert coerce_confirm(0) is False


class TestTelemetryDefault:
    def _fresh_config(self, monkeypatch, *, enabled_cfg, opt_in=None, disable=None):
        from core.telemetry import TelemetryConfig
        for v in ("DISABLE_TELEMETRY", "UNITY_MCP_DISABLE_TELEMETRY",
                  "MCP_DISABLE_TELEMETRY", "UNITY_MCP_TELEMETRY_ENABLED"):
            monkeypatch.delenv(v, raising=False)
        if opt_in is not None:
            monkeypatch.setenv("UNITY_MCP_TELEMETRY_ENABLED", opt_in)
        if disable is not None:
            monkeypatch.setenv("DISABLE_TELEMETRY", disable)
        config.telemetry_enabled = enabled_cfg
        return TelemetryConfig()

    def test_disabled_by_default(self, monkeypatch):
        tc = self._fresh_config(monkeypatch, enabled_cfg=False)
        assert tc.enabled is False

    def test_opt_in_via_env(self, monkeypatch):
        tc = self._fresh_config(monkeypatch, enabled_cfg=False, opt_in="1")
        assert tc.enabled is True

    def test_config_enable(self, monkeypatch):
        tc = self._fresh_config(monkeypatch, enabled_cfg=True)
        assert tc.enabled is True

    def test_disable_overrides_opt_in(self, monkeypatch):
        tc = self._fresh_config(monkeypatch, enabled_cfg=True, opt_in="1", disable="1")
        assert tc.enabled is False


# ---------------------------------------------------------------------------
# Regressions for the 8 adversarial-review findings
# ---------------------------------------------------------------------------

def _nested_batch(inner_commands, depth=1):
    """Wrap inner_commands in `depth` layers of batch_execute."""
    node = {"commands": inner_commands}
    for _ in range(depth):
        node = {"commands": [{"tool": "batch_execute", "params": node}]}
    return node


class TestNestedBatchClassification:
    """Findings #1/#8: a nested batch must not downgrade a destructive member."""

    def test_nested_batch_wrapping_delete_is_destructive(self):
        args = _nested_batch([{"tool": "manage_gameobject", "params": {"action": "delete", "target": "X"}}])
        assert classify_call("batch_execute", args).tool_class is ToolClass.DESTRUCTIVE

    def test_nested_batch_delete_requires_confirm_in_write(self):
        args = _nested_batch([{"tool": "manage_gameobject", "params": {"action": "delete", "target": "X"}}])
        assert evaluate_call(SafetyMode.WRITE, "batch_execute", args).decision is Decision.CONFIRM_REQUIRED

    def test_double_nested_batch_delete_still_destructive(self):
        args = _nested_batch([{"tool": "manage_asset", "params": {"action": "delete", "path": "Assets/x.mat"}}], depth=3)
        assert classify_call("batch_execute", args).tool_class is ToolClass.DESTRUCTIVE

    def test_pathological_nesting_fails_closed(self):
        args = _nested_batch([{"tool": "find_gameobjects", "params": {}}], depth=8)
        # Beyond MAX_BATCH_DEPTH the classifier fails closed to destructive.
        assert classify_call("batch_execute", args).tool_class is ToolClass.DESTRUCTIVE

    def test_empty_nested_batch_is_write(self):
        args = {"commands": [{"tool": "batch_execute", "params": {}}]}
        assert classify_call("batch_execute", args).tool_class is ToolClass.WRITE

    def test_nested_batch_delete_blocked_in_read_only(self):
        args = _nested_batch([{"tool": "manage_asset", "params": {"action": "delete", "path": "Assets/x.mat"}}])
        # Confirm even with confirm=True it is still blocked by mode.
        assert evaluate_call(SafetyMode.READ_ONLY, "batch_execute", args, confirmed=True).decision is Decision.BLOCK


class TestManageBuildDualMode:
    """Findings #2-5: manage_build dual-mode actions must classify by payload."""

    def test_settings_write_is_write(self):
        assert classify_call("manage_build", {"action": "settings", "property": "scripting_backend", "value": "il2cpp"}).tool_class is ToolClass.WRITE

    def test_settings_read_is_read(self):
        assert classify_call("manage_build", {"action": "settings", "property": "scripting_backend"}).tool_class is ToolClass.READ

    def test_platform_switch_is_write(self):
        assert classify_call("manage_build", {"action": "platform", "target": "android"}).tool_class is ToolClass.WRITE

    def test_platform_read_is_read(self):
        assert classify_call("manage_build", {"action": "platform"}).tool_class is ToolClass.READ

    def test_scenes_write_is_write(self):
        assert classify_call("manage_build", {"action": "scenes", "scenes": ["Assets/A.unity"]}).tool_class is ToolClass.WRITE

    def test_scenes_read_is_read(self):
        assert classify_call("manage_build", {"action": "scenes"}).tool_class is ToolClass.READ

    def test_profiles_activate_is_write(self):
        assert classify_call("manage_build", {"action": "profiles", "profile": "Assets/P.asset", "activate": True}).tool_class is ToolClass.WRITE
        assert classify_call("manage_build", {"action": "profiles", "activate": "true"}).tool_class is ToolClass.WRITE

    def test_profiles_read_is_read(self):
        assert classify_call("manage_build", {"action": "profiles", "profile": "Assets/P.asset"}).tool_class is ToolClass.READ

    def test_status_and_build_unchanged(self):
        assert classify_call("manage_build", {"action": "status"}).tool_class is ToolClass.READ
        assert classify_call("manage_build", {"action": "build", "target": "windows64"}).tool_class is ToolClass.DESTRUCTIVE

    def test_read_only_blocks_settings_write(self):
        for args in (
            {"action": "settings", "property": "scripting_backend", "value": "il2cpp"},
            {"action": "platform", "target": "android"},
            {"action": "scenes", "scenes": ["Assets/A.unity"]},
            {"action": "profiles", "activate": True},
        ):
            assert evaluate_call(SafetyMode.READ_ONLY, "manage_build", args).decision is Decision.BLOCK, args

    def test_read_only_allows_settings_read(self):
        assert evaluate_call(SafetyMode.READ_ONLY, "manage_build", {"action": "settings", "property": "version"}).decision is Decision.ALLOW


class TestManageCameraVisualAudit:
    """manage_camera is payload-aware: the visual-audit tier splits by severity,
    and unknown/missing actions must fail closed."""

    def test_visibility_report_is_read(self):
        assert classify_call("manage_camera", {"action": "visibility_report"}).tool_class is ToolClass.READ

    def test_screenshot_is_validate(self):
        assert classify_call("manage_camera", {"action": "screenshot"}).tool_class is ToolClass.VALIDATE

    def test_screenshot_multiview_is_validate(self):
        assert classify_call("manage_camera", {"action": "screenshot_multiview"}).tool_class is ToolClass.VALIDATE

    def test_screenshot_compare_is_validate(self):
        assert classify_call("manage_camera", {"action": "screenshot_compare"}).tool_class is ToolClass.VALIDATE

    def test_pure_read_actions_are_read(self):
        for action in ("ping", "get_brain_status", "list_cameras"):
            assert classify_call("manage_camera", {"action": action}).tool_class is ToolClass.READ, action

    def test_configuration_actions_are_write(self):
        for action in ("create_camera", "set_target", "set_lens", "set_body",
                       "add_extension", "force_camera", "release_override"):
            assert classify_call("manage_camera", {"action": action}).tool_class is ToolClass.WRITE, action

    def test_unknown_action_fails_closed_to_write(self):
        # A never-seen action must not be treated as the (READ) pure-read default.
        assert classify_call("manage_camera", {"action": "brand_new_action"}).tool_class is ToolClass.WRITE

    def test_missing_action_fails_closed_to_write(self):
        assert classify_call("manage_camera", {}).tool_class is ToolClass.WRITE
        assert classify_call("manage_camera", None).tool_class is ToolClass.WRITE

    def test_action_case_and_whitespace_normalized(self):
        assert classify_call("manage_camera", {"action": " Visibility_Report "}).tool_class is ToolClass.READ
        assert classify_call("manage_camera", {"action": "SCREENSHOT_COMPARE"}).tool_class is ToolClass.VALIDATE

    def test_review_only_allows_capture_and_visibility(self):
        for action in ("visibility_report", "screenshot", "screenshot_compare"):
            assert evaluate_call(SafetyMode.REVIEW_ONLY, "manage_camera", {"action": action}).decision is Decision.ALLOW, action

    def test_read_only_allows_visibility_but_blocks_capture(self):
        assert evaluate_call(SafetyMode.READ_ONLY, "manage_camera", {"action": "visibility_report"}).decision is Decision.ALLOW
        assert evaluate_call(SafetyMode.READ_ONLY, "manage_camera", {"action": "screenshot_compare"}).decision is Decision.BLOCK

    def test_read_only_blocks_configuration(self):
        assert evaluate_call(SafetyMode.READ_ONLY, "manage_camera", {"action": "create_camera"}).decision is Decision.BLOCK


class TestMenuKeyPrecedence:
    """Finding #6: every provided menu-path spelling must be allowlisted."""

    def test_inverted_keys_blocked(self):
        # Allowlisted under the guard's preferred key, dangerous under the other.
        r = execute_menu_item_guard(
            {"menuPath": "Window/General/Console", "menu_path": "Assets/Delete"},
            allowlist=DEFAULT_MENU_ITEM_ALLOWLIST, allow_arbitrary=False)
        assert r is not None

    def test_both_allowlisted_ok(self):
        r = execute_menu_item_guard(
            {"menuPath": "Window/General/Console", "menu_path": "Window/General/Console"},
            allowlist=DEFAULT_MENU_ITEM_ALLOWLIST, allow_arbitrary=False)
        assert r is None

    def test_dangerous_snake_only_blocked(self):
        r = execute_menu_item_guard(
            {"menu_path": "Assets/Delete"},
            allowlist=DEFAULT_MENU_ITEM_ALLOWLIST, allow_arbitrary=False)
        assert r is not None


class TestBuildPathCamelCase:
    """Finding #7: camelCase output keys must be checked (C# ToolParams honors them)."""

    def test_camel_output_path_blocked(self):
        assert build_output_guard({"outputPath": "/etc/evil"}, allow_external=False) is not None

    def test_camel_output_dir_blocked(self):
        assert build_output_guard({"outputDir": "/etc/evil"}, allow_external=False) is not None

    def test_camel_traversal_blocked(self):
        assert build_output_guard({"outputPath": "../../../etc"}, allow_external=False) is not None

    def test_camel_relative_ok(self):
        assert build_output_guard({"outputPath": "Builds/win"}, allow_external=False) is None

    def test_enforcement_blocks_camel_build_path(self):
        config.safety_mode = "write"
        config.allow_external_build_output = False
        with pytest.raises(PolicyViolation):
            call_enforce("manage_build", {"action": "build", "target": "windows64", "outputPath": "/tmp/evil"}, confirmed=True)

    def test_enforcement_blocks_camel_build_path_in_batch(self):
        config.safety_mode = "write"
        config.allow_external_build_output = False
        with pytest.raises(PolicyViolation):
            call_enforce("batch_execute", {"commands": [
                {"tool": "manage_build", "params": {"action": "build", "outputPath": "/tmp/evil"}},
            ]}, confirmed=True)


class TestManageSceneDualMode:
    """Re-verify finding: manage_scene validate+auto_repair mutates, must be WRITE."""

    def test_validate_autorepair_is_write(self):
        assert classify_call("manage_scene", {"action": "validate", "auto_repair": True}).tool_class is ToolClass.WRITE

    def test_validate_autorepair_camelcase_is_write(self):
        assert classify_call("manage_scene", {"action": "validate", "autoRepair": True}).tool_class is ToolClass.WRITE

    def test_validate_autorepair_string_true_is_write(self):
        assert classify_call("manage_scene", {"action": "validate", "auto_repair": "true"}).tool_class is ToolClass.WRITE

    def test_plain_validate_is_read(self):
        assert classify_call("manage_scene", {"action": "validate"}).tool_class is ToolClass.READ
        assert classify_call("manage_scene", {"action": "validate", "auto_repair": False}).tool_class is ToolClass.READ

    def test_other_scene_actions_unaffected(self):
        assert classify_call("manage_scene", {"action": "get_hierarchy"}).tool_class is ToolClass.READ
        assert classify_call("manage_scene", {"action": "save"}).tool_class is ToolClass.WRITE

    def test_read_only_blocks_autorepair(self):
        assert evaluate_call(SafetyMode.READ_ONLY, "manage_scene", {"action": "validate", "auto_repair": True}).decision is Decision.BLOCK
        assert evaluate_call(SafetyMode.REVIEW_ONLY, "manage_scene", {"action": "validate", "auto_repair": True}).decision is Decision.BLOCK

    def test_read_only_allows_plain_validate(self):
        assert evaluate_call(SafetyMode.READ_ONLY, "manage_scene", {"action": "validate"}).decision is Decision.ALLOW

    def test_enforcement_blocks_camel_autorepair_in_read_only(self):
        config.safety_mode = "read_only"
        with pytest.raises(PolicyViolation):
            call_enforce("manage_scene", {"action": "validate", "autoRepair": True})


class TestKeyCanonicalization:
    """Systemic fix for the guard/handler key-parity bug class."""

    def test_camel_folds_to_snake(self):
        from core.enforcement import canonicalize_keys
        norm, ambiguous = canonicalize_keys({"outputPath": "/etc/evil", "menuPath": "X"})
        assert norm["output_path"] == "/etc/evil"
        assert norm["menu_path"] == "X"
        assert ambiguous is False

    def test_pascalcase_folds(self):
        from core.enforcement import canonicalize_keys
        norm, _ = canonicalize_keys({"OutputPath": "Builds"})
        assert norm["output_path"] == "Builds"

    def test_same_value_duplicate_not_ambiguous(self):
        from core.enforcement import canonicalize_keys
        _, ambiguous = canonicalize_keys({"output_path": "Builds", "outputPath": "Builds"})
        assert ambiguous is False

    def test_differing_duplicate_is_ambiguous(self):
        from core.enforcement import canonicalize_keys
        _, ambiguous = canonicalize_keys({"menu_path": "Assets/Delete", "menuPath": "Window/General/Console"})
        assert ambiguous is True

    def test_batch_member_params_normalized(self):
        from core.enforcement import canonicalize_keys
        norm, _ = canonicalize_keys({"commands": [
            {"tool": "manage_build", "params": {"outputPath": "/etc"}},
        ]})
        assert norm["commands"][0]["params"] == {"output_path": "/etc"}

    def test_ambiguous_keys_blocked_at_enforcement(self):
        config.safety_mode = "write"
        with pytest.raises(PolicyViolation):
            call_enforce(
                "execute_menu_item",
                {"menuPath": "Window/General/Console", "menu_path": "Assets/Delete"},
                confirmed=True,
            )

    def test_to_snake_matches_unity_tosnakecase(self):
        # Must equal Unity StringCaseUtility.ToSnakeCase:
        # Regex.Replace(s, "([a-z0-9])([A-Z])", "$1_$2").ToLowerInvariant()
        from core.enforcement import _to_snake
        assert _to_snake("outputPath") == "output_path"
        assert _to_snake("menuPath") == "menu_path"
        assert _to_snake("autoRepair") == "auto_repair"
        assert _to_snake("saveBeforeClose") == "save_before_close"
        assert _to_snake("OutputPath") == "output_path"
        assert _to_snake("already_snake") == "already_snake"
        # Acronym run: no lower/digit-then-upper boundary, so it stays fused
        # (matches C# behaviour — the point of the parity fix).
        assert _to_snake("HTTPPath") == "httppath"


class TestThirdPassDualModeSiblings:
    """Re-verify round 3: more dual-mode actions misclassified as READ/VALIDATE."""

    def test_render_ui_is_write(self):
        assert classify_call("manage_ui", {"action": "render_ui", "path": "Assets/UI/M.uxml"}).tool_class is ToolClass.WRITE

    def test_render_ui_blocked_in_inspect_modes(self):
        for mode in (SafetyMode.READ_ONLY, SafetyMode.REVIEW_ONLY):
            assert evaluate_call(mode, "manage_ui", {"action": "render_ui"}).decision is Decision.BLOCK, mode

    def test_manage_ui_reads_still_read(self):
        assert classify_call("manage_ui", {"action": "read", "path": "Assets/UI/M.uxml"}).tool_class is ToolClass.READ

    def test_bake_get_settings_is_write(self):
        assert classify_call("manage_graphics", {"action": "bake_get_settings"}).tool_class is ToolClass.WRITE

    def test_bake_get_settings_blocked_in_read_only(self):
        assert evaluate_call(SafetyMode.READ_ONLY, "manage_graphics", {"action": "bake_get_settings"}).decision is Decision.BLOCK

    def test_bake_status_still_read(self):
        assert classify_call("manage_graphics", {"action": "bake_status"}).tool_class is ToolClass.READ

    def test_close_prefab_stage_discard_is_validate(self):
        assert classify_call("manage_prefabs", {"action": "close_prefab_stage"}).tool_class is ToolClass.VALIDATE

    def test_close_prefab_stage_save_is_write(self):
        assert classify_call("manage_prefabs", {"action": "close_prefab_stage", "save_before_close": True}).tool_class is ToolClass.WRITE
        assert classify_call("manage_prefabs", {"action": "close_prefab_stage", "saveBeforeClose": True}).tool_class is ToolClass.WRITE

    def test_close_prefab_stage_save_blocked_in_review_only(self):
        assert evaluate_call(SafetyMode.REVIEW_ONLY, "manage_prefabs", {"action": "close_prefab_stage", "saveBeforeClose": True}).decision is Decision.BLOCK

    def test_close_prefab_stage_discard_allowed_in_review_only(self):
        assert evaluate_call(SafetyMode.REVIEW_ONLY, "manage_prefabs", {"action": "close_prefab_stage"}).decision is Decision.ALLOW

    def test_prefab_reads_still_read(self):
        assert classify_call("manage_prefabs", {"action": "get_info"}).tool_class is ToolClass.READ
