"""
Append-only JSONL audit log for MCP tool calls.

One record is written per tool call with the safety decision and outcome.
Records never contain full argument payloads: values are truncated so the
log stays small and does not become a second copy of every script edit.

Location: <log dir>/unity_mcp_audit.jsonl, where <log dir> follows
utils.log_paths.resolve_log_dir (override with UNITY_MCP_AUDIT_LOG_DIR).
Disable entirely with UNITY_MCP_AUDIT_LOG=0.

Audit failures are never allowed to break a tool call: every write is
wrapped and logged at debug level on error.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("mcp-for-unity-server")

_MAX_VALUE_CHARS = 200
_MAX_KEYS = 32

AUDIT_FILE_NAME = "unity_mcp_audit.jsonl"


def audit_enabled(env: dict[str, str] | None = None) -> bool:
    raw = (env or os.environ).get("UNITY_MCP_AUDIT_LOG", "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def resolve_audit_path(env: dict[str, str] | None = None) -> str:
    environ = env or os.environ
    override = environ.get("UNITY_MCP_AUDIT_LOG_DIR")
    if override:
        base = os.path.expanduser(override)
    else:
        from utils.log_paths import resolve_log_dir
        base = resolve_log_dir()
    return os.path.join(base, AUDIT_FILE_NAME)


def summarize_arguments(arguments: dict[str, Any] | None) -> dict[str, Any]:
    """Produce a compact, redaction-friendly summary of tool arguments."""
    if not isinstance(arguments, dict):
        return {}

    summary: dict[str, Any] = {}
    for index, (key, value) in enumerate(arguments.items()):
        if index >= _MAX_KEYS:
            summary["_truncated_keys"] = len(arguments) - _MAX_KEYS
            break
        summary[str(key)] = _summarize_value(value)
    return summary


def _summarize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= _MAX_VALUE_CHARS:
            return value
        return f"{value[:_MAX_VALUE_CHARS]}…(+{len(value) - _MAX_VALUE_CHARS} chars)"
    if isinstance(value, list):
        return f"<list len={len(value)}>"
    if isinstance(value, dict):
        return f"<dict keys={sorted(str(k) for k in list(value)[:8])}>"
    return f"<{type(value).__name__}>"


class AuditLogger:
    """Thread-safe append-only JSONL writer."""

    def __init__(self, path: str | None = None, enabled: bool | None = None):
        self._enabled = audit_enabled() if enabled is None else enabled
        self._path = path
        self._lock = threading.Lock()
        self._warned = False

    @property
    def path(self) -> str | None:
        if self._path is None and self._enabled:
            try:
                self._path = resolve_audit_path()
            except Exception:
                logger.debug("Failed to resolve audit log path", exc_info=True)
                self._enabled = False
        return self._path

    def record(self, event: dict[str, Any]) -> None:
        if not self._enabled:
            return
        path = self.path
        if not path:
            return
        payload = {"ts": datetime.now(timezone.utc).isoformat(), **event}
        try:
            line = json.dumps(payload, ensure_ascii=False, default=str)
            with self._lock:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            if not self._warned:
                self._warned = True
                logger.warning(
                    "Audit log write failed (path=%s); further failures logged at debug",
                    path, exc_info=True,
                )
            else:
                logger.debug("Audit log write failed", exc_info=True)


_audit_logger: AuditLogger | None = None
_audit_lock = threading.Lock()


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        with _audit_lock:
            if _audit_logger is None:
                _audit_logger = AuditLogger()
    return _audit_logger


def reset_audit_logger() -> None:
    """Test seam: drop the singleton so the next call re-reads the environment."""
    global _audit_logger
    with _audit_lock:
        _audit_logger = None
