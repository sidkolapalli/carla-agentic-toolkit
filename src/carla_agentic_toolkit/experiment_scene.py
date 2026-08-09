"""Scene and environment runtime helpers for script-only CARLA experiments."""

from __future__ import annotations

import math
import time
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.experiment_common import (
    actor,
    call_required,
    carla_transform,
    object_factory,
    object_public_fields,
    typed_transform,
    unavailable,
)
from carla_agentic_toolkit.experiment_navigation import enum_value
from carla_agentic_toolkit.models import Location, Rotation, Transform

MAX_WATCH_SECONDS = 30.0
MAX_CHASE_DISTANCE = 30.0
MAX_CHASE_HEIGHT = 15.0

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaWorld


def freeze_traffic_lights(world: CarlaWorld, *, enabled: bool) -> dict[str, object]:
    """Freeze or unfreeze all traffic lights."""
    cast("Any", world).freeze_all_traffic_lights(enabled)
    return {"frozen": enabled}


def set_traffic_light_state(world: CarlaWorld, *, actor_id: int, state: str) -> dict[str, object]:
    """Set one traffic light state."""
    carla_module = import_module("carla")
    traffic_light_state = cast("Any", carla_module).TrafficLightState
    cast("Any", actor(world, actor_id)).set_state(enum_value(traffic_light_state, state))
    return {"actor_id": actor_id, "state": state}


def set_spectator(world: CarlaWorld, transform: Transform) -> dict[str, object]:
    """Move the spectator camera and return its previous transform."""
    spectator = cast("Any", world).get_spectator()
    previous = typed_transform(spectator.get_transform())
    spectator.set_transform(carla_transform(transform))
    return {
        "spectator": transform.to_dict(),
        "previous_spectator": previous.to_dict(),
    }


def watch_actor(
    world: CarlaWorld,
    *,
    actor_id: int,
    seconds: float,
    distance: float,
    height: float,
) -> dict[str, object]:
    """Follow an actor from a yaw-relative chase view on each simulator frame."""
    _bounded_watch_value(seconds, "seconds", 0.0, MAX_WATCH_SECONDS)
    _bounded_watch_value(distance, "distance", 0.0, MAX_CHASE_DISTANCE)
    _bounded_watch_value(height, "height", 0.0, MAX_CHASE_HEIGHT)
    target = cast("Any", actor(world, actor_id))
    spectator = cast("Any", world).get_spectator()
    previous = typed_transform(spectator.get_transform())
    deadline = time.monotonic() + seconds
    samples = 0
    try:
        while time.monotonic() < deadline:
            chase = _chase_transform(typed_transform(target.get_transform()), distance, height)
            spectator.set_transform(carla_transform(chase))
            samples += 1
            call_required(world, "wait_for_tick", 1.0)
    finally:
        spectator.set_transform(carla_transform(previous))
    return {
        "actor_id": actor_id,
        "seconds": seconds,
        "samples": samples,
        "distance": distance,
        "height": height,
        "spectator_restored": True,
    }


def _chase_transform(transform: Transform, distance: float, height: float) -> Transform:
    yaw = math.radians(transform.rotation.yaw)
    return Transform(
        location=Location(
            x=transform.location.x - math.cos(yaw) * distance,
            y=transform.location.y - math.sin(yaw) * distance,
            z=transform.location.z + height,
        ),
        rotation=Rotation(pitch=-12.0, yaw=transform.rotation.yaw, roll=0.0),
    )


def _bounded_watch_value(value: float, name: str, lower: float, upper: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not lower < value <= upper
    ):
        message = f"{name} must be finite and in ({lower:g}, {upper:g}]."
        raise CarlaAdapterError(message)


def spectator_transform(world: CarlaWorld) -> Transform:
    """Return the spectator transform as an internal model."""
    return typed_transform(cast("Any", world).get_spectator().get_transform())


def weather(world: CarlaWorld) -> dict[str, object]:
    """Return current weather when supported."""
    method = getattr(world, "get_weather", None)
    if not callable(method):
        return unavailable("world.get_weather is not available in this CARLA build.")
    return {"weather": object_public_fields(method())}


def set_weather(world: CarlaWorld, parameters: dict[str, float]) -> dict[str, object]:
    """Set weather parameters when supported."""
    if not hasattr(world, "set_weather"):
        return unavailable("world.set_weather is not available in this CARLA build.")
    carla_weather = weather_parameters(import_module("carla"), parameters)
    cast("Any", world).set_weather(carla_weather)
    return {"weather": object_public_fields(carla_weather)}


def weather_parameters(module: object, parameters: dict[str, float]) -> object:
    """Build a CARLA WeatherParameters object from provided scalar fields."""
    weather = object_factory(module, "WeatherParameters")()
    for key, value in parameters.items():
        if hasattr(weather, key):
            setattr(weather, key, float(value))
    return weather
