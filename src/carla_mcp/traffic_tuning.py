"""Capability-driven per-vehicle Traffic Manager tuning."""

from __future__ import annotations

import math
from importlib import import_module
from typing import TYPE_CHECKING, cast

from carla_mcp.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_mcp.experiment_common import actor, call_required, carla_location

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_mcp.carla_protocols import CarlaWorld
    from carla_mcp.models import TrafficVehiclePathRequest

MAX_TCP_PORT = 65535
MAX_PERCENTAGE = 100.0
MAX_TRAFFIC_PATH_ITEMS = 500
_ROUTE_OPTIONS = frozenset(
    {
        "Void",
        "Left",
        "Right",
        "Straight",
        "LaneFollow",
        "ChangeLaneLeft",
        "ChangeLaneRight",
        "RoadEnd",
    }
)


def tune_traffic_vehicle(
    client: object,
    *,
    actor_id: int,
    traffic_manager_port: int,
    settings: dict[str, object],
) -> dict[str, object]:
    """Apply validated per-vehicle Traffic Manager settings."""
    if not settings:
        msg = "settings must contain at least one per-vehicle Traffic Manager option."
        raise CarlaAdapterError(msg)
    operations = tuple(_traffic_operation(name, value) for name, value in settings.items())
    manager, target = _manager_and_actor(client, traffic_manager_port, actor_id)
    _require_methods(manager, operations)
    for method_name, value in operations:
        call_required(manager, method_name, target, value)
    return {
        "actor_id": actor_id,
        "traffic_manager_port": traffic_manager_port,
        **settings,
    }


def set_traffic_vehicle_path(
    client: object,
    request: TrafficVehiclePathRequest,
) -> dict[str, object]:
    """Upload one bounded location path or road-option route."""
    _validate_path_request(request)
    manager, target = _manager_and_actor(client, request.traffic_manager_port, request.actor_id)
    method_name, items = _path_items(request)
    call_required(manager, method_name, target, items, request.empty_buffer)
    return {
        "actor_id": request.actor_id,
        "traffic_manager_port": request.traffic_manager_port,
        "path_count": len(request.path),
        "route": list(request.route),
        "empty_buffer": request.empty_buffer,
    }


def _validate_path_request(request: TrafficVehiclePathRequest) -> None:
    _require_one_path(request)
    _validate_path_size(request)
    if not isinstance(request.empty_buffer, bool):
        msg = "empty_buffer must be a boolean."
        raise CarlaAdapterError(msg)
    invalid = [item for item in request.route if item not in _ROUTE_OPTIONS]
    if invalid:
        msg = f"Unsupported Traffic Manager route options: {invalid}."
        raise CarlaAdapterError(msg)


def _require_one_path(request: TrafficVehiclePathRequest) -> None:
    if bool(request.path) == bool(request.route):
        msg = "Exactly one non-empty path or route is required."
        raise CarlaAdapterError(msg)


def _validate_path_size(request: TrafficVehiclePathRequest) -> None:
    if len(request.path) + len(request.route) > MAX_TRAFFIC_PATH_ITEMS:
        msg = f"Traffic Manager paths are limited to {MAX_TRAFFIC_PATH_ITEMS} items."
        raise CarlaAdapterError(msg)


def _path_items(request: TrafficVehiclePathRequest) -> tuple[str, list[object] | list[str]]:
    if request.path:
        module = import_module("carla")
        return "set_path", [carla_location(module, item) for item in request.path]
    return "set_route", list(request.route)


def _manager_and_actor(client: object, port: int, actor_id: int) -> tuple[object, object]:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= MAX_TCP_PORT:
        msg = f"traffic_manager_port must be in 1..{MAX_TCP_PORT}."
        raise CarlaAdapterError(msg)
    manager = call_required(client, "get_trafficmanager", port)
    world = call_required(client, "get_world")
    return manager, actor(cast("CarlaWorld", world), actor_id)


def _require_methods(
    manager: object,
    operations: tuple[tuple[str, object], ...],
) -> None:
    missing = next(
        (name for name, _value in operations if not callable(getattr(manager, name, None))),
        None,
    )
    if missing is not None:
        message = f"Connected CARLA runtime does not support {missing}."
        raise UnsupportedFeatureError(message)


def _traffic_operation(name: str, value: object) -> tuple[str, object]:
    definition = _SETTING_DEFINITIONS.get(name)
    if definition is None:
        msg = f"Unsupported per-vehicle Traffic Manager setting: {name}."
        raise CarlaAdapterError(msg)
    method_name, parser = definition
    return method_name, parser(value, name)


def _auto_lane_change(value: object, _name: str) -> bool:
    if not isinstance(value, bool):
        msg = "auto_lane_change must be a boolean."
        raise CarlaAdapterError(msg)
    return value


def _lane_direction(value: object, _name: str) -> bool:
    if value == "left":
        return True
    if value == "right":
        return False
    msg = "force_lane_change must be 'left' or 'right'."
    raise CarlaAdapterError(msg)


def _percentage(value: object, name: str) -> float:
    percentage = _finite_number(value, name)
    if not 0.0 <= percentage <= MAX_PERCENTAGE:
        msg = f"{name} must be in 0..{MAX_PERCENTAGE:g}."
        raise CarlaAdapterError(msg)
    return percentage


def _nonnegative(value: object, name: str) -> float:
    number = _finite_number(value, name)
    if number < 0.0:
        msg = f"{name} must be greater than or equal to zero."
        raise CarlaAdapterError(msg)
    return number


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        msg = f"{name} must be a finite number."
        raise CarlaAdapterError(msg)
    return float(value)


_SETTING_DEFINITIONS: dict[str, tuple[str, Callable[[object, str], object]]] = {
    "auto_lane_change": ("auto_lane_change", _auto_lane_change),
    "force_lane_change": ("force_lane_change", _lane_direction),
    "desired_speed": ("set_desired_speed", _nonnegative),
    "distance_to_leading_vehicle": ("distance_to_leading_vehicle", _nonnegative),
    "ignore_lights_percentage": ("ignore_lights_percentage", _percentage),
    "ignore_signs_percentage": ("ignore_signs_percentage", _percentage),
    "ignore_vehicles_percentage": ("ignore_vehicles_percentage", _percentage),
    "ignore_walkers_percentage": ("ignore_walkers_percentage", _percentage),
}
