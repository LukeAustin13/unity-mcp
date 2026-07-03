"""
FastMCP middleware that enforces the configured safety mode and writes the
audit log for every MCP tool call.

All policy logic lives in core.enforcement (shared with the /api/command
route); this middleware is a thin adapter that strips the reserved
``confirm`` argument, calls the shared enforcer, and translates a
``PolicyViolation`` into a FastMCP ``ToolError``.

Runs before UnityInstanceMiddleware so blocked calls never trigger instance
discovery or reach Unity. Like unity_instance routing, the reserved
``confirm`` argument is popped here before FastMCP validates arguments
against the tool signature, so tool schemas stay unchanged.
"""
from __future__ import annotations

import logging
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext

from core.audit import AuditLogger, get_audit_logger
from core.enforcement import PolicyViolation, coerce_confirm, enforce_tool_call

logger = logging.getLogger("mcp-for-unity-server")


class SafetyMiddleware(Middleware):
    """Classify each tool call, enforce the safety policy, and audit the result."""

    def __init__(self, audit_logger: AuditLogger | None = None):
        super().__init__()
        self._audit = audit_logger

    @property
    def audit(self) -> AuditLogger:
        if self._audit is None:
            self._audit = get_audit_logger()
        return self._audit

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        message = getattr(context, "message", None)
        tool_name = getattr(message, "name", None) or "<unknown>"
        arguments = getattr(message, "arguments", None)

        # Always strip the reserved key so tool schemas never reject it.
        confirmed = False
        if isinstance(arguments, dict) and "confirm" in arguments:
            confirmed = coerce_confirm(arguments.pop("confirm"))

        try:
            decision = enforce_tool_call(
                tool_name,
                arguments,
                confirmed=confirmed,
                client=self._client_key(context),
                source="mcp",
                audit_logger=self.audit,
            )
        except PolicyViolation as pv:
            raise ToolError(pv.reason)

        try:
            result = await call_next(context)
        except Exception as exc:
            decision.finalize(self.audit, "error", exc)
            raise

        decision.finalize(self.audit, "ok")
        return result

    @staticmethod
    def _client_key(context: MiddlewareContext) -> str:
        ctx = getattr(context, "fastmcp_context", None)
        client_id = getattr(ctx, "client_id", None)
        if isinstance(client_id, str) and client_id:
            return client_id
        return "global"


# Backwards-compat re-export: some callers/tests import coerce_confirm from here.
__all__ = ["SafetyMiddleware", "coerce_confirm"]
