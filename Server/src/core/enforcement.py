"""
Single source of truth for tool-call safety enforcement.

Every command ingress — the FastMCP tool middleware AND the local
``/api/command`` HTTP route used by the CLI — calls ``enforce_tool_call``.
There is exactly one place that decides allow/block/confirm, so a call can
never reach Unity through one path while being gated on another.

Layers applied, in order:
  1. Mode/class check (core.safety.evaluate_call) — read_only/review_only/write.
  2. Tool-specific guards (execute_code enablement, execute_menu_item
     allowlist, manage_build output-path sandbox), recursed into
     batch_execute members so a batch can't smuggle a guarded call.
  3. Destructive confirmation-acknowledgement metadata (``confirm: true``).

Confirmation is *not* human confirmation. It is destructive-operation
acknowledgement metadata that an agent must set deliberately. Real safety
comes from the externally-configured safety mode and the guards above.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from core.audit import summarize_arguments
from core.safety import (
    Classification,
    Decision,
    build_output_guard,
    classify_call,
    evaluate_call,
    execute_code_guard,
    execute_menu_item_guard,
    get_safety_mode,
)

logger = logging.getLogger("mcp-for-unity-server")


class PolicyViolation(Exception):
    """Raised when a tool call is refused by the safety policy.

    Callers translate this into their transport's error shape (ToolError for
    MCP, an HTTP 403 for the API route). The message is safe to surface.
    """

    def __init__(self, reason: str, decision: Decision):
        super().__init__(reason)
        self.reason = reason
        self.decision = decision


def coerce_confirm(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    if isinstance(value, (int, float)):
        return value == 1
    return False


# Mirror Unity's StringCaseUtility.ToSnakeCase EXACTLY — insert '_' only between
# a lower/digit and an upper — so the canonical policy view provably matches the
# key Unity's ToolParams will resolve. Diverging here would reopen the key-parity
# bug class for any future acronym/digit-boundary key.
_CAMEL_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


def _to_snake(key: str) -> str:
    return _CAMEL_BOUNDARY.sub(r"\1_\2", key).lower() if isinstance(key, str) else key


def canonicalize_keys(args: Any) -> tuple[Any, bool]:
    """Return a snake_case *view* of argument keys, and whether it was ambiguous.

    Unity's ToolParams resolves a key under snake_case AND camelCase, so a guard
    or classifier that inspects only one spelling can be bypassed by sending the
    value under the other. Canonicalising every key to snake_case here gives the
    policy layer a single, unambiguous view that provably matches what the Editor
    will read. Recurses into batch_execute command params. If two spellings of
    the same logical key are present with DIFFERING values, the result is flagged
    ambiguous so the caller can fail closed rather than guess which the Editor
    uses. This view is used only for policy decisions; the original arguments are
    still what is forwarded to Unity.
    """
    if not isinstance(args, dict):
        return args, False
    out: dict[str, Any] = {}
    ambiguous = False
    for key, value in args.items():
        snake = _to_snake(key)
        new_value = value
        if snake == "commands" and isinstance(value, list):
            new_value = []
            for command in value:
                if isinstance(command, dict):
                    normalized_command = dict(command)
                    params = command.get("params")
                    if isinstance(params, dict):
                        norm_params, nested_ambiguous = canonicalize_keys(params)
                        normalized_command["params"] = norm_params
                        ambiguous = ambiguous or nested_ambiguous
                    new_value.append(normalized_command)
                else:
                    new_value.append(command)
        if snake in out and out[snake] != new_value:
            ambiguous = True
        out[snake] = new_value
    return out, ambiguous


@dataclass
class EnforcementDecision:
    """Result of an allowed enforcement check; caller records the outcome."""
    classification: Classification
    record: dict[str, Any]

    def finalize(self, audit_logger, outcome: str, error: Any | None = None) -> None:
        rec = dict(self.record)
        rec["outcome"] = outcome
        if error is not None:
            rec["error"] = str(error)[:300]
        if audit_logger is not None:
            audit_logger.record(rec)


def _guard_reason(tool_name: str, arguments: dict[str, Any], config) -> str | None:
    """Apply per-tool guards, recursing into batch_execute members."""
    if tool_name == "batch_execute":
        commands = arguments.get("commands")
        if isinstance(commands, list):
            for command in commands:
                if not isinstance(command, dict):
                    continue
                nested_tool = command.get("tool")
                nested_params = command.get("params")
                if not isinstance(nested_tool, str):
                    continue
                reason = _guard_reason(
                    nested_tool,
                    nested_params if isinstance(nested_params, dict) else {},
                    config,
                )
                if reason:
                    return f"batch command '{nested_tool}': {reason}"
        return None

    if tool_name == "execute_code":
        return execute_code_guard(
            arguments, allow=getattr(config, "allow_execute_code", False))

    if tool_name == "execute_menu_item":
        from core.safety import DEFAULT_MENU_ITEM_ALLOWLIST
        extra = getattr(config, "menu_item_allowlist", None) or []
        allowlist = set(DEFAULT_MENU_ITEM_ALLOWLIST) | {
            str(item).strip() for item in extra if str(item).strip()
        }
        return execute_menu_item_guard(
            arguments,
            allowlist=allowlist,
            allow_arbitrary=getattr(config, "allow_arbitrary_menu_items", False),
        )

    if tool_name == "manage_build":
        from core.safety import action_of
        if action_of(arguments) in ("build", "batch"):
            return build_output_guard(
                arguments,
                allow_external=getattr(config, "allow_external_build_output", False),
            )
        return None

    return None


def enforce_tool_call(
    tool_name: str,
    arguments: dict[str, Any] | None,
    *,
    confirmed: bool,
    client: str,
    source: str,
    audit_logger=None,
    audit_allow: bool = False,
) -> EnforcementDecision:
    """Enforce safety policy for a single tool call.

    Blocks are audited here and raised as ``PolicyViolation``. Allowed calls
    return an ``EnforcementDecision``; the caller records the execution outcome
    via ``decision.finalize(...)``. Set ``audit_allow=True`` for ingress paths
    (e.g. the API route) that cannot conveniently finalize later — the allow is
    then audited immediately.
    """
    from core.config import config

    raw_args = arguments if isinstance(arguments, dict) else {}
    # Canonicalise keys to snake_case for ALL policy checks so a guard/classifier
    # cannot be bypassed by sending a value under a camelCase spelling the Editor
    # still resolves. The original args are what gets forwarded to Unity.
    args, ambiguous = canonicalize_keys(raw_args)
    mode = get_safety_mode()
    safety = evaluate_call(mode, tool_name, args, confirmed)
    classification = safety.classification

    record: dict[str, Any] = {
        "event": "tool_call",
        "tool": tool_name,
        "action": classification.action,
        "classification": classification.tool_class.value,
        "known_tool": classification.known_tool,
        "mode": mode.value,
        "client": client,
        "source": source,
        "confirmed": bool(confirmed),
        "params": summarize_arguments(raw_args),
    }

    # 0. Ambiguous duplicate spellings (snake vs camel with differing values):
    #    refuse rather than guess which the Editor will use.
    if ambiguous:
        _emit_block(
            audit_logger, record, Decision.BLOCK,
            "Ambiguous duplicate parameter keys (snake_case and camelCase "
            "variants with differing values). Send each parameter once.",
        )

    # 1. Mode/class block takes precedence (covers read_only/review_only).
    if safety.decision is Decision.BLOCK:
        _emit_block(audit_logger, record, Decision.BLOCK, safety.reason)

    # 2. Tool-specific guards (apply even in write mode). Run before the
    #    confirm gate so a disabled surface reports the real reason.
    guard_reason = _guard_reason(tool_name, args, config)
    if guard_reason is not None:
        _emit_block(audit_logger, record, Decision.BLOCK, guard_reason)

    # 3. Destructive confirmation-acknowledgement metadata.
    if safety.decision is Decision.CONFIRM_REQUIRED:
        _emit_block(audit_logger, record, Decision.CONFIRM_REQUIRED, safety.reason)

    record["decision"] = Decision.ALLOW.value
    decision = EnforcementDecision(classification=classification, record=record)
    if audit_allow and audit_logger is not None:
        decision.finalize(audit_logger, "allowed")
    return decision


def _emit_block(audit_logger, record: dict[str, Any], decision: Decision, reason: str) -> None:
    rec = dict(record)
    rec["decision"] = decision.value
    rec["outcome"] = "blocked"
    rec["reason"] = reason
    if audit_logger is not None:
        audit_logger.record(rec)
    logger.info("Safety policy refused %s: %s", record.get("tool"), decision.value)
    raise PolicyViolation(reason, decision)
