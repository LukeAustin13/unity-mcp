"""Tests for the optional long-tail ``properties`` bag on manage_gameobject and manage_ui.

These prove the feature is purely additive and non-breaking:
  (a) an old-style call (no ``properties``) forwards identical params;
  (b) a new-style call via ``properties`` forwards the same outgoing params;
  (c) an explicit top-level value wins over the same key in the bag;
  (d) an unknown ``properties`` key returns an error and never dispatches.

Transport is mocked; no Unity is required.
"""
import pytest

import services.tools.manage_gameobject as mg
import services.tools.manage_ui as mu
from services.tools.manage_gameobject import manage_gameobject
from services.tools.manage_ui import manage_ui
from services.tools.utils import merge_properties


class FakeCtx:
    client_id = "test"

    async def get_state(self, key):
        return None


@pytest.fixture
def go_captured(monkeypatch):
    """Capture outgoing params from manage_gameobject."""
    sent = {}

    async def fake_send(send_fn, unity_instance, command, params):
        sent["command"] = command
        sent["params"] = params
        return {"success": True, "data": {"ok": True}}

    monkeypatch.setattr(mg, "send_with_unity_instance", fake_send)
    return sent


@pytest.fixture
def ui_captured(monkeypatch):
    """Capture outgoing params from manage_ui (both mutation and read paths)."""
    sent = {}

    async def fake_send_mutation(ctx, unity_instance, command, params, **kwargs):
        sent["command"] = command
        sent["params"] = params
        return {"success": True, "data": {"ok": True}}

    async def fake_send(send_fn, unity_instance, command, params, **kwargs):
        sent["command"] = command
        sent["params"] = params
        return {"success": True, "data": {"ok": True}}

    monkeypatch.setattr(mu, "send_mutation", fake_send_mutation)
    monkeypatch.setattr(mu, "send_with_unity_instance", fake_send)
    return sent


# --------------------------------------------------------------------------- #
# merge_properties unit tests
# --------------------------------------------------------------------------- #
class TestMergePropertiesHelper:
    def test_none_bag_returns_copy_of_named(self):
        named = {"a": 1, "b": None}
        merged, err = merge_properties(named, None, {"a", "b"})
        assert err is None
        assert merged == {"a": 1, "b": None}
        assert merged is not named  # shallow copy, not the same object

    def test_dict_bag_fills_missing_keys(self):
        merged, err = merge_properties(
            {"a": None, "b": None}, {"a": 5}, {"a", "b"})
        assert err is None
        assert merged == {"a": 5, "b": None}

    def test_explicit_wins_over_bag(self):
        merged, err = merge_properties(
            {"a": "explicit"}, {"a": "bag"}, {"a"})
        assert err is None
        assert merged["a"] == "explicit"

    def test_json_string_bag_parsed(self):
        merged, err = merge_properties(
            {"a": None}, '{"a": 7}', {"a"})
        assert err is None
        assert merged["a"] == 7

    def test_unknown_key_errors(self):
        merged, err = merge_properties(
            {"a": None}, {"zzz": 1}, {"a"})
        assert merged is None
        assert "Unknown properties key" in err
        assert "zzz" in err

    def test_non_dict_bag_errors(self):
        merged, err = merge_properties({"a": None}, "[1, 2, 3]", {"a"})
        assert merged is None
        assert "must be a dict" in err


# --------------------------------------------------------------------------- #
# manage_gameobject
# --------------------------------------------------------------------------- #
class TestManageGameObjectProperties:
    @pytest.mark.asyncio
    async def test_old_style_call_forwards_expected_params(self, go_captured):
        await manage_gameobject(
            FakeCtx(),
            action="move_relative",
            target="Cube",
            reference_object="Player",
            direction="forward",
            distance=2.5,
        )
        p = go_captured["params"]
        assert p["action"] == "move_relative"
        assert p["target"] == "Cube"
        assert p["reference_object"] == "Player"
        assert p["direction"] == "forward"
        assert p["distance"] == 2.5
        # world_space defaults to True when omitted (pre-existing behavior)
        assert p["world_space"] is True

    @pytest.mark.asyncio
    async def test_new_style_via_bag_forwards_same_params(self, go_captured):
        # Old style
        await manage_gameobject(
            FakeCtx(),
            action="move_relative",
            target="Cube",
            reference_object="Player",
            direction="forward",
            distance=2.5,
            world_space=False,
        )
        old_params = go_captured["params"]

        # New style: same values, but the long-tail ones come via the bag
        await manage_gameobject(
            FakeCtx(),
            action="move_relative",
            target="Cube",
            properties={
                "reference_object": "Player",
                "direction": "forward",
                "distance": 2.5,
                "world_space": False,
            },
        )
        new_params = go_captured["params"]

        assert new_params == old_params

    @pytest.mark.asyncio
    async def test_json_string_bag(self, go_captured):
        await manage_gameobject(
            FakeCtx(),
            action="look_at",
            target="Camera",
            properties='{"look_at_target": "Player"}',
        )
        assert go_captured["params"]["look_at_target"] == "Player"

    @pytest.mark.asyncio
    async def test_explicit_wins_over_bag(self, go_captured):
        await manage_gameobject(
            FakeCtx(),
            action="move_relative",
            target="Cube",
            direction="up",  # explicit
            properties={"direction": "down", "distance": 3.0},
        )
        p = go_captured["params"]
        assert p["direction"] == "up"      # explicit wins
        assert p["distance"] == 3.0        # from bag

    @pytest.mark.asyncio
    async def test_unknown_key_errors_and_no_dispatch(self, go_captured):
        result = await manage_gameobject(
            FakeCtx(),
            action="move_relative",
            target="Cube",
            properties={"not_a_real_key": 1},
        )
        assert result["success"] is False
        assert "Unknown properties key" in result["message"]
        assert "command" not in go_captured  # never dispatched

    @pytest.mark.asyncio
    async def test_no_properties_does_not_add_keys(self, go_captured):
        """A create call without any long-tail params must not gain new keys."""
        await manage_gameobject(
            FakeCtx(),
            action="create",
            name="Thing",
        )
        p = go_captured["params"]
        for k in ("new_name", "offset", "reference_object", "direction",
                  "distance", "look_at_target", "look_at_up"):
            assert k not in p
        # world_space is coerced to its default True regardless (pre-existing).
        assert p["world_space"] is True


# --------------------------------------------------------------------------- #
# manage_ui
# --------------------------------------------------------------------------- #
class TestManageUIProperties:
    @pytest.mark.asyncio
    async def test_old_style_call_forwards_expected_params(self, ui_captured):
        await manage_ui(
            FakeCtx(),
            action="modify_visual_element",
            element_name="title",
            text="Hello",
            tooltip="a tip",
        )
        p = ui_captured["params"]
        assert p["action"] == "modify_visual_element"
        assert p["elementName"] == "title"
        assert p["text"] == "Hello"
        assert p["tooltip"] == "a tip"

    @pytest.mark.asyncio
    async def test_new_style_via_bag_forwards_same_params(self, ui_captured):
        await manage_ui(
            FakeCtx(),
            action="modify_visual_element",
            element_name="title",
            text="Hello",
            tooltip="a tip",
        )
        old_params = ui_captured["params"]

        await manage_ui(
            FakeCtx(),
            action="modify_visual_element",
            properties={
                "element_name": "title",
                "text": "Hello",
                "tooltip": "a tip",
            },
        )
        new_params = ui_captured["params"]

        assert new_params == old_params

    @pytest.mark.asyncio
    async def test_json_string_bag(self, ui_captured):
        await manage_ui(
            FakeCtx(),
            action="render_ui",
            target="Panel",
            properties='{"width": 800, "height": 600}',
        )
        p = ui_captured["params"]
        assert p["width"] == 800
        assert p["height"] == 600

    @pytest.mark.asyncio
    async def test_explicit_wins_over_bag(self, ui_captured):
        await manage_ui(
            FakeCtx(),
            action="modify_visual_element",
            element_name="title",
            text="explicit",  # explicit
            properties={"text": "bag", "tooltip": "from bag"},
        )
        p = ui_captured["params"]
        assert p["text"] == "explicit"     # explicit wins
        assert p["tooltip"] == "from bag"  # from bag

    @pytest.mark.asyncio
    async def test_unknown_key_errors_and_no_dispatch(self, ui_captured):
        result = await manage_ui(
            FakeCtx(),
            action="modify_visual_element",
            properties={"not_a_real_key": 1},
        )
        assert result["success"] is False
        assert "Unknown properties key" in result["message"]
        assert "command" not in ui_captured  # never dispatched

    @pytest.mark.asyncio
    async def test_no_properties_does_not_add_keys(self, ui_captured):
        """A plain list call without long-tail params must not gain new keys."""
        await manage_ui(FakeCtx(), action="list")
        p = ui_captured["params"]
        for k in ("width", "height", "include_image", "max_resolution",
                  "file_name", "output_folder", "elementName", "text",
                  "addClasses", "removeClasses", "toggleClasses", "style",
                  "enabled", "visible", "tooltip"):
            assert k not in p
