"""Input parsing for MCP tool payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from carla_mcp.models import (
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
    from collections.abc import Mapping

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
    return _SENSOR_BLUEPRINTS[kind]


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
    return TrafficPopulationRequest(
        vehicle_count=_int_field_or_default(payload, "vehicle_count", default=30),
        traffic_manager_port=_int_field_or_default(
            payload,
            "traffic_manager_port",
            default=8000,
        ),
        seed=_int_field_or_default(payload, "seed", default=0),
        safe_filter=_bool_field_or_default(payload, "safe_filter", default=True),
        global_distance_to_leading_vehicle=_float_field_or_default(
            payload,
            "global_distance_to_leading_vehicle",
            default=2.5,
        ),
        global_percentage_speed_difference=_float_field_or_default(
            payload,
            "global_percentage_speed_difference",
            default=10.0,
        ),
    )


def parse_traffic_density_request(payload: dict[str, object]) -> TrafficDensityRequest:
    """Parse a JSON-compatible traffic density request."""
    return TrafficDensityRequest(
        vehicle_count=_int_field_or_default(payload, "vehicle_count", default=30),
        traffic_manager_port=_int_field_or_default(payload, "traffic_manager_port", default=8000),
        seed=_int_field_or_default(payload, "seed", default=0),
        safe_filter=_bool_field_or_default(payload, "safe_filter", default=True),
        reset_existing=_bool_field_or_default(payload, "reset_existing", default=False),
        global_distance_to_leading_vehicle=_float_field_or_default(
            payload,
            "global_distance_to_leading_vehicle",
            default=4.0,
        ),
        global_percentage_speed_difference=_float_field_or_default(
            payload,
            "global_percentage_speed_difference",
            default=0.0,
        ),
    )


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
    return AutopilotRequest(
        actor_ids=tuple(_int_value(actor_id, "actor_ids") for actor_id in actor_ids),
        enabled=_bool_field_or_default(payload, "enabled", default=True),
        traffic_manager_port=_int_field_or_default(payload, "traffic_manager_port", default=8000),
    )


def parse_vehicle_behavior_request(payload: dict[str, object]) -> VehicleBehaviorRequest:
    """Parse a JSON-compatible vehicle behavior request."""
    actor_ids = _list_field(payload, "actor_ids")
    return VehicleBehaviorRequest(
        actor_ids=tuple(_int_value(actor_id, "actor_ids") for actor_id in actor_ids),
        profile=_string_field(payload, "profile"),
        traffic_manager_port=_int_field_or_default(payload, "traffic_manager_port", default=8000),
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
    path = tuple(_location(_mapping_value(item, "path")) for item in path_values)
    route = tuple(_string_value(item, "route") for item in route_values)
    return TrafficVehiclePathRequest(
        actor_id=actor_id,
        traffic_manager_port=_int_field_or_default(payload, "traffic_manager_port", default=8000),
        path=path,
        route=route,
        empty_buffer=_bool_field_or_default(payload, "empty_buffer", default=True),
    )


def parse_traffic_manager_request(payload: dict[str, object]) -> TrafficManagerRequest:
    """Parse a JSON-compatible Traffic Manager request."""
    return TrafficManagerRequest(
        traffic_manager_port=_int_field_or_default(payload, "traffic_manager_port", default=8000),
        global_distance_to_leading_vehicle=_optional_float_field(
            payload,
            "global_distance_to_leading_vehicle",
            default=None,
        ),
        global_percentage_speed_difference=_optional_float_field(
            payload,
            "global_percentage_speed_difference",
            default=None,
        ),
        seed=_optional_int_field(payload, "seed"),
        synchronous_mode=_optional_bool_field(payload, "synchronous_mode"),
    )


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
    """Read a required numeric field as a float."""
    value = payload[key]
    if isinstance(value, int | float):
        return float(value)
    msg = f"{key} must be numeric."
    raise TypeError(msg)


def _optional_int_field(payload: Mapping[str, object], key: str) -> int | None:
    """Read an optional integer field."""
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    msg = f"{key} must be an integer or null."
    raise TypeError(msg)


def _int_field_or_default(payload: Mapping[str, object], key: str, *, default: int) -> int:
    """Read an optional integer field with a default."""
    value = _optional_int_field(payload, key)
    if value is None:
        return default
    return value


def _optional_float_field(
    payload: Mapping[str, object],
    key: str,
    *,
    default: float | None,
) -> float | None:
    """Read an optional numeric field as a float."""
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, int | float):
        return float(value)
    msg = f"{key} must be numeric or null."
    raise TypeError(msg)


def _float_field_or_default(payload: Mapping[str, object], key: str, *, default: float) -> float:
    """Read an optional float field with a default."""
    value = _optional_float_field(payload, key, default=None)
    if value is None:
        return default
    return value


def _optional_bool_field(
    payload: Mapping[str, object],
    key: str,
) -> bool | None:
    """Read an optional boolean field."""
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    msg = f"{key} must be a boolean or null."
    raise TypeError(msg)


def _bool_field_or_default(payload: Mapping[str, object], key: str, *, default: bool) -> bool:
    """Read an optional boolean field with a default."""
    value = _optional_bool_field(payload, key)
    if value is None:
        return default
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
    if isinstance(value, int):
        return value
    msg = f"{context} values must be integers."
    raise TypeError(msg)


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
