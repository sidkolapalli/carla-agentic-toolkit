"""Replay, batch, and capability helpers for script-only CARLA experiments."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_mcp.errors import CarlaAdapterError
from carla_mcp.experiment_common import (
    capabilities,
    int_attr,
    int_value,
    optional_str_attr,
    safe_text_method,
)

if TYPE_CHECKING:
    from pathlib import Path

    from carla_mcp.carla_protocols import CarlaWorld


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    """Inputs for a CARLA recorder replay."""

    path: Path
    start: float
    duration: float
    follow_id: int
    replay_sensors: bool


def replay_recording(client: object, request: ReplayRequest) -> dict[str, object]:
    """Replay a CARLA recorder file."""
    result = cast("Any", client).replay_file(
        str(request.path),
        request.start,
        request.duration,
        request.follow_id,
        request.replay_sensors,
    )
    return {"path": str(request.path), "result": str(result)}


def recording_collisions(
    client: object,
    *,
    path: Path,
    actor_type: str,
    other_type: str,
) -> dict[str, object]:
    """Return recorder collision report text."""
    report = cast("Any", client).show_recorder_collisions(str(path), actor_type, other_type)
    return {"path": str(path), "report": str(report)}


def recording_actors_blocked(
    client: object,
    *,
    path: Path,
    min_time: float,
    min_distance: float,
) -> dict[str, object]:
    """Return recorder blocked-actor report text."""
    report = cast("Any", client).show_recorder_actors_blocked(str(path), min_time, min_distance)
    return {"path": str(path), "report": str(report)}


def apply_batch(client: object, commands: list[dict[str, object]]) -> dict[str, object]:
    """Apply a small JSON-compatible batch using carla.command."""
    carla_commands = [batch_command(import_module("carla"), command) for command in commands]
    responses = cast("Any", client).apply_batch_sync(carla_commands, do_tick=True)
    return {"responses": [batch_response(response) for response in responses]}


def capability_report(client: object, world: CarlaWorld) -> dict[str, object]:
    """Probe available CARLA capabilities."""
    world_map = world.get_map()
    return {
        "client_version": safe_text_method(client, "get_client_version"),
        "server_version": safe_text_method(client, "get_server_version"),
        "world": capabilities(world, ("get_weather", "set_weather", "get_spectator")),
        "map": capabilities(world_map, ("get_spawn_points", "get_waypoint", "get_topology")),
        "recorder": capabilities(
            client,
            ("replay_file", "show_recorder_collisions", "show_recorder_actors_blocked"),
        ),
        "batch": capabilities(client, ("apply_batch_sync",)),
    }


def batch_command(module: object, command: dict[str, object]) -> object:
    """Build a supported carla.command object."""
    action = str(command.get("action", ""))
    if action == "destroy_actor":
        return cast("Any", module).command.DestroyActor(int_value(command["actor_id"]))
    msg = f"Unsupported batch action: {action}."
    raise CarlaAdapterError(msg)


def batch_response(response: object) -> dict[str, object]:
    """Return a JSON-compatible batch response."""
    return {
        "actor_id": int_attr(response, "actor_id"),
        "error": optional_str_attr(response, "error"),
    }
