"""Tests for read_console format='summary' (Python-side grouping).

format='summary' is implemented entirely on the Python side: it fetches entries
as structured 'detailed' records with stack traces, then collapses them into
ranked signature groups. These tests verify:

  - grouping by (type, normalized first line),
  - digit/hex/guid normalization so near-identical errors collapse,
  - file:line extraction from the topmost user stack frame,
  - the 25-group cap + suppressed count,
  - that non-summary formats are byte-identical pass-through (untouched).
"""
import services.tools.read_console as rc
from services.tools.read_console import (
    SUMMARY_GROUP_CAP,
    _first_file_line,
    _normalize_signature,
    _summarize_entries,
    read_console,
)

import pytest


class FakeCtx:
    client_id = "test"

    async def get_state(self, key):
        return None


def _entry(message, type_="Error", stack=None):
    return {"type": type_, "message": message, "file": "", "line": 0, "stackTrace": stack}


@pytest.fixture
def transport(monkeypatch):
    """Capture the params sent to Unity and return a scripted response."""
    state = {"params": None, "response": {"success": True, "data": []}}

    async def fake_send(send_fn, unity_instance, command, params):
        state["params"] = params
        state["command"] = command
        return state["response"]

    monkeypatch.setattr(rc, "send_with_unity_instance", fake_send)
    return state


# --- Pure helpers -----------------------------------------------------------

class TestNormalization:
    def test_digits_collapsed(self):
        a = _normalize_signature("NullReferenceException at index 42")
        b = _normalize_signature("NullReferenceException at index 9999")
        assert a == b

    def test_guid_collapsed(self):
        a = _normalize_signature("Missing asset 3f2504e0-4f89-41d3-9a0c-0305e82c3301")
        b = _normalize_signature("Missing asset 8e7a0f11-1234-4bcd-9a0c-0305e82c3399")
        assert a == b

    def test_hex_pointer_collapsed(self):
        a = _normalize_signature("Object at 0xDEADBEEF was destroyed")
        b = _normalize_signature("Object at 0x00ABCDEF was destroyed")
        assert a == b

    def test_only_first_line_used(self):
        sig = _normalize_signature("Boom!\n  at Foo.Bar () (at Assets/Foo.cs:12)")
        assert "foo.cs" not in sig
        assert sig.startswith("boom")

    def test_case_and_whitespace_normalized(self):
        a = _normalize_signature("Some   Error   Happened")
        b = _normalize_signature("some error happened")
        assert a == b

    def test_distinct_messages_stay_distinct(self):
        a = _normalize_signature("NullReferenceException")
        b = _normalize_signature("IndexOutOfRangeException")
        assert a != b


class TestFileLineExtraction:
    def test_extracts_topmost_user_frame(self):
        stack = (
            "NullReferenceException\n"
            "  at Game.Player.Update () (at Assets/Scripts/Player.cs:57)\n"
            "  at UnityEngine.Something () (at Assets/Other.cs:99)"
        )
        assert _first_file_line(stack) == "Assets/Scripts/Player.cs:57"

    def test_none_when_no_frame(self):
        assert _first_file_line("just a message with no frame") is None

    def test_none_when_stack_none(self):
        assert _first_file_line(None) is None


class TestSummarizeEntries:
    def test_groups_by_type_and_signature(self):
        entries = [
            _entry("Error A at 1"),
            _entry("Error A at 2"),
            _entry("Error A at 3"),
            _entry("Error B"),
        ]
        summary = _summarize_entries(entries)
        assert summary["total_entries"] == 4
        assert summary["group_count"] == 2
        # Ranked count-desc: the 3x group first.
        assert summary["groups"][0]["count"] == 3
        assert summary["groups"][1]["count"] == 1

    def test_same_message_different_type_are_separate_groups(self):
        entries = [
            _entry("Duplicate", type_="Error"),
            _entry("Duplicate", type_="Warning"),
        ]
        summary = _summarize_entries(entries)
        assert summary["group_count"] == 2

    def test_sample_message_is_first_line(self):
        entries = [_entry("Line one\nLine two\nLine three")]
        summary = _summarize_entries(entries)
        assert summary["groups"][0]["sample_message"] == "Line one"

    def test_first_file_line_captured_from_any_member(self):
        # First member has no stack; a later member in the same group carries it.
        entries = [
            _entry("Boom at 1", stack=None),
            _entry("Boom at 2", stack="  at X () (at Assets/X.cs:10)"),
        ]
        summary = _summarize_entries(entries)
        assert summary["groups"][0]["count"] == 2
        assert summary["groups"][0]["first_file_line"] == "Assets/X.cs:10"

    def test_cap_and_suppressed_count(self):
        # 30 distinct signatures -> capped at 25, suppressed = 5.
        entries = [_entry(f"Unique error kind {chr(65 + i)}{i}xx") for i in range(30)]
        # Ensure signatures are distinct by using distinct alpha tokens.
        entries = [_entry(f"ErrorKind{chr(65 + i)}") for i in range(30)]
        summary = _summarize_entries(entries)
        assert summary["group_count"] == 30
        assert len(summary["groups"]) == SUMMARY_GROUP_CAP == 25
        assert summary["suppressed"] == 5

    def test_empty_entries(self):
        summary = _summarize_entries([])
        assert summary["total_entries"] == 0
        assert summary["groups"] == []
        assert summary["suppressed"] == 0


# --- Tool integration -------------------------------------------------------

class TestSummaryFormatWiring:
    @pytest.mark.asyncio
    async def test_summary_forces_detailed_and_stacktrace_on_wire(self, transport):
        transport["response"] = {"success": True, "data": [_entry("X at 1")]}
        await read_console(FakeCtx(), action="get", format="summary")
        # The C# side must never receive 'summary'; it gets detailed + stacktrace.
        assert transport["params"]["format"] == "detailed"
        assert transport["params"]["includeStacktrace"] is True

    @pytest.mark.asyncio
    async def test_summary_returns_grouped_payload(self, transport):
        transport["response"] = {
            "success": True,
            "data": [
                _entry("Null at 1", stack="  at A () (at Assets/A.cs:3)"),
                _entry("Null at 2", stack="  at A () (at Assets/A.cs:3)"),
                _entry("Other error"),
            ],
        }
        result = await read_console(FakeCtx(), action="get", format="summary")
        assert result["success"] is True
        data = result["data"]
        assert data["total_entries"] == 3
        assert data["group_count"] == 2
        assert data["groups"][0]["count"] == 2
        assert data["groups"][0]["first_file_line"] == "Assets/A.cs:3"

    @pytest.mark.asyncio
    async def test_summary_handles_paged_items_payload(self, transport):
        transport["response"] = {
            "success": True,
            "data": {"items": [_entry("Boom at 1"), _entry("Boom at 2")], "total": 2},
        }
        result = await read_console(FakeCtx(), action="get", format="summary", page_size=50)
        assert result["success"] is True
        assert result["data"]["total_entries"] == 2
        assert result["data"]["group_count"] == 1

    @pytest.mark.asyncio
    async def test_summary_error_passthrough(self, transport):
        transport["response"] = {"success": False, "error": "boom"}
        result = await read_console(FakeCtx(), action="get", format="summary")
        assert result["success"] is False


class TestNonSummaryUntouched:
    """Non-summary formats must be byte-identical pass-through behaviour."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fmt", ["plain", "detailed", "json"])
    async def test_wire_format_unchanged(self, transport, fmt):
        transport["response"] = {"success": True, "data": []}
        await read_console(FakeCtx(), action="get", format=fmt)
        assert transport["params"]["format"] == fmt

    @pytest.mark.asyncio
    async def test_detailed_response_returned_verbatim(self, transport):
        payload = {"success": True, "data": [_entry("keep me exactly")]}
        transport["response"] = payload
        result = await read_console(
            FakeCtx(), action="get", format="detailed", include_stacktrace=True
        )
        # include_stacktrace=True means the stripping branch is skipped and the
        # response object is returned exactly as received.
        assert result is payload

    @pytest.mark.asyncio
    async def test_default_format_is_plain(self, transport):
        transport["response"] = {"success": True, "data": []}
        await read_console(FakeCtx(), action="get")
        assert transport["params"]["format"] == "plain"
