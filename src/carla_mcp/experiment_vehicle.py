"""Direct vehicle-control runtime helpers for script-only CARLA experiments."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_mcp.errors import CarlaAdapterError
from carla_mcp.experiment_common import (
    actor,
    bool_value,
    carla_location,
    carla_transform,
    float_value,
    int_value,
    object_factory,
    object_public_fields,
    safe_numeric_method,
    safe_object_dict_method,
    safe_text_method,
    safe_vector_method,
    transform_dict,
    vector_dict,
    vector_length,
)

if TYPE_CHECKING:
    from carla_mcp.carla_protocols import CarlaWorld
    from carla_mcp.models import Location, Transform


def apply_vehicle_control(
    world: CarlaWorld,
    *,
    actor_id: int,
    control: dict[str, object],
) -> dict[str, object]:
    """Apply direct VehicleControl to one actor."""
    vehicle = actor(world, actor_id)
    carla_module = import_module("carla")
    carla_control = vehicle_control(carla_module, control)
    try:
        cast("Any", vehicle).apply_control(carla_control)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc
    return {"actor_id": actor_id, "applied_control": control_dict(carla_control)}


def vehicle_telemetry(world: CarlaWorld, actor_id: int) -> dict[str, object]:
    """Return vehicle state useful for closed-loop control."""
    vehicle = actor(world, actor_id)
    return {
        "actor_id": actor_id,
        "transform": transform_dict(cast("Any", vehicle).get_transform()),
        "velocity": vector_dict(cast("Any", vehicle).get_velocity()),
        "acceleration": safe_vector_method(vehicle, "get_acceleration"),
        "control": safe_object_dict_method(vehicle, "get_control"),
        "speed_limit": safe_numeric_method(vehicle, "get_speed_limit"),
        "traffic_light_state": safe_text_method(vehicle, "get_traffic_light_state"),
        "speed_mps": vector_length(cast("Any", vehicle).get_velocity()),
    }


def set_actor_transform(
    world: CarlaWorld,
    *,
    actor_id: int,
    transform: Transform,
) -> dict[str, object]:
    """Teleport an actor to a transform."""
    cast("Any", actor(world, actor_id)).set_transform(carla_transform(transform))
    return {"actor_id": actor_id, "transform": transform.to_dict()}


def set_vehicle_lights(world: CarlaWorld, *, actor_id: int, state: str | int) -> dict[str, object]:
    """Set vehicle light state by integer mask or pipe-separated names."""
    carla_module = import_module("carla")
    light_state = vehicle_light_state(carla_module, state)
    cast("Any", actor(world, actor_id)).set_light_state(light_state)
    return {"actor_id": actor_id, "light_state": str(light_state)}


def set_target_velocity(
    world: CarlaWorld,
    *,
    actor_id: int,
    velocity: Location,
) -> dict[str, object]:
    """Set actor target velocity."""
    target_velocity = carla_location(import_module("carla"), velocity)
    cast("Any", actor(world, actor_id)).set_target_velocity(target_velocity)
    return {"actor_id": actor_id, "target_velocity": velocity.to_dict()}


def vehicle_control(module: object, values: dict[str, object]) -> object:
    """Build a CARLA VehicleControl object from JSON-compatible values."""
    factory = object_factory(module, "VehicleControl")
    return factory(
        throttle=float_value(values.get("throttle", 0.0)),
        steer=float_value(values.get("steer", 0.0)),
        brake=float_value(values.get("brake", 0.0)),
        hand_brake=bool_value(values.get("hand_brake", False)),
        reverse=bool_value(values.get("reverse", False)),
        manual_gear_shift=bool_value(values.get("manual_gear_shift", False)),
        gear=int_value(values.get("gear", 0)),
    )


def control_dict(control: object) -> dict[str, object]:
    """Expose public scalar control fields."""
    return object_public_fields(control)


def vehicle_light_state(module: object, state: str | int) -> object:
    """Build a CARLA VehicleLightState value."""
    factory = object_factory(module, "VehicleLightState")
    if isinstance(state, int):
        return factory(state)
    bitmask = 0
    enum_type = cast("Any", module).VehicleLightState
    for part in state.split("|"):
        bitmask |= int(getattr(enum_type, part.strip()))
    return factory(bitmask)
