"""Input parsing for MCP tool payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import (
    AutopilotRequest,
    CameraAttachRequest,
    Location,
    Rotation,
    SpawnRequest,
    TrafficControllerStartRequest,
    TrafficDensityRequest,
    TrafficManagerRequest,
    TrafficPopulationRequest,
    TrafficVehiclePathRequest,
    Transform,
    VehicleBehaviorRequest,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Any

_SENSOR_BLUEPRINTS = {
    "rgb": "sensor.camera.rgb",
    "camera.rgb": "sensor.camera.rgb",
    "depth": "sensor.camera.depth",
    "camera.depth": "sensor.camera.depth",
    "semantic_segmentation": "sensor.camera.semantic_segmentation",
    "instance_segmentation": "sensor.camera.instance_segmentation",
    "lidar": "sensor.lidar.ray_cast",
    "semantic_lidar": "sensor.lidar.ray_cast_semantic",
    "radar": "sensor.other.radar",
    "imu": "sensor.other.imu",
    "gnss": "sensor.other.gnss",
    "collision": "sensor.other.collision",
    "lane_invasion": "sensor.other.lane_invasion",
    "obstacle": "sensor.other.obstacle",
}


def sensor_blueprint(kind: str) -> str:
    """Resolve a friendly sensor kind or preserve a runtime blueprint ID."""
    if kind.startswith("sensor."):
        return kind
    try:
        return _SENSOR_BLUEPRINTS[kind]
    except KeyError as exc:
        message = f"Unknown sensor kind {kind!r}."
        raise CarlaAdapterError(message) from exc


def zero_transform() -> dict[str, object]:
    """Return a zero-relative transform payload."""
    return {
        "location": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
    }


def parse_spawn_requests(payloads: list[dict[str, object]]) -> tuple[SpawnRequest, ...]:
    """Parse JSON-compatible spawn request payloads into typed requests."""
    return tuple(_spawn_request(payload) for payload in payloads)


def parse_transform(payload: dict[str, object]) -> Transform:
    """Parse a JSON-compatible transform payload."""
    return _transform(payload)


def parse_location(payload: dict[str, object]) -> Location:
    """Parse a JSON-compatible location/vector payload."""
    return _location(payload)


def parse_camera_attach_request(payload: dict[str, object]) -> CameraAttachRequest:
    """Parse a JSON-compatible camera attach payload."""
    return CameraAttachRequest(
        blueprint_id=_string_field(payload, "blueprint_id"),
        transform=_transform(_mapping_field(payload, "transform")),
        attributes=_string_mapping_field(payload, "attributes"),
        parent_actor_id=_optional_int_field(payload, "parent_actor_id"),
    )


def parse_traffic_population_request(payload: dict[str, object]) -> TrafficPopulationRequest:
    """Parse a JSON-compatible traffic population request."""
    fields = _provided_fields(
        payload,
        {
            "vehicle_count": _int_field,
            "traffic_manager_port": _int_field,
            "seed": _int_field,
            "safe_filter": _bool_field,
            "advance_world": _bool_field,
            "global_distance_to_leading_vehicle": _float_field,
            "global_percentage_speed_difference": _float_field,
        },
    )
    return TrafficPopulationRequest(**cast("Any", fields))


def parse_traffic_density_request(payload: dict[str, object]) -> TrafficDensityRequest:
    """Parse a JSON-compatible traffic density request."""
    fields = _provided_fields(
        payload,
        {
            "vehicle_count": _int_field,
            "traffic_manager_port": _int_field,
            "seed": _int_field,
            "safe_filter": _bool_field,
            "reset_existing": _bool_field,
            "global_distance_to_leading_vehicle": _float_field,
            "global_percentage_speed_difference": _float_field,
        },
    )
    return TrafficDensityRequest(**cast("Any", fields))


def parse_traffic_controller_start_request(
    payload: dict[str, object],
    *,
    host: str,
    port: int,
    timeout_seconds: float,
) -> TrafficControllerStartRequest:
    """Parse a JSON-compatible traffic controller start request."""
    return TrafficControllerStartRequest(
        density=parse_traffic_density_request(payload),
        host=host,
        port=port,
        timeout_seconds=timeout_seconds,
    )


def parse_autopilot_request(payload: dict[str, object]) -> AutopilotRequest:
    """Parse a JSON-compatible autopilot request."""
    actor_ids = _list_field(payload, "actor_ids")
    fields = _provided_fields(
        payload,
        {"enabled": _bool_field, "traffic_manager_port": _int_field, "advance_world": _bool_field},
    )
    return AutopilotRequest(
        actor_ids=tuple(_int_value(actor_id, "actor_ids") for actor_id in actor_ids),
        **cast("Any", fields),
    )


def parse_vehicle_behavior_request(payload: dict[str, object]) -> VehicleBehaviorRequest:
    """Parse a JSON-compatible vehicle behavior request."""
    actor_ids = _list_field(payload, "actor_ids")
    fields = _provided_fields(payload, {"traffic_manager_port": _int_field})
    return VehicleBehaviorRequest(
        actor_ids=tuple(_int_value(actor_id, "actor_ids") for actor_id in actor_ids),
        profile=_string_field(payload, "profile"),
        **cast("Any", fields),
    )


def parse_traffic_vehicle_path_request(
    actor_id: int,
    payload: dict[str, object],
) -> TrafficVehiclePathRequest:
    """Parse one per-vehicle Traffic Manager path request."""
    path_values = payload.get("path", [])
    route_values = payload.get("route", [])
    if not isinstance(path_values, list) or not isinstance(route_values, list):
        msg = "path and route must be lists."
        raise TypeError(msg)
    fields = _provided_fields(
        payload,
        {"traffic_manager_port": _int_field, "empty_buffer": _bool_field},
    )
    return TrafficVehiclePathRequest(
        actor_id=actor_id,
        path=tuple(_location(_mapping_value(item, "path")) for item in path_values),
        route=tuple(_string_value(item, "route") for item in route_values),
        **cast("Any", fields),
    )


def parse_traffic_manager_request(payload: dict[str, object]) -> TrafficManagerRequest:
    """Parse a JSON-compatible Traffic Manager request."""
    fields = _provided_fields(
        payload,
        {
            "traffic_manager_port": _int_field,
            "global_distance_to_leading_vehicle": _float_field,
            "global_percentage_speed_difference": _float_field,
            "seed": _int_field,
            "synchronous_mode": _bool_field,
        },
    )
    return TrafficManagerRequest(**cast("Any", fields))


def _spawn_request(payload: Mapping[str, object]) -> SpawnRequest:
    """Parse one spawn request."""
    return SpawnRequest(
        blueprint_id=_string_field(payload, "blueprint_id"),
        transform=_transform(_mapping_field(payload, "transform")),
        attributes=_string_mapping_field(payload, "attributes"),
    )


def _transform(payload: Mapping[str, object]) -> Transform:
    """Parse a transform payload."""
    return Transform(
        location=_location(_mapping_field(payload, "location")),
        rotation=_rotation(_mapping_field(payload, "rotation")),
    )


def _location(payload: Mapping[str, object]) -> Location:
    """Parse a location payload."""
    return Location(
        x=_float_field(payload, "x"),
        y=_float_field(payload, "y"),
        z=_float_field(payload, "z"),
    )


def _rotation(payload: Mapping[str, object]) -> Rotation:
    """Parse a rotation payload."""
    return Rotation(
        pitch=_float_field(payload, "pitch"),
        yaw=_float_field(payload, "yaw"),
        roll=_float_field(payload, "roll"),
    )


def _string_field(payload: Mapping[str, object], key: str) -> str:
    """Read a required string field."""
    value = payload[key]
    if isinstance(value, str):
        return value
    msg = f"{key} must be a string."
    raise TypeError(msg)


def _float_field(payload: Mapping[str, object], key: str) -> float:
    """Read a required finite numeric field as a float.

    Booleans are rejected because :class:`bool` is a subclass of :class:`int`
    and model-authored ``true``/``false`` should not silently become ``1.0``/``0.0``.
    """
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{key} must be a finite number."
        raise TypeError(msg)
    return float(value)


def _int_field(payload: Mapping[str, object], key: str) -> int:
    """Read a required integer field."""
    value = payload[key]
    # Reject bool because it is a subclass of int.
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{key} must be an integer."
        raise TypeError(msg)
    return value


def _bool_field(payload: Mapping[str, object], key: str) -> bool:
    """Read a required boolean field."""
    value = payload[key]
    if isinstance(value, bool):
        return value
    msg = f"{key} must be a boolean."
    raise TypeError(msg)


def _optional_int_field(payload: Mapping[str, object], key: str) -> int | None:
    """Read an optional integer field."""
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{key} must be an integer or null."
        raise TypeError(msg)
    return value


def _list_field(payload: Mapping[str, object], key: str) -> list[object]:
    """Read a required list field."""
    value = payload[key]
    if isinstance(value, list):
        return cast("list[object]", value)
    msg = f"{key} must be a list."
    raise TypeError(msg)


def _int_value(value: object, context: str) -> int:
    """Read an integer list value."""
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{context} values must be integers."
        raise TypeError(msg)
    return value


def _mapping_value(value: object, context: str) -> Mapping[str, object]:
    """Read one object value outside a keyed payload."""
    if isinstance(value, dict):
        return cast("Mapping[str, object]", value)
    msg = f"{context} values must be objects."
    raise TypeError(msg)


def _mapping_field(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
    """Read a required object field."""
    value = payload[key]
    if isinstance(value, dict):
        return cast("Mapping[str, object]", value)
    msg = f"{key} must be an object."
    raise TypeError(msg)


def _string_mapping_field(payload: Mapping[str, object], key: str) -> dict[str, str]:
    """Read a required string-to-string object field."""
    value = payload[key]
    if not isinstance(value, dict):
        msg = f"{key} must be an object."
        raise TypeError(msg)
    return {str(item_key): _string_value(item_value, key) for item_key, item_value in value.items()}


def _string_value(value: object, context: str) -> str:
    """Read a string mapping value."""
    if isinstance(value, str):
        return value
    msg = f"{context} values must be strings."
    raise TypeError(msg)


def _provided_fields(
    payload: Mapping[str, object],
    parsers: Mapping[str, Callable[[Mapping[str, object], str], object]],
) -> dict[str, object]:
    """Parse only supplied non-null fields so model defaults stay authoritative."""
    return {
        key: parser(payload, key) for key, parser in parsers.items() if payload.get(key) is not None
    }
