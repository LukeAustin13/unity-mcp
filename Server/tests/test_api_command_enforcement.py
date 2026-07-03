"""End-to-end proof that the /api/command HTTP route (the CLI ingress) cannot
bypass the safety policy.

The route does not pass through the FastMCP middleware chain, so it calls the
shared enforcement layer directly. This drives the *real* FastMCP HTTP app
in-process — but building it imports ``main`` (which reconfigures logging at
import) and needs the real ``fastmcp`` (the integration conftest stubs it for
the rest of the suite). Both concerns are solved by running the whole scenario
in a fresh subprocess: isolated logging, real fastmcp, no Unity required.

A blocked call is refused with HTTP 403 by enforcement *before* the route ever
looks for a Unity session; an allowed call passes enforcement and only then
fails with 503 (no Unity connected).
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]

_SCENARIO = textwrap.dedent(
    """
    import asyncio, json, sys
    sys.path.insert(0, "src")
    import httpx
    from core.config import config
    from main import create_mcp_server

    async def main():
        app = create_mcp_server(project_scoped_tools=False).http_app()

        async def post(payload):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                r = await c.post("/api/command", json=payload)
            return r.status_code, r.json()

        out = {}

        config.safety_mode = "read_only"
        code, body = await post({"type": "manage_asset",
                                 "params": {"action": "delete", "path": "Assets/x.mat"}})
        out["read_only_delete"] = [code, body.get("error", "")]

        config.safety_mode = "review_only"
        code, body = await post({"type": "create_script",
                                 "params": {"name": "Foo", "path": "Assets/"}})
        out["review_write"] = [code, body.get("error", "")]

        config.safety_mode = "write"
        code, body = await post({"type": "manage_asset",
                                 "params": {"action": "delete", "path": "Assets/x.mat"}})
        out["write_delete_noconfirm"] = [code, body.get("error", "")]

        config.allow_execute_code = False
        code, body = await post({"type": "execute_code",
                                 "params": {"action": "execute", "code": "return 1;", "confirm": True}})
        out["execute_code_blocked"] = [code, body.get("error", "")]

        config.allow_external_build_output = False
        code, body = await post({"type": "manage_build",
                                 "params": {"action": "build", "target": "windows64",
                                            "output_path": "/tmp/evil", "confirm": True}})
        out["build_external"] = [code, body.get("error", "")]

        config.safety_mode = "read_only"
        code, body = await post({"type": "read_console", "params": {"action": "get"}})
        out["read_allowed"] = [code, body.get("error", "")]

        # Adversarial-review regressions, end-to-end through the route:
        config.safety_mode = "write"
        # #1: nested batch smuggling a destructive delete (no confirm) must 403.
        code, body = await post({"type": "batch_execute", "params": {"commands": [
            {"tool": "batch_execute", "params": {"commands": [
                {"tool": "manage_gameobject", "params": {"action": "delete", "target": "X"}}]}}]}})
        out["nested_batch_delete"] = [code, body.get("error", "")]

        # #2: manage_build settings-write must be blocked in read_only.
        config.safety_mode = "read_only"
        code, body = await post({"type": "manage_build", "params": {
            "action": "settings", "property": "scripting_backend", "value": "il2cpp"}})
        out["build_settings_readonly"] = [code, body.get("error", "")]

        # #6: menu key-precedence inversion must 403.
        config.safety_mode = "write"
        code, body = await post({"type": "execute_menu_item", "params": {
            "menuPath": "Window/General/Console", "menu_path": "Assets/Delete", "confirm": True}})
        out["menu_inversion"] = [code, body.get("error", "")]

        # #7: camelCase build output path must 403.
        config.allow_external_build_output = False
        code, body = await post({"type": "manage_build", "params": {
            "action": "build", "target": "windows64", "outputPath": "/tmp/evil", "confirm": True}})
        out["build_camel_path"] = [code, body.get("error", "")]

        # re-verify finding: manage_scene validate+autoRepair (camelCase) mutates,
        # must be blocked in read_only.
        config.safety_mode = "read_only"
        code, body = await post({"type": "manage_scene", "params": {
            "action": "validate", "autoRepair": True}})
        out["scene_autorepair_readonly"] = [code, body.get("error", "")]

        print("RESULT " + json.dumps(out))

    asyncio.run(main())
    """
)


@pytest.fixture(scope="module")
def scenario_results():
    proc = subprocess.run(
        [sys.executable, "-c", _SCENARIO],
        cwd=str(SERVER_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        env={**__import__("os").environ, "UNITY_MCP_DISABLE_TELEMETRY": "1",
             "UNITY_MCP_SKIP_STARTUP_CONNECT": "1"},
    )
    if proc.returncode != 0:
        pytest.fail(f"scenario subprocess failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, f"no RESULT line in output:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(line[len("RESULT "):])


def test_read_only_blocks_destructive_via_api(scenario_results):
    code, err = scenario_results["read_only_delete"]
    assert code == 403
    assert "read_only" in err


def test_review_only_blocks_write_via_api(scenario_results):
    code, err = scenario_results["review_write"]
    assert code == 403
    assert "review_only" in err


def test_write_destructive_requires_confirm_via_api(scenario_results):
    code, err = scenario_results["write_delete_noconfirm"]
    assert code == 403
    assert "confirm" in err.lower()


def test_execute_code_blocked_via_api_even_confirmed(scenario_results):
    code, err = scenario_results["execute_code_blocked"]
    assert code == 403
    assert "sandbox" in err.lower()


def test_build_external_path_blocked_via_api(scenario_results):
    code, _ = scenario_results["build_external"]
    assert code == 403


def test_allowed_read_passes_enforcement_then_needs_unity(scenario_results):
    # Allowed by policy; with no Unity the route proceeds past enforcement and
    # returns 503 (not 403), proving the gate allowed rather than blocked it.
    code, _ = scenario_results["read_allowed"]
    assert code != 403
    assert code in (200, 503)


def test_nested_batch_delete_blocked_via_api(scenario_results):
    code, err = scenario_results["nested_batch_delete"]
    assert code == 403
    assert "confirm" in err.lower()


def test_build_settings_write_blocked_in_read_only_via_api(scenario_results):
    code, err = scenario_results["build_settings_readonly"]
    assert code == 403
    assert "read_only" in err


def test_menu_key_inversion_blocked_via_api(scenario_results):
    code, _ = scenario_results["menu_inversion"]
    assert code == 403


def test_camel_build_path_blocked_via_api(scenario_results):
    code, _ = scenario_results["build_camel_path"]
    assert code == 403


def test_scene_autorepair_blocked_in_read_only_via_api(scenario_results):
    code, err = scenario_results["scene_autorepair_readonly"]
    assert code == 403
    assert "read_only" in err
