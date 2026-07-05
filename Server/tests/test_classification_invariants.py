"""Self-policing CI checks for the safety classification table.

These make the classification table defend itself as the tool set grows — the
adversarial review found a dozen misclassifications by hand; these turn the two
most common failure modes into CI failures:

  A. Completeness: every registered MCP tool must be EXPLICITLY classified, so a
     new tool can never silently inherit the unknown-tool WRITE default (which
     would under-gate a genuinely destructive tool in write mode). Tool modules
     are imported directly so a NEW tool that fails to import is a red test, not
     a silent pass.
  B. C#/Python action drift: for tools that expose an ALL_ACTIONS list, every
     action the C# handler accepts must be one the Python tool lists — otherwise
     a C#-only action reaches Unity classified as the tool's default rather than
     its true class. The C# handler is located recursively (handlers live in
     subfolders), every action-dispatch switch is scanned, and prefix-routed
     sub-switch labels are matched as a suffix of the Python action names.
"""
import importlib
import re
from pathlib import Path

import pytest

from core.safety import TOOL_POLICIES

TOOLS_DIR = Path(__file__).resolve().parents[1] / "src" / "services" / "tools"
CS_TOOLS_DIR = Path(__file__).resolve().parents[2] / "MCPForUnity" / "Editor" / "Tools"

# Tools classified by a payload-aware branch in classify_call rather than a flat
# TOOL_POLICIES entry.
SPECIAL_CASE_CLASSIFIERS = {"batch_execute", "manage_build", "manage_scene", "manage_prefabs", "manage_checkpoint", "manage_camera"}

# Infra case labels that need not appear in a tool's Python ALL_ACTIONS.
_INFRA_ACTIONS = {"ping"}

NOT_TOOL_MODULES = {"__init__", "utils", "preflight"}


def _import_all_tool_modules() -> None:
    """Import every tool module directly so an import-broken module fails loudly
    (discover_modules swallows import errors, which would hide a broken new tool)."""
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.stem in NOT_TOOL_MODULES:
            continue
        importlib.import_module(f"services.tools.{path.stem}")


def _registered_tool_names() -> set[str]:
    _import_all_tool_modules()
    from services.registry import get_registered_tools
    return {t["name"] for t in get_registered_tools() if t.get("name")}


def test_every_registered_tool_is_explicitly_classified():
    names = _registered_tool_names()
    assert names, "No tools discovered — registry population failed."
    unclassified = sorted(
        n for n in names
        if n not in TOOL_POLICIES and n not in SPECIAL_CASE_CLASSIFIERS
    )
    assert not unclassified, (
        "These registered tools have NO explicit safety classification and would "
        "fall back to the unknown-tool WRITE default (a destructive tool would be "
        f"under-gated in write mode). Add each to core.safety.TOOL_POLICIES: {unclassified}"
    )


def _pascal_case(snake: str) -> str:
    return "".join(part.capitalize() for part in snake.split("_"))


def _tools_with_all_actions() -> list[tuple[str, list[str]]]:
    out = []
    for path in sorted(TOOLS_DIR.glob("*.py")):
        if path.stem in NOT_TOOL_MODULES:
            continue
        try:
            mod = importlib.import_module(f"services.tools.{path.stem}")
        except Exception:
            continue
        actions = getattr(mod, "ALL_ACTIONS", None)
        if isinstance(actions, (list, tuple)) and actions:
            out.append((path.stem, [str(a).lower() for a in actions]))
    return out


def _find_cs_handler(command: str) -> Path | None:
    """Locate the C# handler recursively; match case-insensitively so ManageVFX.cs
    matches the expected ManageVfx."""
    want = _pascal_case(command).lower()
    matches = [p for p in CS_TOOLS_DIR.rglob("*.cs") if p.stem.lower() == want]
    return matches[0] if matches else None


def _action_switch_blocks(text: str) -> list[str]:
    """Return the body of every switch whose selector mentions 'action'.

    Balances parentheses so selectors with method calls
    (e.g. switch(action.ToLowerInvariant())) are captured, and braces so the
    full block is returned. Collects ALL such switches (prefix-routed handlers
    have several).
    """
    blocks: list[str] = []
    for m in re.finditer(r"switch\s*\(", text):
        # Balance parens to find the end of the selector.
        i = m.end()
        depth = 1
        while i < len(text) and depth > 0:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
            i += 1
        selector = text[m.end():i - 1]
        if "action" not in selector.lower():
            continue
        # Next non-space char must open the block.
        j = i
        while j < len(text) and text[j] in " \t\r\n":
            j += 1
        if j >= len(text) or text[j] != "{":
            continue
        depth = 0
        for k in range(j, len(text)):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[j:k + 1])
                    break
    return blocks


def _csharp_action_cases(cs_path: Path) -> set[str] | None:
    text = cs_path.read_text(encoding="utf-8", errors="ignore")
    blocks = _action_switch_blocks(text)
    if not blocks:
        return None
    cases: set[str] = set()
    for block in blocks:
        cases |= {lab.lower() for lab in re.findall(r'case\s+"([a-z0-9_]+)"\s*:', block)}
    return cases


def _covered(cs_action: str, python_actions: set[str]) -> bool:
    if cs_action in python_actions or cs_action in _INFRA_ACTIONS:
        return True
    # Prefix-routed handlers strip the prefix before their sub-switch
    # (e.g. "animator_get_info" -> sub-switch case "get_info"). Accept a C#
    # label that is the suffix of a full prefixed Python action.
    return any(pa.endswith("_" + cs_action) for pa in python_actions)


# Tools whose C# handler has a parseable action switch — asserted below so a
# future refactor that reintroduces a silent skip is itself caught.
_EXPECTED_CROSSCHECKED = {"manage_build", "manage_packages", "manage_project"}


@pytest.mark.parametrize("stem,actions", _tools_with_all_actions(), ids=lambda v: v if isinstance(v, str) else "")
def test_csharp_actions_are_covered_by_python(stem, actions):
    cs_path = _find_cs_handler(stem)
    if cs_path is None:
        pytest.skip(f"No C# handler found for {stem}")

    cs_actions = _csharp_action_cases(cs_path)
    if cs_actions is None:
        pytest.skip(f"No action-dispatch switch located in {cs_path.name}")
    if not cs_actions:
        pytest.skip(f"No case labels parsed from {cs_path.name}")

    python_actions = set(actions)
    drift = sorted(a for a in cs_actions if not _covered(a, python_actions))
    assert not drift, (
        f"{cs_path.name} accepts action(s) {drift} that services/tools/{stem}.py "
        "does not list in ALL_ACTIONS. A C#-only action reaches Unity classified as "
        "the tool default, not its true class — add it to ALL_ACTIONS (and classify "
        "it if it is not the tool default class)."
    )


def test_known_crosschecked_tools_are_actually_checked():
    """Guard against a regression that makes the drift check silently skip the
    tools it is supposed to cover."""
    for stem in _EXPECTED_CROSSCHECKED:
        cs_path = _find_cs_handler(stem)
        assert cs_path is not None, f"C# handler for {stem} not found (drift check would skip it)"
        cases = _csharp_action_cases(cs_path)
        assert cases, f"No action cases parsed from {cs_path.name} (drift check inert for {stem})"
