"""Behavior specs for the opt-in live MCP smoke harness."""

from __future__ import annotations

import ast
import asyncio
import json
import runpy
import zlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolResult

from carla_agentic_toolkit.output_content import MAX_IMAGE_BYTES
from scripts import live_mcp_smoke
from scripts.live_mcp_smoke import _call, _mutation_script, _parse_args

if TYPE_CHECKING:
    import argparse
    from pathlib import Path

    from mcp import ClientSession


def test_live_smoke_requires_explicit_confirmation() -> None:
    """A live simulator mutation must never run by default."""
    with pytest.raises(SystemExit):
        _parse_args([])


def test_live_smoke_script_tags_and_cleans_only_its_actors() -> None:
    """The generated script should own cleanup through its unique role tag."""
    code = _mutation_script("carla-agentic-toolkit-smoke-test", drive_seconds=8.0)

    assert all(
        marker in code
        for marker in (
            "carla-agentic-toolkit-smoke-test",
            "finally:",
            "api.destroy_actors(actor_ids)",
            "api.set_weather(weather_before)",
            "api.watch_actor(actor_id, seconds=8.0)",
            "throttle=0.65, brake=0.0, hand_brake=False",
            "throttle=0.0, brake=1.0",
            "publish=True",
        )
    )


def test_live_smoke_camera_fits_publication_limit_without_compression() -> None:
    """Even an uncompressed RGBA frame must fit the public image byte limit."""
    width, height = _smoke_camera_dimensions()
    # Include each PNG scanline's filter byte, stored DEFLATE blocks, and PNG chunks.
    scanlines = bytes((width * 4 + 1) * height)
    png_bytes = len(zlib.compress(scanlines, level=0)) + 57

    assert width > 0
    assert height > 0
    assert png_bytes <= MAX_IMAGE_BYTES


def _smoke_camera_dimensions() -> tuple[int, int]:
    """Read the camera configuration used by the actual generated script."""
    tree = ast.parse(_mutation_script("size-check"))
    camera_call = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "attach_sensor"
    )
    attributes = ast.literal_eval(camera_call.args[3])
    return int(attributes["image_size_x"]), int(attributes["image_size_y"])


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("capture failed"), "RuntimeError: capture failed"),
        (
            ExceptionGroup(
                "stdio task group",
                [ExceptionGroup("session task group", [RuntimeError("image_too_large")])],
            ),
            "RuntimeError: image_too_large",
        ),
        (
            ExceptionGroup(
                "stdio task group", [RuntimeError("capture failed"), OSError("cleanup failed")]
            ),
            "RuntimeError: capture failed; OSError: cleanup failed",
        ),
    ],
)
def test_live_smoke_main_reports_failure_as_one_json_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected: str,
) -> None:
    """Nested transport task groups must not hide causes or emit tracebacks."""

    async def fail(_args: argparse.Namespace) -> dict[str, object]:
        raise error

    monkeypatch.setattr(live_mcp_smoke, "run_live_smoke", fail)

    assert live_mcp_smoke.main(["--confirm-live"]) == 1
    output = capsys.readouterr()
    assert len(output.out.splitlines()) == 1
    assert json.loads(output.out) == {"ok": False, "error": expected}
    assert output.err == ""


@pytest.mark.parametrize("is_error", [True, False])
def test_live_smoke_tool_failure_keeps_cause_and_cleanup_without_snapshots(
    *, is_error: bool
) -> None:
    """Both error channels retain useful diagnostics without dumping scene data."""
    response = CallToolResult(
        content=[],
        is_error=is_error,
        structured_content={
            "ok": False,
            "error_type": "image_too_large",
            "error": "One image exceeds 524288 bytes.",
            "cleanup": {"destroyed_actor_ids": [7], "failures": []},
            "snapshots": {"large-scene": "x" * 50_000},
            "result": {"large-result": "y" * 50_000},
        },
    )
    session = cast("ClientSession", SimpleNamespace(call_tool=AsyncMock(return_value=response)))

    with pytest.raises(RuntimeError, match="image_too_large") as failure:
        asyncio.run(_call(session, "result = 1", _parse_args(["--confirm-live"])))

    message = str(failure.value)
    _prefix, _separator, details = message.partition(": ")
    assert json.loads(details) == {
        "error_type": "image_too_large",
        "error": "One image exceeds 524288 bytes.",
        "cleanup": {"destroyed_actor_ids": [7], "failures": []},
    }


@pytest.mark.parametrize("ok_value", [None, "true", 1])
def test_live_smoke_rejects_nonboolean_success(ok_value: object) -> None:
    """A transport success must still contain an explicit successful tool result."""
    response = CallToolResult(content=[], structured_content={"ok": ok_value})
    session = cast("ClientSession", SimpleNamespace(call_tool=AsyncMock(return_value=response)))

    with pytest.raises(RuntimeError, match="unexpected tool outcome"):
        asyncio.run(_call(session, "result = 1", _parse_args(["--confirm-live"])))


def test_live_smoke_control_failure_preserves_cause_and_cleans_up(tmp_path: Path) -> None:
    """Recoverable actuation errors stop the demo and still restore its state."""
    api = Mock()
    api.health_check.return_value = {"connected": True}
    api.get_weather.return_value = {"weather": {"cloudiness": 0.0}}
    api.get_spawn_points.return_value = {"spawn_points": [{}]}
    api.spawn_actor_batch.return_value = {"results": [{"actor_id": 7}]}
    api.get_vehicle_telemetry.return_value = {"speed_mps": 0.0}
    api.set_weather.return_value = {}
    api.set_vehicle_lights.return_value = {"actor_id": 7}
    api.apply_vehicle_control.return_value = {
        "ok": False,
        "error_type": "apply_vehicle_control_failed",
        "error": "simulator rejected control",
    }
    script = tmp_path / "smoke.py"
    script.write_text(_mutation_script("failure-check"), encoding="utf-8")

    with pytest.raises(AssertionError, match="simulator rejected control"):
        runpy.run_path(str(script), init_globals={"api": api})

    api.watch_actor.assert_not_called()
    api.set_autopilot.assert_not_called()
    api.destroy_actors.assert_called_once_with([7])
    assert api.set_weather.call_args.args == ({"cloudiness": 0.0},)


def test_live_smoke_cleanup_observation_waits_for_async_cache(tmp_path: Path) -> None:
    """A stale actor list after successful destruction must refresh before the verdict."""
    api = Mock()
    api.health_check.return_value = {"connected": True}
    api.get_weather.side_effect = [
        {"weather": {"cloudiness": 0.0}},
        {"weather": {"cloudiness": 50.0}},
        {"weather": {"cloudiness": 0.0}},
    ]
    api.get_spawn_points.return_value = {"spawn_points": [{}]}
    api.spawn_actor_batch.return_value = {"results": [{"actor_id": 7}]}
    api.get_vehicle_telemetry.return_value = {"speed_mps": 1.0}
    api.set_weather.return_value = {}
    api.set_vehicle_lights.return_value = {"actor_id": 7}
    api.apply_vehicle_control.return_value = {"applied_control": {}}
    api.watch_actor.return_value = {"spectator_restored": True}
    api.attach_sensor.return_value = {"sensor_id": 8}
    api.capture_sensor_frame.return_value = {}
    api.detach_sensor.return_value = {}
    api.destroy_actors.return_value = {}
    api.list_actors.side_effect = [
        {"actors": [{"actor_id": 7, "role_name": "cache-check"}]},
        {"actors": []},
    ]
    script = tmp_path / "smoke.py"
    script.write_text(_mutation_script("cache-check"), encoding="utf-8")

    namespace = runpy.run_path(str(script), init_globals={"api": api})

    assert namespace["result"]["leftovers"] == []
    api.wait.assert_called_once_with(0.1)
    api.tick.assert_not_called()
