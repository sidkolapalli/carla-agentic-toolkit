"""Replay, batch, and capability helpers for script-only CARLA experiments."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.experiment_common import (
    capabilities,
    int_attr,
    int_value,
    optional_str_attr,
    safe_text_method,
)

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaWorld


@dataclass(frozen=True, slots=True)
class ReplayRequest:
    """Inputs for a CARLA recorder replay."""

    path: Path
    start: float
    duration: float
    follow_id: int
    replay_sensors: bool
    do_tick: bool = True


def replay_recording(client: object, request: ReplayRequest) -> dict[str, object]:
    """Replay a CARLA recorder file; only do_tick=True is supported."""
    _validate_tick_flag(request.do_tick)
    if not request.do_tick:
        message = "CARLA replay_file does not expose a non-ticking replay capability."
        raise UnsupportedFeatureError(message)
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
    """Query collisions: h=hero, v=vehicle, w=walker, t=traffic light, o=other, a=any."""
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


def apply_batch(
    client: object, commands: list[dict[str, object]], *, do_tick: bool = False
) -> dict[str, object]:
    """Apply reviewed commands with CARLA's non-ticking apply_batch_sync default."""
    _validate_tick_flag(do_tick)
    carla_commands = [batch_command(import_module("carla"), command) for command in commands]
    responses = cast("Any", client).apply_batch_sync(carla_commands, do_tick=do_tick)
    return {"responses": [batch_response(response) for response in responses]}


def _validate_tick_flag(do_tick: object) -> None:
    if type(do_tick) is not bool:
        message = "do_tick must be a boolean."
        raise CarlaAdapterError(message)


def capability_report(client: object, world: CarlaWorld) -> dict[str, object]:
    """Probe available CARLA capabilities without version-string assumptions."""
    world_map = world.get_map()
    carla_module = import_module("carla")
    return {
        "client_version": safe_text_method(client, "get_client_version"),
        "server_version": safe_text_method(client, "get_server_version"),
        "world": capabilities(world, ("get_weather", "set_weather", "get_spectator")),
        "map": capabilities(
            world_map,
            (
                "get_spawn_points",
                "get_waypoint",
                "get_topology",
                "get_all_landmarks",
                "get_all_landmarks_of_type",
                "get_all_landmarks_from_id",
            ),
        ),
        "recorder": capabilities(
            client,
            ("replay_file", "show_recorder_collisions", "show_recorder_actors_blocked"),
        ),
        "batch": capabilities(client, ("apply_batch_sync",)),
        "environment": capabilities(
            world,
            (
                "get_environment_objects",
                "enable_environment_objects",
                "load_map_layer",
                "unload_map_layer",
                "get_level_bbs",
            ),
        ),
        "opendrive": capabilities(client, ("generate_opendrive_world",)),
        "actor_semantic_tags": hasattr(getattr(carla_module, "Actor", None), "semantic_tags"),
        "actor_physics": capabilities(
            getattr(carla_module, "Actor", None),
            (
                "set_simulate_physics",
                "set_enable_gravity",
                "add_impulse",
                "add_force",
                "add_torque",
                "set_target_angular_velocity",
            ),
        ),
        "vehicle_physics": capabilities(
            getattr(carla_module, "Vehicle", None),
            ("get_physics_control", "apply_physics_control"),
        ),
        "traffic_manager_vehicle": capabilities(
            getattr(carla_module, "TrafficManager", None),
            (
                "auto_lane_change",
                "force_lane_change",
                "set_desired_speed",
                "distance_to_leading_vehicle",
                "ignore_lights_percentage",
                "ignore_signs_percentage",
                "ignore_vehicles_percentage",
                "ignore_walkers_percentage",
                "set_path",
                "set_route",
            ),
        ),
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
