"""Capability-driven environment, map-layer, and OpenDRIVE helpers."""

from __future__ import annotations

import math
import re
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.actor_boxes import bounding_box_metadata
from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.experiment_common import (
    call_required,
    required_attribute,
    transform_dict,
    vector_dict,
)
from carla_agentic_toolkit.experiment_ground_truth import finite_vector

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.models import Location

MAX_ENVIRONMENT_OBJECTS = 1000
MAX_ENVIRONMENT_IDS = 1000
MAX_OPENDRIVE_BYTES = 2 * 1024 * 1024
_OPENDRIVE_FIELDS = frozenset(
    {
        "vertex_distance",
        "max_road_length",
        "wall_height",
        "additional_width",
        "smooth_junctions",
        "enable_mesh_visibility",
        "enable_pedestrian_navigation",
    }
)


def get_environment_objects(
    world: object,
    *,
    label: str = "Any",
    max_count: int = 200,
    include_level_bounds: bool = False,
) -> dict[str, object]:
    """Return bounded objects and optional level bounds for one semantic label."""
    _validate_max_count(max_count)
    if not isinstance(include_level_bounds, bool):
        message = "include_level_bounds must be a boolean."
        raise CarlaAdapterError(message)
    enum_value = _enum_value("CityObjectLabel", label)
    objects = list(cast("Any", call_required(world, "get_environment_objects", enum_value)))
    result: dict[str, object] = {
        "label": label,
        "objects": [_environment_object(item) for item in objects[:max_count]],
        "truncated": len(objects) > max_count,
    }
    if include_level_bounds:
        result["level_bounds"] = get_level_bounding_boxes(world, label=label, max_count=max_count)
    return result


def get_level_bounding_boxes(
    world: object,
    *,
    label: str = "Any",
    max_count: int = 200,
    origin: Location | None = None,
    max_distance: float | None = None,
) -> dict[str, object]:
    """Select nearest bounds when an origin is supplied; otherwise retain native order."""
    _validate_max_count(max_count)
    _validate_spatial_query(origin, max_distance)
    enum_value = _enum_value("CityObjectLabel", label)
    boxes = list(cast("Any", call_required(world, "get_level_bbs", enum_value)))
    if origin is None:
        return {
            "label": label,
            "bounding_boxes": [_bounding_box(item) for item in boxes[:max_count]],
            "truncated": len(boxes) > max_count,
        }
    selected = _spatial_boxes(boxes, origin, max_distance)
    return {
        "label": label,
        "bounding_boxes": selected[:max_count],
        "truncated": len(selected) > max_count,
    }


def _validate_spatial_query(origin: Location | None, max_distance: float | None) -> None:
    if origin is not None:
        finite_vector(origin)
    if max_distance is None:
        return
    if origin is None:
        message = "max_distance requires an explicit origin."
        raise CarlaAdapterError(message)
    _validate_max_distance(max_distance)


def _validate_max_distance(max_distance: float) -> None:
    distance = _distance_value(max_distance)
    if not math.isfinite(distance) or distance < 0:
        message = "max_distance must be a finite nonnegative number."
        raise CarlaAdapterError(message)


def _distance_value(max_distance: float) -> float:
    if isinstance(max_distance, bool) or not isinstance(max_distance, int | float):
        message = "max_distance must be a finite nonnegative number."
        raise CarlaAdapterError(message)
    try:
        return float(max_distance)
    except OverflowError as exc:
        message = "max_distance must be a representable finite number."
        raise CarlaAdapterError(message) from exc


def _spatial_boxes(
    boxes: list[object], origin: Location, max_distance: float | None
) -> list[dict[str, object]]:
    measured = [_distance_box(item, origin) for item in boxes]
    eligible = [
        item
        for item in measured
        if max_distance is None or cast("float", item["distance_m"]) <= max_distance
    ]
    return sorted(eligible, key=lambda item: cast("float", item["distance_m"]))


def _distance_box(box: object, origin: Location) -> dict[str, object]:
    measured = bounding_box_metadata(box)
    distance = math.dist(tuple(measured["location"].values()), (origin.x, origin.y, origin.z))
    if not math.isfinite(distance):
        message = "Level bounding box distance must be finite."
        raise CarlaAdapterError(message)
    return {**measured, "distance_m": distance}


def enable_environment_objects(
    world: object,
    *,
    object_ids: tuple[int, ...],
    enabled: bool,
) -> dict[str, object]:
    """Enable or disable explicit environment object IDs."""
    if len(object_ids) > MAX_ENVIRONMENT_IDS or not all(_positive_int(item) for item in object_ids):
        message = f"object_ids must contain at most {MAX_ENVIRONMENT_IDS} positive integers."
        raise CarlaAdapterError(message)
    if not isinstance(enabled, bool):
        message = "enabled must be a boolean."
        raise CarlaAdapterError(message)
    call_required(world, "enable_environment_objects", set(object_ids), enabled)
    return {"object_ids": list(object_ids), "enabled": enabled}


def set_map_layer(world: object, *, layer: str, loaded: bool) -> dict[str, object]:
    """Load or unload one runtime MapLayer enum value."""
    if not isinstance(loaded, bool):
        message = "loaded must be a boolean."
        raise CarlaAdapterError(message)
    method = "load_map_layer" if loaded else "unload_map_layer"
    call_required(world, method, _enum_value("MapLayer", layer))
    return {"layer": layer, "loaded": loaded}


def generate_opendrive_world(
    client: object,
    *,
    opendrive: str,
    parameters: dict[str, object],
    reset_settings: bool,
) -> object:
    """Generate a world from bounded OpenDRIVE XML and known parameters."""
    _validate_opendrive(opendrive)
    unknown = parameters.keys() - _OPENDRIVE_FIELDS
    if unknown:
        message = f"Unsupported OpenDRIVE generation fields: {sorted(unknown)}."
        raise CarlaAdapterError(message)
    parameter_type = required_attribute(import_module("carla"), "OpendriveGenerationParameters")
    if not callable(parameter_type):
        message = "Connected CARLA runtime cannot construct OpendriveGenerationParameters."
        raise UnsupportedFeatureError(message)
    factory = cast("Callable[[], object]", parameter_type)
    parameter_object = factory()
    for name, value in parameters.items():
        _set_parameter(parameter_object, name, value)
    return call_required(
        client,
        "generate_opendrive_world",
        opendrive,
        parameter_object,
        _boolean(reset_settings),
    )


def _validate_max_count(max_count: int) -> None:
    if (
        isinstance(max_count, bool)
        or not isinstance(max_count, int)
        or not 1 <= max_count <= MAX_ENVIRONMENT_OBJECTS
    ):
        message = f"max_count must be in 1..{MAX_ENVIRONMENT_OBJECTS}."
        raise CarlaAdapterError(message)


def _environment_object(value: object) -> dict[str, object]:
    item = cast("Any", value)
    return {
        "id": int(item.id),
        "name": str(item.name),
        "type": str(item.type),
        "transform": transform_dict(item.transform),
        "bounding_box": _bounding_box(item.bounding_box),
    }


def _bounding_box(value: object) -> dict[str, object]:
    box = cast("Any", value)
    rotation = box.rotation
    return {
        "location": vector_dict(box.location),
        "extent": vector_dict(box.extent),
        "rotation": {
            "pitch": float(rotation.pitch),
            "yaw": float(rotation.yaw),
            "roll": float(rotation.roll),
        },
    }


def _enum_value(enum_name: str, member_name: str) -> object:
    if not isinstance(member_name, str) or not member_name:
        message = f"{enum_name} member name must be a non-empty string."
        raise CarlaAdapterError(message)
    enum_type = required_attribute(import_module("carla"), enum_name)
    value = getattr(enum_type, member_name, None)
    if value is None:
        message = f"Connected CARLA runtime does not expose {enum_name}.{member_name}."
        raise UnsupportedFeatureError(message)
    return value


def _validate_opendrive(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        message = "opendrive must be a non-empty XML string."
        raise CarlaAdapterError(message)
    if len(value.encode()) > MAX_OPENDRIVE_BYTES:
        message = f"opendrive must not exceed {MAX_OPENDRIVE_BYTES} UTF-8 bytes."
        raise CarlaAdapterError(message)
    root = re.match(r"(?:<\?xml[^>]*\?>\s*)?<OpenDRIVE(?:\s|/?>)", value.lstrip())
    if root is None:
        message = "opendrive XML root must be OpenDRIVE."
        raise CarlaAdapterError(message)


def _set_parameter(target: object, name: str, value: object) -> None:
    if not hasattr(target, name):
        message = f"Connected CARLA runtime does not expose OpendriveGenerationParameters.{name}."
        raise UnsupportedFeatureError(message)
    current = getattr(target, name)
    parsed = _boolean(value) if isinstance(current, bool) else _finite_float(value)
    setattr(target, name, parsed)


def _finite_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        message = "OpenDRIVE numeric parameters must be finite numbers."
        raise CarlaAdapterError(message)
    return float(value)


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        message = "OpenDRIVE boolean parameters must be booleans."
        raise CarlaAdapterError(message)
    return value


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
