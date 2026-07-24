"""Scene and environment runtime helpers for script-only CARLA experiments."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_mcp.experiment_common import (
    actor,
    carla_transform,
    object_factory,
    object_public_fields,
    typed_transform,
    unavailable,
)
from carla_mcp.experiment_navigation import enum_value

if TYPE_CHECKING:
    from carla_mcp.carla_protocols import CarlaWorld
    from carla_mcp.models import Transform


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
    """Move the spectator camera."""
    spectator = cast("Any", world).get_spectator()
    spectator.set_transform(carla_transform(transform))
    return {"spectator": transform.to_dict()}


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
