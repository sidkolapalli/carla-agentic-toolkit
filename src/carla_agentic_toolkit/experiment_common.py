"""Shared helpers for script-only CARLA experiment runtime modules."""

from __future__ import annotations

import math
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.models import (
    ActorCounts,
    Location,
    Rotation,
    Transform,
    WorldSettings,
    WorldState,
)

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaSensor, CarlaWorld, ObjectFactory


def world_state(world: CarlaWorld) -> WorldState:
    """Convert a CARLA world into the authoritative state model."""
    return WorldState(
        current_map=map_name(world),
        settings=world_settings(world),
        actor_counts=actor_counts(world),
        frame=frame(world),
        warnings=(),
    )


def world_state_payload(world: CarlaWorld) -> dict[str, object]:
    """Return a JSON-compatible current-world payload."""
    return world_state(world).to_dict()


def map_name(world: CarlaWorld) -> str | None:
    """Return the current CARLA map name when available."""
    try:
        carla_map = world.get_map()
        return str(carla_map.name)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def world_settings(world: CarlaWorld) -> WorldSettings:
    """Convert CARLA world settings into a stable model."""
    settings = world.get_settings()
    return WorldSettings(
        synchronous_mode=bool(settings.synchronous_mode),
        fixed_delta_seconds=settings.fixed_delta_seconds,
        no_rendering_mode=bool(settings.no_rendering_mode),
    )


def actor_counts(world: CarlaWorld) -> ActorCounts:
    """Count common CARLA actor categories."""
    actors = world.get_actors()
    return ActorCounts(
        vehicles=len(actors.filter("vehicle.*")),
        walkers=len(actors.filter("walker.*")),
        sensors=len(actors.filter("sensor.*")),
        traffic=len(actors.filter("traffic.*")),
    )


def frame(world: CarlaWorld) -> int | None:
    """Return the latest CARLA frame ID when available."""
    try:
        return int(world.get_snapshot().frame)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def typed_transform(transform: object) -> Transform:
    """Convert a CARLA transform object into the internal typed model."""
    carla_transform = cast("Any", transform)
    return Transform(
        location=Location(
            x=float(carla_transform.location.x),
            y=float(carla_transform.location.y),
            z=float(carla_transform.location.z),
        ),
        rotation=Rotation(
            pitch=float(carla_transform.rotation.pitch),
            yaw=float(carla_transform.rotation.yaw),
            roll=float(carla_transform.rotation.roll),
        ),
    )


def carla_transform(transform: Transform) -> object:
    """Build a CARLA Transform object from a typed transform."""
    module = import_module("carla")
    location = carla_location(module, transform.location)
    rotation = carla_rotation(module, transform.rotation)
    transform_factory = object_factory(module, "Transform")
    return transform_factory(location, rotation)


def carla_location(module: object, location: Location) -> object:
    """Build a CARLA Location object."""
    location_factory = object_factory(module, "Location")
    return location_factory(x=location.x, y=location.y, z=location.z)


def carla_rotation(module: object, rotation: Rotation) -> object:
    """Build a CARLA Rotation object."""
    rotation_factory = object_factory(module, "Rotation")
    return rotation_factory(pitch=rotation.pitch, yaw=rotation.yaw, roll=rotation.roll)


def actor(world: CarlaWorld, actor_id: int) -> object:
    """Return a CARLA actor by ID."""
    found_actor = world.get_actors().find(actor_id)
    if found_actor is None:
        msg = f"Actor {actor_id} was not found."
        raise CarlaAdapterError(msg)
    return found_actor


def sensor_actor(world: CarlaWorld, sensor_id: int) -> CarlaSensor:
    """Return a sensor actor by ID."""
    found_actor = world.get_actors().find(sensor_id)
    if found_actor is None:
        msg = f"Sensor {sensor_id} was not found."
        raise CarlaAdapterError(msg)
    return require_sensor(found_actor)


def require_sensor(candidate: object) -> CarlaSensor:
    """Validate a dynamic CARLA sensor actor."""
    missing_api = not all(hasattr(candidate, name) for name in ("id", "listen", "stop"))
    if missing_api:
        msg = "CARLA actor does not expose the expected sensor API."
        raise CarlaAdapterError(msg)
    return cast("CarlaSensor", candidate)


def transform_dict(transform: object) -> dict[str, object]:
    """Return a JSON-compatible transform dictionary."""
    return typed_transform(transform).to_dict()


def optional_transform_dict(transform: object | None) -> dict[str, object] | None:
    """Return a transform dictionary when present."""
    if transform is None:
        return None
    return transform_dict(transform)


def vector_dict(vector: object) -> dict[str, object]:
    """Return a JSON-compatible vector dictionary."""
    carla_vector = cast("Any", vector)
    return {
        "x": float(carla_vector.x),
        "y": float(carla_vector.y),
        "z": float(carla_vector.z),
    }


def vector_length(vector: object) -> float:
    """Return vector magnitude."""
    carla_vector = cast("Any", vector)
    return math.sqrt(
        float(carla_vector.x) ** 2 + float(carla_vector.y) ** 2 + float(carla_vector.z) ** 2
    )


def safe_vector_method(target: object, method_name: str) -> dict[str, object] | None:
    """Call a vector-returning method when available."""
    value = safe_method_value(target, method_name)
    if value is None:
        return None
    return vector_dict(value)


def safe_object_dict_method(target: object, method_name: str) -> dict[str, object] | None:
    """Call a method and expose public scalar fields."""
    value = safe_method_value(target, method_name)
    if value is None:
        return None
    return object_public_fields(value)


def safe_numeric_method(target: object, method_name: str) -> float | None:
    """Call a numeric method when available."""
    value = safe_method_value(target, method_name)
    if isinstance(value, int | float):
        return float(value)
    return None


def safe_text_method(target: object, method_name: str) -> str | None:
    """Call a method and stringify the result when available."""
    value = safe_method_value(target, method_name)
    if value is None:
        return None
    return str(value)


def call_required(target: object, method_name: str, *args: object) -> object:
    """Call a runtime capability or report that the connected build lacks it."""
    method = getattr(target, method_name, None)
    if not callable(method):
        message = f"Connected CARLA runtime does not support {method_name}."
        raise UnsupportedFeatureError(message)
    try:
        return method(*args)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc


def required_attribute(target: object, name: str) -> object:
    """Return a runtime attribute or a capability-specific error."""
    value = getattr(target, name, None)
    if value is None:
        message = f"Connected CARLA runtime does not expose {name}."
        raise UnsupportedFeatureError(message)
    return value


def safe_method_value(target: object, method_name: str) -> object | None:
    """Call a no-argument method and return None when unsupported."""
    method = getattr(target, method_name, None)
    if not callable(method):
        return None
    try:
        return method()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def capabilities(target: object, names: tuple[str, ...]) -> dict[str, bool]:
    """Return whether target exposes each named method."""
    return {name: callable(getattr(target, name, None)) for name in names}


def unavailable(reason: str) -> dict[str, object]:
    """Return a stable unavailable capability payload."""
    return {"available": False, "reason": reason}


def object_public_fields(value: object) -> dict[str, object]:
    """Expose public scalar fields from a CARLA value object."""
    fields: dict[str, object] = {}
    for key in dir(value):
        item = getattr(value, key)
        if is_public_scalar_field(key, item):
            fields[key] = item
    return fields


def is_public_scalar_field(key: str, value: object) -> bool:
    """Return whether a field should be exposed in JSON."""
    return not key.startswith("_") and isinstance(value, bool | int | float | str | type(None))


def float_value(value: object) -> float:
    """Parse a numeric value as float."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = "Expected a numeric value."
        raise TypeError(msg)
    return float(value)


def int_value(value: object) -> int:
    """Parse an integer value."""
    if isinstance(value, bool) or not isinstance(value, int):
        msg = "Expected an integer value."
        raise TypeError(msg)
    return value


def bool_value(value: object) -> bool:
    """Parse a boolean value."""
    if isinstance(value, bool):
        return value
    msg = "Expected a boolean value."
    raise TypeError(msg)


def int_attr(target: object, name: str) -> int | None:
    """Return an integer attribute when present."""
    value = getattr(target, name, None)
    if isinstance(value, int):
        return value
    return None


def float_attr(target: object, name: str) -> float | None:
    """Return a float attribute when present."""
    value = getattr(target, name, None)
    if isinstance(value, int | float):
        return float(value)
    return None


def optional_str_attr(target: object, name: str) -> str | None:
    """Return a string attribute when present."""
    value = getattr(target, name, None)
    if value is None:
        return None
    return str(value)


def nested_id(target: object, name: str) -> int | None:
    """Return nested actor id when present."""
    value = getattr(target, name, None)
    return int_attr(value, "id")


def object_factory(module: object, name: str) -> ObjectFactory:
    """Return a named CARLA object factory."""
    factory = getattr(module, name, None)
    if callable(factory):
        return cast("ObjectFactory", factory)
    msg = f"CARLA module does not expose {name}."
    raise CarlaAdapterError(msg)
