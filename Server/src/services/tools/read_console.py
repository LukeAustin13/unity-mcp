"""
Defines the read_console tool for accessing Unity Editor console messages.
"""
import re
from typing import Annotated, Any, Literal

from fastmcp import Context
from mcp.types import ToolAnnotations

from services.registry import mcp_for_unity_tool
from services.tools import get_unity_instance_from_context
from services.tools.utils import coerce_int, coerce_bool, parse_json_payload
from transport.unity_transport import send_with_unity_instance
from transport.legacy.unity_connection import async_send_command_with_retry

# Maximum number of grouped signatures returned by format='summary'.
SUMMARY_GROUP_CAP = 25

# Tokens replaced when normalizing a message into a stable grouping signature:
# hex/GUID/pointer/number runs vary between otherwise-identical log lines.
_GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")
_LONGHEX_RE = re.compile(r"\b[0-9a-fA-F]{6,}\b")
_NUM_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")

# Matches a "(at Assets/Foo.cs:123)" frame reference in a Unity stacktrace.
_FILE_LINE_RE = re.compile(r"\(at\s+(.+?:\d+)\)")


def _strip_stacktrace_from_list(items: list) -> None:
    """Remove stacktrace fields from a list of log entries."""
    for item in items:
        if isinstance(item, dict) and "stacktrace" in item:
            item.pop("stacktrace", None)


def _normalize_signature(message: str) -> str:
    """Reduce a message's first line to a stable grouping signature.

    Strips GUIDs, hex/pointer literals, long hex runs, and digit runs so that
    otherwise-identical errors that differ only in an id or count collapse into
    one group. Case and whitespace are normalized too.
    """
    first_line = (message or "").splitlines()[0] if message else ""
    sig = _GUID_RE.sub("<guid>", first_line)
    sig = _HEX_RE.sub("<hex>", sig)
    sig = _LONGHEX_RE.sub("<hex>", sig)
    sig = _NUM_RE.sub("<n>", sig)
    sig = _WS_RE.sub(" ", sig).strip().lower()
    return sig


def _first_file_line(stacktrace: str | None) -> str | None:
    """Extract the topmost user frame 'File.cs:line' from a stacktrace, if any."""
    if not stacktrace:
        return None
    match = _FILE_LINE_RE.search(stacktrace)
    if match:
        return match.group(1).strip()
    return None


def _extract_entries(data: Any) -> list[dict]:
    """Return the list of entry dicts from a read_console 'get' response payload.

    The C# handler returns either a bare list (non-paging) or a dict with an
    'items' list (paging). Only dict entries are usable for grouping.
    """
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        for key in ("items", "lines"):
            val = data.get(key)
            if isinstance(val, list):
                return [e for e in val if isinstance(e, dict)]
    return []


def _summarize_entries(entries: list[dict]) -> dict[str, Any]:
    """Group console entries by (type, normalized first line) and rank by count."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []

    for entry in entries:
        entry_type = str(entry.get("type", "") or "")
        message = entry.get("message", "") or ""
        signature = _normalize_signature(message)
        key = (entry_type, signature)
        group = groups.get(key)
        if group is None:
            group = {
                "count": 0,
                "type": entry_type,
                "sample_message": message.splitlines()[0] if message else "",
                "first_file_line": _first_file_line(entry.get("stackTrace")),
            }
            groups[key] = group
            order.append(key)
        group["count"] += 1
        if group["first_file_line"] is None:
            group["first_file_line"] = _first_file_line(entry.get("stackTrace"))

    ranked = sorted(
        (groups[k] for k in order),
        key=lambda g: g["count"],
        reverse=True,
    )
    total_entries = len(entries)
    capped = ranked[:SUMMARY_GROUP_CAP]
    suppressed = len(ranked) - len(capped)
    return {
        "groups": capped,
        "total_entries": total_entries,
        "group_count": len(ranked),
        "suppressed": max(0, suppressed),
    }


@mcp_for_unity_tool(
    description="Gets messages from or clears the Unity Editor console. Defaults to 10 most recent entries. Use page_size/cursor for paging. Note: For maximum client compatibility, pass count as a quoted string (e.g., '5'). The 'get' action is read-only; 'clear' modifies ephemeral UI state (not project data).",
    annotations=ToolAnnotations(
        title="Read Console",
    ),
)
async def read_console(
    ctx: Context,
    action: Annotated[Literal['get', 'clear'],
                      "Get or clear the Unity Editor console. Defaults to 'get' if omitted."] | None = None,
    types: Annotated[list[Literal['error', 'warning',
                                  'log', 'all']] | str,
                     "Message types to get (accepts list or JSON string)"] | None = None,
    count: Annotated[int | str,
                     "Max messages to return in non-paging mode (accepts int or string, e.g., 5 or '5'). Ignored when paging with page_size/cursor."] | None = None,
    filter_text: Annotated[str, "Text filter for messages"] | None = None,
    page_size: Annotated[int | str,
                         "Page size for paginated console reads. Defaults to 50 when omitted."] | None = None,
    cursor: Annotated[int | str,
                      "Opaque cursor for paging (0-based offset). Defaults to 0."] | None = None,
    format: Annotated[Literal['plain', 'detailed',
                              'json', 'summary'],
                      "Output format. 'summary' groups entries by error signature "
                      "(type + normalized first line) and returns the most frequent "
                      "groups with counts — compact triage view."] | None = None,
    include_stacktrace: Annotated[bool | str,
                                  "Include stack traces in output (accepts true/false or 'true'/'false')"] | None = None,
) -> dict[str, Any]:
    # Get active instance from session state
    # Removed session_state import
    unity_instance = await get_unity_instance_from_context(ctx)
    # Set defaults if values are None
    action = action if action is not None else 'get'
    
    # Parse types if it's a JSON string (handles client compatibility issue #561)
    if isinstance(types, str):
        types = parse_json_payload(types)
    # Validate types is a list after parsing
    if types is not None and not isinstance(types, list):
        return {
            "success": False,
            "message": (
                f"types must be a list, got {type(types).__name__}. "
                "If passing as JSON string, use format: '[\"error\", \"warning\"]'"
            )
        }
    if types is not None:
        allowed_types = {"error", "warning", "log", "all"}
        normalized_types = []
        for entry in types:
            if not isinstance(entry, str):
                return {
                    "success": False,
                    "message": f"types entries must be strings, got {type(entry).__name__}"
                }
            normalized = entry.strip().lower()
            if normalized not in allowed_types:
                return {
                    "success": False,
                    "message": (
                        f"invalid types entry '{entry}'. "
                        f"Allowed values: {sorted(allowed_types)}"
                    )
                }
            normalized_types.append(normalized)
        types = normalized_types
    else:
        types = ['error', 'warning', 'log']
    
    format = format if format is not None else 'plain'
    # Coerce booleans defensively (strings like 'true'/'false')

    include_stacktrace = coerce_bool(include_stacktrace, default=False)
    coerced_page_size = coerce_int(page_size, default=None)
    coerced_cursor = coerce_int(cursor, default=None)

    # Normalize action if it's a string
    if isinstance(action, str):
        action = action.lower()

    # Coerce count defensively (string/float -> int).
    # Important: leaving count unset previously meant "return all console entries", which can be extremely slow
    # (and can exceed the plugin command timeout when Unity has a large console).
    # To keep the tool responsive by default, we cap the default to a reasonable number of most-recent entries.
    # If a client truly wants everything, it can pass count="all" (or count="*") explicitly.
    if isinstance(count, str) and count.strip().lower() in ("all", "*"):
        count = None
    else:
        count = coerce_int(count)

    if action == "get" and count is None:
        count = 10

    normalized_format = format.lower() if isinstance(format, str) else format
    is_summary = normalized_format == "summary"

    # 'summary' is a Python-side transformation: fetch entries as structured
    # 'detailed' records with stack traces (so we can group and extract file:line),
    # then collapse them into ranked signature groups. The C# side never sees a
    # 'summary' format — other formats are passed through untouched.
    wire_format = "detailed" if is_summary else normalized_format
    wire_include_stacktrace = True if is_summary else include_stacktrace

    # Prepare parameters for the C# handler
    params_dict = {
        "action": action,
        "types": types,
        "count": count,
        "filterText": filter_text,
        "pageSize": coerced_page_size,
        "cursor": coerced_cursor,
        "format": wire_format,
        "includeStacktrace": wire_include_stacktrace
    }

    # Remove None values unless it's 'count' (as None might mean 'all')
    params_dict = {k: v for k, v in params_dict.items()
                   if v is not None or k == 'count'}

    # Add count back if it was None, explicitly sending null might be important for C# logic
    if 'count' not in params_dict:
        params_dict['count'] = None

    # Use centralized retry helper with instance routing
    resp = await send_with_unity_instance(async_send_command_with_retry, unity_instance, "read_console", params_dict)

    if is_summary:
        if isinstance(resp, dict) and resp.get("success"):
            entries = _extract_entries(resp.get("data"))
            return {
                "success": True,
                "message": resp.get("message", "Console summary."),
                "data": _summarize_entries(entries),
            }
        return resp if isinstance(resp, dict) else {"success": False, "message": str(resp)}

    if isinstance(resp, dict) and resp.get("success") and not include_stacktrace:
        # Strip stacktrace fields from returned lines if present
        try:
            data = resp.get("data")
            if isinstance(data, dict):
                for key in ("lines", "items"):
                    if key in data and isinstance(data[key], list):
                        _strip_stacktrace_from_list(data[key])
                        break
            elif isinstance(data, list):
                _strip_stacktrace_from_list(data)
        except Exception:
            pass
    return resp if isinstance(resp, dict) else {"success": False, "message": str(resp)}
