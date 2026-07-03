"""Invalid-action rejection for the free-string `action` tools (Phase-2 task 5).

These tools keep an `action: str` external schema (a hard Literal risks
rejecting valid actions if the Python list drifts from the C# side), but they
validate the action against their ALL_ACTIONS list at runtime and return a
structured error before dispatching to Unity. These tests prove an unknown
action is rejected without any Unity round-trip.
"""
import pytest

from services.tools.manage_build import manage_build
from services.tools.manage_packages import manage_packages
from services.tools.manage_graphics import manage_graphics
from services.tools.manage_profiler import manage_profiler
from services.tools.manage_camera import manage_camera
from services.tools.manage_vfx import manage_vfx
from services.tools.manage_probuilder import manage_probuilder


class FakeCtx:
    """Minimal Context: invalid-action validation returns before any dispatch,
    so get_state is never expected to matter, but is provided defensively."""
    client_id = "test"

    async def get_state(self, key):  # pragma: no cover - not reached on invalid action
        return None


TOOLS = [
    ("manage_build", manage_build),
    ("manage_packages", manage_packages),
    ("manage_graphics", manage_graphics),
    ("manage_profiler", manage_profiler),
    ("manage_camera", manage_camera),
    ("manage_vfx", manage_vfx),
    ("manage_probuilder", manage_probuilder),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,func", TOOLS, ids=[t[0] for t in TOOLS])
async def test_unknown_action_is_rejected(name, func):
    result = await func(FakeCtx(), action="definitely_not_a_real_action")
    assert isinstance(result, dict)
    assert result.get("success") is False
    text = (result.get("message") or result.get("error") or "").lower()
    assert "unknown action" in text or "action" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("name,func", TOOLS, ids=[t[0] for t in TOOLS])
async def test_empty_action_is_rejected(name, func):
    result = await func(FakeCtx(), action="")
    assert isinstance(result, dict)
    assert result.get("success") is False
