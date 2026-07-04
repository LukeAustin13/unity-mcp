"""Tests for the audit read/summarize helpers and the audit CLI."""
import json

import pytest
from click.testing import CliRunner

from core.audit import read_audit_records, summarize_audit


def _write(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


SAMPLE = [
    {"ts": "t1", "tool": "manage_scene", "action": "get_active", "classification": "read", "mode": "read_only", "decision": "allow"},
    {"ts": "t2", "tool": "manage_asset", "action": "delete", "classification": "destructive", "mode": "read_only", "decision": "block", "reason": "read_only blocks this"},
    {"ts": "t3", "tool": "manage_asset", "action": "delete", "classification": "destructive", "mode": "write", "decision": "confirm_required", "reason": "needs confirm"},
    {"ts": "t4", "tool": "manage_scene", "action": "save", "classification": "write", "mode": "write", "decision": "allow"},
]


class TestReadAndSummarize:
    def test_missing_file_is_empty(self, tmp_path):
        assert read_audit_records(path=str(tmp_path / "nope.jsonl")) == []

    def test_reads_and_skips_malformed(self, tmp_path):
        p = tmp_path / "a.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(SAMPLE[0]) + "\n")
            f.write("{not json}\n")
            f.write("\n")
            f.write(json.dumps(SAMPLE[1]) + "\n")
        recs = read_audit_records(path=str(p))
        assert [r["ts"] for r in recs] == ["t1", "t2"]

    def test_limit_returns_most_recent(self, tmp_path):
        p = tmp_path / "a.jsonl"
        _write(p, SAMPLE)
        recs = read_audit_records(path=str(p), limit=2)
        assert [r["ts"] for r in recs] == ["t3", "t4"]

    def test_summary_counts(self, tmp_path):
        s = summarize_audit(SAMPLE)
        assert s["total"] == 4
        assert s["by_decision"] == {"allow": 2, "block": 1, "confirm_required": 1}
        assert s["by_classification"]["destructive"] == 2
        assert s["blocked_count"] == 2
        assert {b["tool"] for b in s["recent_blocks"]} == {"manage_asset"}

    def test_summary_empty(self):
        s = summarize_audit([])
        assert s["total"] == 0
        assert s["recent_blocks"] == []

    def test_summary_tolerates_nonstring_reason(self):
        # A crafted/odd record must not crash the summary.
        recs = [
            {"decision": "block", "reason": 12345, "tool": "x"},
            {"decision": "confirm_required", "reason": ["a", "b"], "tool": "y"},
            {"decision": "block", "reason": None, "tool": "z"},
        ]
        s = summarize_audit(recs)
        assert s["blocked_count"] == 3
        assert all(isinstance(b["reason"], str) for b in s["recent_blocks"])


class TestAuditCli:
    def test_tail(self, tmp_path):
        from cli.commands.audit import audit
        p = tmp_path / "a.jsonl"
        _write(p, SAMPLE)
        r = CliRunner().invoke(audit, ["tail", "--path", str(p), "-n", "10"])
        assert r.exit_code == 0
        assert "manage_asset(delete)" in r.output
        assert "manage_scene(get_active)" in r.output

    def test_tail_blocked_only(self, tmp_path):
        from cli.commands.audit import audit
        p = tmp_path / "a.jsonl"
        _write(p, SAMPLE)
        r = CliRunner().invoke(audit, ["tail", "--path", str(p), "--blocked-only"])
        assert r.exit_code == 0
        assert "block" in r.output
        assert "get_active" not in r.output  # the allow is filtered out

    def test_tail_empty(self, tmp_path):
        from cli.commands.audit import audit
        r = CliRunner().invoke(audit, ["tail", "--path", str(tmp_path / "nope.jsonl")])
        assert r.exit_code == 0
        assert "No audit records" in r.output

    def test_summary(self, tmp_path):
        from cli.commands.audit import audit
        p = tmp_path / "a.jsonl"
        _write(p, SAMPLE)
        r = CliRunner().invoke(audit, ["summary", "--path", str(p)])
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["total"] == 4
        assert data["blocked_count"] == 2

    def test_registered_in_cli(self):
        from cli.main import cli
        assert "audit" in cli.commands
