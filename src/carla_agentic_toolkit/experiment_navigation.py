"""Navigation and map runtime helpers for script-only CARLA experiments."""

from __future__ import annotations

import math
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.experiment_common import (
    call_required,
    carla_location,
    float_attr,
    int_attr,
    optional_str_attr,
    optional_transform_dict,
    transform_dict,
)

MAX_LANDMARKS = 1000
MAX_LANDMARK_FILTER_LENGTH = 128

if TYPE_CHECKING:
    from collections.abc import Iterable

    from carla_agentic_toolkit.carla_protocols import CarlaWorld
    from carla_agentic_toolkit.models import Location


def spawn_points(world: CarlaWorld) -> dict[str, object]:
    """Return legal vehicle spawn transforms from the loaded map."""
    return {"spawn_points": [transform_dict(point) for point in world.get_map().get_spawn_points()]}


def waypoint(
    world: CarlaWorld,
    *,
    location: Location,
    lane_type_name: str,
    project_to_road: bool,
) -> dict[str, object]:
    """Return map waypoint metadata for a location."""
    world_map = world.get_map()
    carla_module = import_module("carla")
    try:
        found_waypoint = cast("Any", world_map).get_waypoint(
            carla_location(carla_module, location),
            project_to_road=project_to_road,
            lane_type=lane_type(carla_module, lane_type_name),
        )
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc
    return {"waypoint": waypoint_dict(found_waypoint)}


def route(
    world: CarlaWorld,
    *,
    start: Location,
    end: Location,
    step_meters: float,
    max_steps: int,
) -> dict[str, object]:
    """Generate an A-to-B waypoint route by following official waypoint.next links."""
    world_map = world.get_map()
    carla_module = import_module("carla")
    start_waypoint = cast("Any", world_map).get_waypoint(carla_location(carla_module, start))
    end_location = carla_location(carla_module, end)
    waypoints = follow_waypoints(start_waypoint, end_location, step_meters, max_steps)
    return {
        "step_meters": step_meters,
        "waypoint_count": len(waypoints),
        "route": [waypoint_dict(item) for item in waypoints],
    }


def topology(world: CarlaWorld, *, max_segments: int) -> dict[str, object]:
    """Return a compact road topology graph."""
    segments = cast("Any", world.get_map()).get_topology()
    selected = [
        {"entry": waypoint_dict(entry), "exit": waypoint_dict(exit_)}
        for entry, exit_ in segments[: max(max_segments, 0)]
    ]
    return {
        "segments": selected,
        "returned_segments": len(selected),
        "truncated": len(segments) > len(selected),
    }


def landmarks(
    world: CarlaWorld,
    *,
    max_count: int,
    landmark_type: str | None = None,
    landmark_id: str | None = None,
) -> dict[str, object]:
    """Return bounded landmarks through one official map query variant."""
    _validate_landmark_query(max_count, landmark_type, landmark_id)
    method_name, arguments = _landmark_query(landmark_type, landmark_id)
    values = list(cast("Any", call_required(world.get_map(), method_name, *arguments)))
    selected = values[:max_count]
    return {
        "landmark_type": landmark_type,
        "landmark_id": landmark_id,
        "landmarks": [landmark_dict(landmark) for landmark in selected],
        "returned_landmarks": len(selected),
        "truncated": len(values) > max_count,
    }


def _validate_landmark_query(
    max_count: int,
    landmark_type: str | None,
    landmark_id: str | None,
) -> None:
    _validate_landmark_count(max_count)
    if landmark_type is not None and landmark_id is not None:
        msg = "landmark_type and landmark_id are mutually exclusive."
        raise CarlaAdapterError(msg)
    _validate_landmark_filter(landmark_type)
    _validate_landmark_filter(landmark_id)


def _validate_landmark_count(max_count: int) -> None:
    valid = isinstance(max_count, int) and not isinstance(max_count, bool)
    if not valid or not 1 <= max_count <= MAX_LANDMARKS:
        msg = f"max_count must be in 1..{MAX_LANDMARKS}."
        raise CarlaAdapterError(msg)


def _validate_landmark_filter(value: str | None) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value or len(value) > MAX_LANDMARK_FILTER_LENGTH:
        msg = f"Landmark filters must be non-empty strings up to {MAX_LANDMARK_FILTER_LENGTH}."
        raise CarlaAdapterError(msg)


def _landmark_query(
    landmark_type: str | None,
    landmark_id: str | None,
) -> tuple[str, tuple[str, ...]]:
    if landmark_type is not None:
        return "get_all_landmarks_of_type", (landmark_type,)
    if landmark_id is not None:
        return "get_all_landmarks_from_id", (landmark_id,)
    return "get_all_landmarks", ()


def lane_type(module: object, lane_type_name: str) -> object:
    """Return a CARLA LaneType enum value."""
    return enum_value(cast("Any", module).LaneType, lane_type_name)


def enum_value(enum_type: object, name: str) -> object:
    """Return a CARLA enum member by name."""
    try:
        return getattr(enum_type, name)
    except AttributeError as exc:
        msg = f"Unknown CARLA enum value: {name}."
        raise CarlaAdapterError(msg) from exc


def follow_waypoints(
    start_waypoint: object,
    end_location: object,
    step_meters: float,
    max_steps: int,
) -> list[object]:
    """Follow waypoint.next options toward a destination."""
    route_points = [start_waypoint]
    for _ in range(max(max_steps - 1, 0)):
        choices = cast("Any", route_points[-1]).next(max(step_meters, 0.1))
        if not choices:
            break
        route_points.append(closest_waypoint(choices, end_location))
        if distance(cast("Any", route_points[-1]).transform.location, end_location) <= step_meters:
            break
    return route_points


def closest_waypoint(waypoints: Iterable[object], location: object) -> object:
    """Return the waypoint nearest to a CARLA location."""
    return min(
        waypoints,
        key=lambda waypoint_item: distance(cast("Any", waypoint_item).transform.location, location),
    )


def distance(first: object, second: object) -> float:
    """Return Euclidean distance between CARLA locations."""
    first_location = cast("Any", first)
    second_location = cast("Any", second)
    return math.sqrt(
        (float(first_location.x) - float(second_location.x)) ** 2
        + (float(first_location.y) - float(second_location.y)) ** 2
        + (float(first_location.z) - float(second_location.z)) ** 2
    )


def waypoint_dict(waypoint_item: object) -> dict[str, object]:
    """Return compact waypoint metadata."""
    typed_waypoint = cast("Any", waypoint_item)
    return {
        "road_id": int_attr(typed_waypoint, "road_id"),
        "section_id": int_attr(typed_waypoint, "section_id"),
        "lane_id": int_attr(typed_waypoint, "lane_id"),
        "s": float_attr(typed_waypoint, "s"),
        "lane_type": str(getattr(typed_waypoint, "lane_type", "")),
        "is_junction": bool(getattr(typed_waypoint, "is_junction", False)),
        "transform": transform_dict(typed_waypoint.transform),
    }


def landmark_dict(landmark: object) -> dict[str, object]:
    """Return bounded official landmark scalar metadata."""
    return {
        "id": str(getattr(landmark, "id", "")),
        "name": str(getattr(landmark, "name", "")),
        "type": str(getattr(landmark, "type", "")),
        "sub_type": optional_str_attr(landmark, "sub_type"),
        "road_id": int_attr(landmark, "road_id"),
        "distance": float_attr(landmark, "distance"),
        "s": float_attr(landmark, "s"),
        "t": float_attr(landmark, "t"),
        "is_dynamic": bool(getattr(landmark, "is_dynamic", False)),
        "orientation": optional_str_attr(landmark, "orientation"),
        "z_offset": float_attr(landmark, "z_offset"),
        "value": float_attr(landmark, "value"),
        "unit": optional_str_attr(landmark, "unit"),
        "height": float_attr(landmark, "height"),
        "width": float_attr(landmark, "width"),
        "text": optional_str_attr(landmark, "text"),
        "h_offset": float_attr(landmark, "h_offset"),
        "pitch": float_attr(landmark, "pitch"),
        "roll": float_attr(landmark, "roll"),
        "transform": optional_transform_dict(getattr(landmark, "transform", None)),
    }
