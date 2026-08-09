"""Capability-driven actor and vehicle physics helpers."""

from __future__ import annotations

import math
from importlib import import_module
from typing import Any, cast

from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.experiment_common import (
    actor,
    call_required,
    object_factory,
    vector_dict,
)
from carla_agentic_toolkit.models import Location

_PHYSICS_ACTIONS = {
    "impulse": "add_impulse",
    "force": "add_force",
    "torque": "add_torque",
    "angular_impulse": "add_angular_impulse",
    "target_angular_velocity": "set_target_angular_velocity",
}
_FLOAT_FIELDS = (
    "mass",
    "drag_coefficient",
    "max_rpm",
    "moi",
    "damping_rate_full_throttle",
    "damping_rate_zero_throttle_clutch_engaged",
    "damping_rate_zero_throttle_clutch_disengaged",
    "gear_switch_time",
    "clutch_strength",
    "final_ratio",
)
_BOOL_FIELDS = ("use_gear_autobox", "use_sweep_wheel_collision")
_SUPPORTED_FIELDS = frozenset((*_FLOAT_FIELDS, *_BOOL_FIELDS, "center_of_mass"))


def configure_actor_physics(
    world: object,
    *,
    actor_id: int,
    simulate_physics: bool | None,
    gravity: bool | None,
) -> dict[str, object]:
    """Toggle the official actor physics and gravity capabilities."""
    if simulate_physics is None and gravity is None:
        message = "At least one of simulate_physics or gravity is required."
        raise CarlaAdapterError(message)
    target = actor(cast("Any", world), actor_id)
    if simulate_physics is not None:
        call_required(target, "set_simulate_physics", _bool(simulate_physics))
    if gravity is not None:
        call_required(target, "set_enable_gravity", _bool(gravity))
    return {
        "actor_id": actor_id,
        "simulate_physics": simulate_physics,
        "gravity": gravity,
    }


def apply_actor_physics(
    world: object,
    *,
    actor_id: int,
    action: str,
    vector: Location,
) -> dict[str, object]:
    """Apply one official vector-based actor physics operation."""
    method_name = _PHYSICS_ACTIONS.get(action)
    if method_name is None:
        message = f"Unknown physics action '{action}'; expected {sorted(_PHYSICS_ACTIONS)}."
        raise CarlaAdapterError(message)
    module = import_module("carla")
    factory = object_factory(module, "Vector3D")
    carla_vector = factory(x=vector.x, y=vector.y, z=vector.z)
    call_required(actor(cast("Any", world), actor_id), method_name, carla_vector)
    return {"actor_id": actor_id, "action": action, "vector": vector.to_dict()}


def get_vehicle_physics(world: object, actor_id: int) -> dict[str, object]:
    """Return a bounded common subset of VehiclePhysicsControl."""
    control = call_required(actor(cast("Any", world), actor_id), "get_physics_control")
    return _physics_payload(actor_id, control)


def update_vehicle_physics(
    world: object,
    *,
    actor_id: int,
    changes: dict[str, object],
) -> dict[str, object]:
    """Update a fixed common subset of VehiclePhysicsControl fields."""
    unknown = changes.keys() - _SUPPORTED_FIELDS
    if unknown:
        message = f"Unsupported vehicle physics fields: {sorted(unknown)}."
        raise CarlaAdapterError(message)
    target = actor(cast("Any", world), actor_id)
    control = call_required(target, "get_physics_control")
    for name, value in changes.items():
        _set_physics_field(control, name, value)
    call_required(target, "apply_physics_control", control)
    return _physics_payload(actor_id, control)


def _physics_payload(actor_id: int, control: object) -> dict[str, object]:
    payload: dict[str, object] = {"actor_id": actor_id}
    for name in (*_FLOAT_FIELDS, *_BOOL_FIELDS):
        value = getattr(control, name, None)
        if isinstance(value, bool | int | float):
            payload[name] = value
    center = getattr(control, "center_of_mass", None)
    if center is not None:
        payload["center_of_mass"] = vector_dict(center)
    return payload


def _set_physics_field(control: object, name: str, value: object) -> None:
    if not hasattr(control, name):
        message = f"Connected CARLA does not expose VehiclePhysicsControl.{name}."
        raise UnsupportedFeatureError(message)
    if name in _FLOAT_FIELDS:
        setattr(control, name, _finite_float(value))
    elif name in _BOOL_FIELDS:
        setattr(control, name, _bool(value))
    else:
        setattr(control, name, _carla_location(value))


def _carla_location(value: object) -> object:
    if not isinstance(value, dict):
        message = "center_of_mass must be an object with x, y, and z."
        raise CarlaAdapterError(message)
    location = Location(
        x=_finite_float(value.get("x")),
        y=_finite_float(value.get("y")),
        z=_finite_float(value.get("z")),
    )
    return object_factory(import_module("carla"), "Location")(**location.to_dict())


def _finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        message = "Vehicle physics numeric fields must be finite numbers."
        raise CarlaAdapterError(message)
    return float(value)


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        message = "Actor physics flags must be booleans."
        raise CarlaAdapterError(message)
    return value
