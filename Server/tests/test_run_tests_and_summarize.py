"""Tests for run_tests_and_summarize (composition over run_tests + get_test_job)."""
from unittest.mock import AsyncMock

import pytest

import services.tools.run_tests as rt
from services.tools.run_tests import (
    GetTestJobData,
    GetTestJobResponse,
    RunTestsResult,
    RunTestsStartData,
    RunTestsStartResponse,
    RunTestsSummary,
    RunTestsTestResult,
    run_tests_and_summarize,
)
from models import MCPResponse

# Aliased to non-``Test*`` names so pytest does not try to collect these
# pydantic models as test classes.
JobFailure = rt.TestJobFailure
JobProgress = rt.TestJobProgress


class FakeCtx:
    client_id = "test"

    async def get_state(self, key):
        return None


def _start_ok(job_id="job-1"):
    return RunTestsStartResponse(
        success=True, data=RunTestsStartData(job_id=job_id, status="queued"))


def _job(status, summary=None, results=None, progress=None):
    return GetTestJobResponse(success=True, data=GetTestJobData(
        job_id="job-1", status=status,
        result=RunTestsResult(mode="EditMode", summary=summary, results=results) if summary else None,
        progress=progress,
    ))


@pytest.mark.asyncio
async def test_all_pass_summary(monkeypatch):
    monkeypatch.setattr(rt, "run_tests", AsyncMock(return_value=_start_ok()))
    monkeypatch.setattr(rt, "get_test_job", AsyncMock(return_value=_job(
        "succeeded",
        summary=RunTestsSummary(total=5, passed=5, failed=0, skipped=0,
                                durationSeconds=1.5, resultState="Passed"),
        results=[])))

    out = await run_tests_and_summarize(FakeCtx(), mode="EditMode")

    assert out["success"] is True
    assert out["completed"] is True
    assert out["all_passed"] is True
    assert out["summary"]["passed"] == 5
    assert out["failing_tests"] == []


@pytest.mark.asyncio
async def test_failures_summarized_and_capped(monkeypatch):
    results = [
        RunTestsTestResult(name=f"t{i}", fullName=f"A.t{i}", state="Failed",
                           durationSeconds=0.1, message=f"boom {i}")
        for i in range(30)
    ]
    results.append(RunTestsTestResult(name="ok", fullName="A.ok", state="Passed", durationSeconds=0.1))
    monkeypatch.setattr(rt, "run_tests", AsyncMock(return_value=_start_ok()))
    monkeypatch.setattr(rt, "get_test_job", AsyncMock(return_value=_job(
        "failed",
        summary=RunTestsSummary(total=31, passed=1, failed=30, skipped=0,
                                durationSeconds=2.0, resultState="Failed"),
        results=results)))

    out = await run_tests_and_summarize(FakeCtx(), max_failures=10)

    assert out["all_passed"] is False
    assert out["summary"]["failed"] == 30
    assert len(out["failing_tests"]) == 10
    assert out["failing_tests_truncated"] is True
    assert out["failing_tests"][0]["full_name"] == "A.t0"


@pytest.mark.asyncio
async def test_start_failure_returns_error(monkeypatch):
    monkeypatch.setattr(rt, "run_tests", AsyncMock(return_value=MCPResponse(
        success=False, error="busy", hint="retry")))
    get_mock = AsyncMock()
    monkeypatch.setattr(rt, "get_test_job", get_mock)

    out = await run_tests_and_summarize(FakeCtx())

    assert out["success"] is False
    assert out["error"] == "busy"
    get_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_still_running_reports_progress(monkeypatch):
    monkeypatch.setattr(rt, "run_tests", AsyncMock(return_value=_start_ok()))
    monkeypatch.setattr(rt, "get_test_job", AsyncMock(return_value=_job(
        "running",
        progress=JobProgress(completed=3, total=10, failures_so_far=[
            JobFailure(full_name="A.slow", message="not yet")]))))

    out = await run_tests_and_summarize(FakeCtx(), timeout_seconds=5)

    assert out["success"] is True
    assert out["completed"] is False
    assert out["timed_out"] is True
    assert out["progress"]["completed"] == 3
    assert out["progress"]["failures_so_far"][0]["full_name"] == "A.slow"


@pytest.mark.asyncio
async def test_passes_timeout_to_get_test_job(monkeypatch):
    monkeypatch.setattr(rt, "run_tests", AsyncMock(return_value=_start_ok()))
    get_mock = AsyncMock(return_value=_job(
        "succeeded",
        summary=RunTestsSummary(total=1, passed=1, failed=0, skipped=0,
                                durationSeconds=0.1, resultState="Passed"),
        results=[]))
    monkeypatch.setattr(rt, "get_test_job", get_mock)

    await run_tests_and_summarize(FakeCtx(), timeout_seconds=42)

    _, kwargs = get_mock.call_args
    assert kwargs["wait_timeout"] == 42
    assert kwargs["include_failed_tests"] is True


class TestSummarizeClassification:
    def test_validate_classified(self):
        from core.safety import classify_call, ToolClass
        assert classify_call("run_tests_and_summarize", {}).tool_class is ToolClass.VALIDATE

    def test_allowed_in_review_only_blocked_in_read_only(self):
        from core.safety import evaluate_call, SafetyMode, Decision
        assert evaluate_call(SafetyMode.REVIEW_ONLY, "run_tests_and_summarize", {}).decision is Decision.ALLOW
        assert evaluate_call(SafetyMode.READ_ONLY, "run_tests_and_summarize", {}).decision is Decision.BLOCK
