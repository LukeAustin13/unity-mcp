"""Tests for run_tests_and_summarize (composition over run_tests + get_test_job)."""
import os
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
    _extract_source_context,
    _parse_stack_location,
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


# --- test-failure triage: stack parsing --------------------------------------


class TestStackLocationParsing:
    def test_posix_in_path_line(self):
        st = "  at A.B () [0x00000] in /home/u/proj/Assets/Foo.cs:42"
        assert _parse_stack_location(st) == ("/home/u/proj/Assets/Foo.cs", 42)

    def test_windows_in_path_line(self):
        st = r"  at A.B () [0x0] in C:\Users\me\proj\Assets\Foo.cs:117"
        assert _parse_stack_location(st) == (r"C:\Users\me\proj\Assets\Foo.cs", 117)

    def test_in_path_line_keyword(self):
        st = "Stack:\n  in /src/Bar.cs:line 9\n"
        assert _parse_stack_location(st) == ("/src/Bar.cs", 9)

    def test_none_when_no_frame(self):
        assert _parse_stack_location("no location here") is None
        assert _parse_stack_location(None) is None
        assert _parse_stack_location("") is None

    def test_prefers_locally_existing_frame(self, tmp_path):
        real = tmp_path / "Real.cs"
        real.write_text("line1\nline2\n", encoding="utf-8")
        st = (
            "  at A.Helper () in /does/not/exist/Ghost.cs:5\n"
            f"  at A.Test () in {real}:2\n"
        )
        assert _parse_stack_location(st) == (str(real), 2)


class TestSourceContextExtraction:
    def _write(self, tmp_path, n=20):
        p = tmp_path / "Sample.cs"
        p.write_text("\n".join(f"code line {i}" for i in range(1, n + 1)) + "\n",
                     encoding="utf-8")
        return str(p)

    def test_extracts_window_and_marks_failing_line(self, tmp_path):
        path = self._write(tmp_path)
        ctx = _extract_source_context(path, 10, radius=5)
        assert ctx is not None
        assert ctx["file"] == path
        assert ctx["line"] == 10
        assert ctx["start_line"] == 5
        assert ctx["end_line"] == 15
        marked = [ln for ln in ctx["lines"] if ln.startswith(">")]
        assert len(marked) == 1
        assert "10:" in marked[0]

    def test_clamps_at_file_start(self, tmp_path):
        path = self._write(tmp_path)
        ctx = _extract_source_context(path, 2, radius=5)
        assert ctx["start_line"] == 1

    def test_missing_file_returns_none(self):
        assert _extract_source_context("/no/such/file_xyz.cs", 3) is None

    def test_line_out_of_range_returns_none(self, tmp_path):
        path = self._write(tmp_path, n=5)
        assert _extract_source_context(path, 999) is None


# --- test-failure triage: source-context integration -------------------------


@pytest.mark.asyncio
async def test_source_context_attached_when_local(monkeypatch, tmp_path):
    src = tmp_path / "FailTest.cs"
    src.write_text("\n".join(f"L{i}" for i in range(1, 30)) + "\n", encoding="utf-8")
    stack = f"  at NUnit.X () in {src}:12"
    results = [RunTestsTestResult(
        name="t1", fullName="A.t1", state="Failed", durationSeconds=0.1,
        message="boom", stackTrace=stack)]
    monkeypatch.setattr(rt, "run_tests", AsyncMock(return_value=_start_ok()))
    monkeypatch.setattr(rt, "get_test_job", AsyncMock(return_value=_job(
        "failed",
        summary=RunTestsSummary(total=1, passed=0, failed=1, skipped=0,
                                durationSeconds=0.1, resultState="Failed"),
        results=results)))

    out = await run_tests_and_summarize(FakeCtx(), include_source_context=True)

    ctx = out["failing_tests"][0]["source_context"]
    assert ctx["line"] == 12
    assert ctx["file"] == str(src)


@pytest.mark.asyncio
async def test_source_context_skipped_when_file_missing(monkeypatch):
    stack = "  at NUnit.X () in /no/such/remote/File.cs:12"
    results = [RunTestsTestResult(
        name="t1", fullName="A.t1", state="Failed", durationSeconds=0.1,
        message="boom", stackTrace=stack)]
    monkeypatch.setattr(rt, "run_tests", AsyncMock(return_value=_start_ok()))
    monkeypatch.setattr(rt, "get_test_job", AsyncMock(return_value=_job(
        "failed",
        summary=RunTestsSummary(total=1, passed=0, failed=1, skipped=0,
                                durationSeconds=0.1, resultState="Failed"),
        results=results)))

    out = await run_tests_and_summarize(FakeCtx(), include_source_context=True)

    # Graceful: no source_context key, and no error raised.
    assert "source_context" not in out["failing_tests"][0]


@pytest.mark.asyncio
async def test_defaults_off_produce_no_triage_keys(monkeypatch, tmp_path):
    src = tmp_path / "FailTest.cs"
    src.write_text("L1\nL2\nL3\n", encoding="utf-8")
    results = [RunTestsTestResult(
        name="t1", fullName="A.t1", state="Failed", durationSeconds=0.1,
        message="boom", stackTrace=f"  in {src}:2")]
    monkeypatch.setattr(rt, "run_tests", AsyncMock(return_value=_start_ok()))
    monkeypatch.setattr(rt, "get_test_job", AsyncMock(return_value=_job(
        "failed",
        summary=RunTestsSummary(total=1, passed=0, failed=1, skipped=0,
                                durationSeconds=0.1, resultState="Failed"),
        results=results)))

    out = await run_tests_and_summarize(FakeCtx())

    assert "source_context" not in out["failing_tests"][0]
    assert "flaky_tests" not in out["summary"]
    assert "deterministic_failures" not in out["summary"]


# --- test-failure triage: retry classification -------------------------------


def _fail_result(name):
    return RunTestsTestResult(name=name.split(".")[-1], fullName=name,
                              state="Failed", durationSeconds=0.1, message="boom")


class _SeqAsyncMock:
    """Returns queued values in order; repeats the last one after exhaustion."""

    def __init__(self, values):
        self._values = list(values)
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        idx = min(len(self.calls) - 1, len(self._values) - 1)
        return self._values[idx]


@pytest.mark.asyncio
async def test_retry_classifies_flaky_and_deterministic(monkeypatch):
    # Initial run: two failures.
    initial = _job(
        "failed",
        summary=RunTestsSummary(total=2, passed=0, failed=2, skipped=0,
                                durationSeconds=0.1, resultState="Failed"),
        results=[_fail_result("A.flaky"), _fail_result("A.stubborn")])
    # Retry run: only A.stubborn still fails; A.flaky passed (absent).
    retry = _job(
        "failed",
        summary=RunTestsSummary(total=2, passed=1, failed=1, skipped=0,
                                durationSeconds=0.1, resultState="Failed"),
        results=[_fail_result("A.stubborn")])

    monkeypatch.setattr(rt, "run_tests", AsyncMock(return_value=_start_ok()))
    monkeypatch.setattr(rt, "get_test_job", _SeqAsyncMock([initial, retry]))

    out = await run_tests_and_summarize(FakeCtx(), retry_failed=1)

    assert out["summary"]["flaky_tests"] == ["A.flaky"]
    assert out["summary"]["deterministic_failures"] == ["A.stubborn"]


@pytest.mark.asyncio
async def test_retry_all_pass_marks_all_flaky(monkeypatch):
    initial = _job(
        "failed",
        summary=RunTestsSummary(total=1, passed=0, failed=1, skipped=0,
                                durationSeconds=0.1, resultState="Failed"),
        results=[_fail_result("A.t1")])
    retry_pass = _job(
        "succeeded",
        summary=RunTestsSummary(total=1, passed=1, failed=0, skipped=0,
                                durationSeconds=0.1, resultState="Passed"),
        results=[])

    monkeypatch.setattr(rt, "run_tests", AsyncMock(return_value=_start_ok()))
    monkeypatch.setattr(rt, "get_test_job", _SeqAsyncMock([initial, retry_pass]))

    out = await run_tests_and_summarize(FakeCtx(), retry_failed=2)

    assert out["summary"]["flaky_tests"] == ["A.t1"]
    assert out["summary"]["deterministic_failures"] == []


@pytest.mark.asyncio
async def test_retry_capped_at_three(monkeypatch):
    initial = _job(
        "failed",
        summary=RunTestsSummary(total=1, passed=0, failed=1, skipped=0,
                                durationSeconds=0.1, resultState="Failed"),
        results=[_fail_result("A.t1")])
    retry_fail = _job(
        "failed",
        summary=RunTestsSummary(total=1, passed=0, failed=1, skipped=0,
                                durationSeconds=0.1, resultState="Failed"),
        results=[_fail_result("A.t1")])

    start_mock = AsyncMock(return_value=_start_ok())
    monkeypatch.setattr(rt, "run_tests", start_mock)
    monkeypatch.setattr(rt, "get_test_job", _SeqAsyncMock([initial, retry_fail]))

    out = await run_tests_and_summarize(FakeCtx(), retry_failed=99)

    # 1 initial start + at most 3 retries.
    assert start_mock.await_count <= 4
    assert out["summary"]["deterministic_failures"] == ["A.t1"]
    assert out["summary"]["flaky_tests"] == []


@pytest.mark.asyncio
async def test_retry_uses_test_names_filter(monkeypatch):
    initial = _job(
        "failed",
        summary=RunTestsSummary(total=1, passed=0, failed=1, skipped=0,
                                durationSeconds=0.1, resultState="Failed"),
        results=[_fail_result("A.only")])
    retry = _job(
        "succeeded",
        summary=RunTestsSummary(total=1, passed=1, failed=0, skipped=0,
                                durationSeconds=0.1, resultState="Passed"),
        results=[])

    start_mock = _SeqAsyncMock([_start_ok(), _start_ok()])
    monkeypatch.setattr(rt, "run_tests", start_mock)
    monkeypatch.setattr(rt, "get_test_job", _SeqAsyncMock([initial, retry]))

    await run_tests_and_summarize(FakeCtx(), retry_failed=1)

    # The second run_tests call (the retry) must filter by the failed test name.
    retry_call_kwargs = start_mock.calls[1][1]
    assert retry_call_kwargs["test_names"] == ["A.only"]


@pytest.mark.asyncio
async def test_retry_skipped_when_no_failures(monkeypatch):
    start_mock = AsyncMock(return_value=_start_ok())
    monkeypatch.setattr(rt, "run_tests", start_mock)
    monkeypatch.setattr(rt, "get_test_job", AsyncMock(return_value=_job(
        "succeeded",
        summary=RunTestsSummary(total=1, passed=1, failed=0, skipped=0,
                                durationSeconds=0.1, resultState="Passed"),
        results=[])))

    out = await run_tests_and_summarize(FakeCtx(), retry_failed=3)

    # No failures -> no retry run, no classification keys.
    assert start_mock.await_count == 1
    assert "flaky_tests" not in out["summary"]
