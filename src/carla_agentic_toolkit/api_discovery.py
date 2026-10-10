"""Runtime discovery for the curated script facade."""

from __future__ import annotations

from inspect import signature
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.models import JsonObject

CARLA_EQUIVALENTS = {
    "list_worlds": "carla.Client.get_available_maps",
    "load_world": "carla.Client.load_world",
    "reload_world": "carla.Client.reload_world",
    "tick": "carla.World.tick",
    "attach_camera": "carla.World.spawn_actor",
    "attach_sensor": "carla.World.spawn_actor",
    "attach_event_sensor": "carla.World.spawn_actor",
    "get_spawn_points": "carla.Map.get_spawn_points",
    "get_waypoint": "carla.Map.get_waypoint",
    "get_topology": "carla.Map.get_topology",
    "enable_environment_objects": "carla.World.enable_environment_objects",
    "generate_opendrive_world": "carla.Client.generate_opendrive_world",
    "get_vehicle_physics": "carla.Vehicle.get_physics_control",
    "apply_vehicle_control": "carla.Vehicle.apply_control",
    "set_actor_transform": "carla.Actor.set_transform",
    "set_vehicle_lights": "carla.Vehicle.set_light_state",
    "set_target_velocity": "carla.Actor.set_target_velocity",
    "set_walker_destination": "carla.WalkerAIController.go_to_location",
    "apply_walker_control": "carla.Walker.apply_control",
    "freeze_traffic_lights": "carla.World.freeze_all_traffic_lights",
    "set_traffic_light_state": "carla.TrafficLight.set_state",
    "set_spectator": "carla.Actor.set_transform",
    "get_weather": "carla.World.get_weather",
    "set_weather": "carla.World.set_weather",
    "replay_recording": "carla.Client.replay_file",
    "query_recording_collisions": "carla.Client.show_recorder_collisions",
    "query_recording_actors_blocked": "carla.Client.show_recorder_actors_blocked",
    "apply_batch": "carla.Client.apply_batch_sync",
    "record_episode": "carla.Client.start_recorder",
    "stop_recording": "carla.Client.stop_recorder",
}


def method_catalog(api: object) -> JsonObject:
    """Return signatures, summaries and primary native equivalents for public methods."""
    return {
        name: _method_description(getattr(api, name), name=name)
        for name in dir(api)
        if callable(getattr(api, name)) and not name.startswith("_")
    }


def _method_description(method: object, *, name: str) -> JsonObject:
    doc = getattr(method, "__doc__", "") or ""
    typed_method = cast("Callable[..., object]", method)
    return {
        "signature": str(signature(typed_method)),
        "doc": doc.strip().splitlines()[0] if doc.strip() else "",
        "carla_equivalent": CARLA_EQUIVALENTS.get(name),
    }
