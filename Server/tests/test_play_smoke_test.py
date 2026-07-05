"""Tests for the play_smoke_test tool (Python side).

Covers action validation, the run-loop composition (start -> poll -> verdict)
with a mocked transport simulating start/pending/done sequences and the timeout
path, and the VALIDATE safety classification. The actual play-mode enter/run/exit
is C# (PlaySmokeTest.cs) and is verified in a Unity EditMode test.
"""
import services.tools.play_smoke_test as pst
from services.tools.play_smoke_test import ALL_ACTIONS, play_smoke_test

from core.safety import TOOL_POLICIES, ToolClass, classify_call

import pytest


class FakeCtx:
    client_id = "test"

    async def get_state(self, key):
        return None


class _ScriptedTransport:
    """Returns a queued sequence of responses; records every call's params."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __call__(self, send_fn, unity_instance, command, params):
        self.calls.append(params)
        if self._responses:
            return self._responses.pop(0)
        # Default steady-state: keep reporting the last response.
        return {"success": True, "data": {"status": "running"}}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fake_sleep(_seconds):
        return None
    monkeypatch.setattr(pst.asyncio, "sleep", fake_sleep)


def _install(monkeypatch, responses):
    transport = _ScriptedTransport(responses)
    monkeypatch.setattr(pst.unity_transport, "send_with_unity_instance", transport)
    return transport


# --- Action validation ------------------------------------------------------

class TestActionValidation:
    @pytest.mark.asyncio
    async def test_unknown_action_rejected(self, monkeypatch):
        _install(monkeypatch, [])
        result = await play_smoke_test(FakeCtx(), action="nope")
        assert result["success"] is False
        assert "Unknown action" in result["error"]

    @pytest.mark.asyncio
    async def test_status_requires_job_id(self, monkeypatch):
        _install(monkeypatch, [])
        result = await play_smoke_test(FakeCtx(), action="status")
        assert result["success"] is False
        assert "job_id" in result["error"]

    @pytest.mark.asyncio
    async def test_all_actions_listed(self):
        assert ALL_ACTIONS == ["run", "start", "status"]

    @pytest.mark.asyncio
    async def test_start_forwards_clamped_duration(self, monkeypatch):
        t = _install(monkeypatch, [{"success": True, "data": {"job_id": "j1"}}])
        await play_smoke_test(FakeCtx(), action="start", seconds=999)
        assert t.calls[0]["action"] == "start"
        assert t.calls[0]["duration_seconds"] == pst.MAX_DURATION_SECONDS

    @pytest.mark.asyncio
    async def test_start_default_duration(self, monkeypatch):
        t = _install(monkeypatch, [{"success": True, "data": {"job_id": "j1"}}])
        await play_smoke_test(FakeCtx(), action="start")
        assert t.calls[0]["duration_seconds"] == pst.DEFAULT_DURATION_SECONDS

    @pytest.mark.asyncio
    async def test_status_forwards_job_id(self, monkeypatch):
        t = _install(monkeypatch, [{"success": True, "data": {"status": "running"}}])
        await play_smoke_test(FakeCtx(), action="status", job_id="abc")
        assert t.calls[0] == {"action": "status", "job_id": "abc"}

    @pytest.mark.asyncio
    async def test_invalid_seconds_rejected(self, monkeypatch):
        _install(monkeypatch, [])
        result = await play_smoke_test(FakeCtx(), action="run", seconds="notanint")
        assert result["success"] is False
        assert "seconds" in result["error"]


# --- run composition --------------------------------------------------------

class TestRunComposition:
    @pytest.mark.asyncio
    async def test_start_then_pending_then_done_pass(self, monkeypatch):
        _install(monkeypatch, [
            {"success": True, "data": {"job_id": "job-1"}},               # start
            {"success": True, "data": {"status": "running"}},             # poll 1
            {"success": True, "data": {"status": "running"}},             # poll 2
            {"success": True, "data": {
                "status": "done",
                "error_count": 0,
                "exception_count": 0,
                "warning_count": 2,
                "sample_errors": [],
            }},                                                           # poll 3
        ])
        result = await play_smoke_test(FakeCtx(), action="run", seconds=5)
        assert result["success"] is True
        assert result["completed"] is True
        assert result["verdict"] == "pass"
        assert result["error_count"] == 0
        assert result["exception_count"] == 0
        assert result["warning_count"] == 2
        assert result["job_id"] == "job-1"

    @pytest.mark.asyncio
    async def test_done_with_errors_is_fail(self, monkeypatch):
        _install(monkeypatch, [
            {"success": True, "data": {"job_id": "job-2"}},
            {"success": True, "data": {
                "status": "done",
                "error_count": 3,
                "exception_count": 1,
                "warning_count": 0,
                "sample_errors": ["NullReferenceException", "boom"],
            }},
        ])
        result = await play_smoke_test(FakeCtx(), action="run", seconds=5)
        assert result["verdict"] == "fail"
        assert result["error_count"] == 3
        assert result["exception_count"] == 1
        assert result["sample_errors"] == ["NullReferenceException", "boom"]

    @pytest.mark.asyncio
    async def test_job_failed_status_is_fail(self, monkeypatch):
        _install(monkeypatch, [
            {"success": True, "data": {"job_id": "job-3"}},
            {"success": True, "data": {
                "status": "failed",
                "error_count": 0,
                "exception_count": 0,
                "warning_count": 0,
                "reason": "play_mode_never_entered (compile error or blocked play)",
            }},
        ])
        result = await play_smoke_test(FakeCtx(), action="run", seconds=5)
        assert result["completed"] is True
        assert result["verdict"] == "fail"
        assert "never_entered" in result["reason"]

    @pytest.mark.asyncio
    async def test_start_failure_short_circuits(self, monkeypatch):
        t = _install(monkeypatch, [
            {"success": False, "error": "already_playing"},
        ])
        result = await play_smoke_test(FakeCtx(), action="run", seconds=5)
        assert result["success"] is False
        assert result["verdict"] == "fail"
        assert result["reason"] == "already_playing"
        # No status polling should have happened.
        assert len(t.calls) == 1

    @pytest.mark.asyncio
    async def test_start_without_job_id_is_fail(self, monkeypatch):
        _install(monkeypatch, [
            {"success": True, "data": {}},  # start succeeded but no job_id
        ])
        result = await play_smoke_test(FakeCtx(), action="run", seconds=5)
        assert result["success"] is False
        assert "job_id" in result["reason"]

    @pytest.mark.asyncio
    async def test_transient_status_failures_are_tolerated(self, monkeypatch):
        # The play-entry domain reload can make status calls briefly fail; the loop
        # must keep polling instead of aborting.
        _install(monkeypatch, [
            {"success": True, "data": {"job_id": "job-4"}},   # start
            {"success": False, "error": "bridge reloading"},  # transient
            {"success": False, "error": "bridge reloading"},  # transient
            {"success": True, "data": {
                "status": "done",
                "error_count": 0,
                "exception_count": 0,
                "warning_count": 0,
            }},
        ])
        result = await play_smoke_test(FakeCtx(), action="run", seconds=5)
        assert result["completed"] is True
        assert result["verdict"] == "pass"

    @pytest.mark.asyncio
    async def test_timeout_path(self, monkeypatch):
        # Job never reaches a terminal state; the overall timeout must fire and
        # return a fail verdict with completed=False.
        _install(monkeypatch, [
            {"success": True, "data": {"job_id": "job-5"}},
        ])  # every later poll returns the transport default {status: running}
        result = await play_smoke_test(
            FakeCtx(), action="run", seconds=1, timeout_seconds=1
        )
        assert result["success"] is True
        assert result["completed"] is False
        assert result["verdict"] == "fail"
        assert "timed out" in result["reason"].lower()


# --- Safety classification --------------------------------------------------

class TestSafetyClassification:
    def test_play_smoke_test_is_validate(self):
        assert "play_smoke_test" in TOOL_POLICIES
        assert TOOL_POLICIES["play_smoke_test"].default is ToolClass.VALIDATE

    @pytest.mark.parametrize("action", ["run", "start", "status"])
    def test_classify_call_validate_for_all_actions(self, action):
        c = classify_call("play_smoke_test", {"action": action})
        assert c.tool_class is ToolClass.VALIDATE
        assert c.known_tool is True
