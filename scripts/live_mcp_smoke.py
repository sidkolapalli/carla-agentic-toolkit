"""Opt-in live CARLA smoke test through the public MCP stdio tool."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import BlobResourceContents, ImageContent, ResourceLink

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mcp.types import CallToolResult


def main(argv: Sequence[str] | None = None) -> int:
    """Run the live smoke test and emit one JSON report."""
    args = _parse_args(argv)
    try:
        report = asyncio.run(run_live_smoke(args))
    except (OSError, RuntimeError, TypeError) as error:
        report = {"ok": False, "error": str(error)}
    sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
    return 0 if report.get("ok") is True else 1


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse explicit live-test configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument(
        "--windows", action="store_true", help="launch through carla-agentic-toolkit-windows"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=2000, type=int)
    parser.add_argument("--timeout-seconds", default=20.0, type=float)
    args = parser.parse_args(argv)
    if not args.confirm_live:
        parser.error("--confirm-live is required because this test mutates the simulator")
    return args


async def run_live_smoke(
    args: argparse.Namespace,
    *,
    save_image: Path | None = None,
    drive_seconds: float = 1.0,
) -> dict[str, object]:
    """Exercise live CARLA through one MCP stdio server."""
    tag = f"carla-agentic-toolkit-smoke-{uuid4().hex}"
    server = StdioServerParameters(
        command=str(_launcher(windows=args.windows)),
        env=dict(os.environ),
    )
    async with (
        stdio_client(server) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        initialized = await session.initialize()
        tools = await session.list_tools()
        _require(
            "tool mismatch",
            condition=[tool.name for tool in tools.tools] == ["execute_carla_script"],
        )
        mutation = await _call(session, _mutation_script(tag, drive_seconds=drive_seconds), args)
        _require(
            "capture did not return native MCP image content",
            condition=mutation.pop("_image_content") is True,
        )
        image_data = mutation.pop("_image_data")
        resource_uri = mutation.pop("_image_resource_uri")
        resource = await session.read_resource(str(resource_uri))
        _require(
            "capture resource could not be read through MCP",
            condition=any(
                isinstance(item, BlobResourceContents) and bool(item.blob)
                for item in resource.contents
            ),
        )
        rejected = await _call(
            session,
            "import os\nresult = 1",
            args,
            timeout_seconds=2,
            expect_error=True,
        )
        timed_out = await _call(
            session,
            "result = api.wait(10)",
            args,
            timeout_seconds=0.1,
            expect_error=True,
        )
    mutation_result = _mapping(mutation["result"], "mutation result")
    landlock = _mapping(
        _mapping(mutation.get("sandbox"), "sandbox metadata").get("landlock"),
        "Landlock metadata",
    )
    if save_image is not None:
        _save_image(image_data, save_image)
    movement = _mapping(mutation_result.get("movement"), "movement result")
    _mapping(mutation_result.get("capture"), "capture result")
    before_speed = movement.get("before_mps")
    after_speed = movement.get("after_mps")
    _require(
        "vehicle did not accelerate",
        condition=(
            isinstance(before_speed, int | float)
            and isinstance(after_speed, int | float)
            and after_speed > before_speed
        ),
    )
    _require("weather change failed", condition=mutation_result.get("weather_changed") is True)
    _require("weather restore failed", condition=mutation_result.get("weather_restored") is True)
    _require(
        "spectator restore failed", condition=mutation_result.get("spectator_restored") is True
    )
    _require("live smoke left actors behind", condition=mutation_result.get("leftovers") == [])
    _require(
        "validator probe was not rejected",
        condition=rejected.get("error_type") == "script_rejected",
    )
    _require(
        "timeout probe was misclassified",
        condition=timed_out.get("error_type") == "script_timeout",
    )
    return {
        "ok": True,
        "server": initialized.server_info.name,
        "tag": tag,
        "health": mutation_result.get("health"),
        "movement": mutation_result.get("movement"),
        "capture": mutation_result.get("capture"),
        "image_content": True,
        "resource_read": True,
        "saved_image": str(save_image) if save_image is not None else None,
        "landlock_enforced": landlock.get("ruleset_enforced") is True,
        "weather_restored": mutation_result.get("weather_restored"),
        "spectator_restored": mutation_result.get("spectator_restored"),
        "leftovers": mutation_result["leftovers"],
        "validator_rejected": True,
        "timeout_cleaned": _mapping(timed_out.get("sandbox"), "timeout sandbox").get("timed_out"),
    }


async def _call(
    session: ClientSession,
    code: str,
    args: argparse.Namespace,
    *,
    timeout_seconds: float | None = None,
    expect_error: bool = False,
) -> dict[str, object]:
    """Call the public script tool and validate its fatal-error channel."""
    response: CallToolResult = await session.call_tool(
        "execute_carla_script",
        {
            "code": code,
            "host": args.host,
            "port": args.port,
            "timeout_seconds": timeout_seconds or args.timeout_seconds,
        },
    )
    payload = _mapping(response.structured_content, "tool result")
    _require(f"unexpected MCP isError: {payload}", condition=response.is_error is expect_error)
    _require(f"unexpected tool outcome: {payload}", condition=payload.get("ok") is not expect_error)
    images = [item for item in response.content if isinstance(item, ImageContent)]
    resources = [item for item in response.content if isinstance(item, ResourceLink)]
    payload["_image_content"] = bool(images) and bool(resources)
    payload["_image_data"] = images[0].data if images else None
    payload["_image_resource_uri"] = str(resources[0].uri) if resources else None
    return payload


def _save_image(data: object, path: Path) -> None:
    """Persist one MCP base64 image."""
    if not isinstance(data, str):
        message = "MCP image content was not base64 text."
        raise TypeError(message)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(data, validate=True))


def _launcher(*, windows: bool) -> Path:
    """Return the installed direct or Windows MCP console command."""
    suffix = ".exe" if os.name == "nt" else ""
    name = "carla-agentic-toolkit-windows" if windows else "carla-agentic-toolkit"
    return Path(sys.executable).with_name(name + suffix)


def _mutation_script(tag: str, *, drive_seconds: float = 1.0) -> str:
    """Return one self-cleaning live mutation script for a unique actor tag."""
    quoted_tag = json.dumps(tag)
    return f"""
tag = {quoted_tag}
health = api.health_check()
capabilities = api.list_capabilities()
weather_before = api.get_weather()["weather"]
spectator_restored = True
actor_ids = []
sensor_ids = []
capture = None
before_speed = 0.0
after_speed = 0.0
weather_changed = False
try:
    points = api.get_spawn_points()["spawn_points"]
    actor_id = None
    for point in points[:10]:
        spawned = api.spawn_actor_batch([{{
            "blueprint_id": "vehicle.tesla.model3",
            "transform": point,
            "attributes": {{"role_name": tag, "color": "255,0,0"}},
        }}])["results"][0]
        if spawned["actor_id"] is not None:
            actor_id = spawned["actor_id"]
            actor_ids.append(actor_id)
            break
    assert actor_id is not None, "no free spawn point"
    before_speed = api.get_vehicle_telemetry(actor_id)["speed_mps"]
    api.set_weather({{
        "cloudiness": 50.0,
        "precipitation": 15.0,
        "wetness": 35.0,
        "sun_altitude_angle": 45.0,
    }})
    weather_changed = api.get_weather()["weather"]["cloudiness"] == 50.0
    api.set_vehicle_lights(actor_id, "Position|LowBeam")
    api.set_autopilot({{
        "actor_ids": [actor_id],
        "enabled": True,
        "traffic_manager_port": 8000,
    }})
    api.tune_traffic_vehicle(actor_id, {{
        "desired_speed": 8.0,
        "auto_lane_change": False,
    }})
    watch = api.watch_actor(actor_id, seconds={drive_seconds})
    spectator_restored = watch["spectator_restored"]
    after_speed = api.get_vehicle_telemetry(actor_id)["speed_mps"]
    sensor = api.attach_sensor(
        "rgb",
        actor_id,
        {{
            "location": {{"x": -6.0, "y": 0.0, "z": 3.0}},
            "rotation": {{"pitch": -10.0, "yaw": 0.0, "roll": 0.0}},
        }},
        {{"image_size_x": "640", "image_size_y": "360"}},
    )
    sensor_ids.append(sensor["sensor_id"])
    capture = api.capture_sensor_frame(
        sensor["sensor_id"], "live-mcp/" + tag + ".png", publish=True
    )
finally:
    for sensor_id in sensor_ids:
        api.detach_sensor(sensor_id)
    if actor_ids:
        api.destroy_actors(actor_ids)
    api.set_weather(weather_before)
remaining = api.list_actors("*")["actors"]
leftovers = [actor["actor_id"] for actor in remaining if actor["role_name"] == tag]
result = {{
    "health": health,
    "capabilities": capabilities,
    "movement": {{"before_mps": before_speed, "after_mps": after_speed}},
    "capture": capture,
    "weather_changed": weather_changed,
    "weather_restored": api.get_weather()["weather"]["cloudiness"] == weather_before["cloudiness"],
    "spectator_restored": spectator_restored,
    "leftovers": leftovers,
}}
"""


def _mapping(value: object, label: str) -> dict[str, object]:
    """Require a string-keyed object."""
    if not isinstance(value, dict):
        message = f"{label} was not an object: {value!r}"
        raise TypeError(message)
    return {str(key): item for key, item in value.items()}


def _require(message: str, *, condition: bool) -> None:
    """Raise an actionable smoke-test failure."""
    if not condition:
        raise RuntimeError(message)


if __name__ == "__main__":
    raise SystemExit(main())
