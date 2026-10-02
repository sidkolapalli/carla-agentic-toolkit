"""Typed public models for CARLA Agentic Toolkit tool results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

type JsonObject = dict[str, object]


def _jsonable(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return _jsonable_mapping(cast("dict[object, object]", value))
    if isinstance(value, list | tuple):
        return _jsonable_sequence(cast("list[object] | tuple[object, ...]", value))
    return value


def _jsonable_mapping(value: dict[object, object]) -> dict[str, object]:
    return {str(key): _jsonable(item) for key, item in value.items()}


def _jsonable_sequence(value: list[object] | tuple[object, ...]) -> list[object]:
    return [_jsonable(item) for item in value]


class _JsonModel:
    __slots__ = ()

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return cast("JsonObject", _jsonable(asdict(cast("Any", self))))


@dataclass(frozen=True, slots=True)
class ActorCounts(_JsonModel):
    """Counts for major actor groups in a CARLA world."""

    vehicles: int = 0
    walkers: int = 0
    sensors: int = 0
    traffic: int = 0


@dataclass(frozen=True, slots=True)
class WorldSettings(_JsonModel):
    """Relevant CARLA world timing and rendering settings."""

    synchronous_mode: bool
    fixed_delta_seconds: float | None
    no_rendering_mode: bool


@dataclass(frozen=True, slots=True)
class HealthReport(_JsonModel):
    """Health details for a CARLA client/server connection."""

    connected: bool
    client_version: str | None
    server_version: str | None
    current_map: str | None
    settings: WorldSettings
    actor_counts: ActorCounts
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorldState(_JsonModel):
    """Readable state for the currently loaded CARLA world."""

    current_map: str | None
    settings: WorldSettings
    actor_counts: ActorCounts
    frame: int | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BlueprintAttribute(_JsonModel):
    """Summary of a CARLA blueprint attribute."""

    attribute_id: str
    is_modifiable: bool
    recommended_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BlueprintInfo(_JsonModel):
    """Summary of a CARLA blueprint."""

    blueprint_id: str
    tags: tuple[str, ...]
    attributes: tuple[BlueprintAttribute, ...]


@dataclass(frozen=True, slots=True)
class Location(_JsonModel):
    """CARLA location coordinates."""

    x: float
    y: float
    z: float


@dataclass(frozen=True, slots=True)
class Rotation(_JsonModel):
    """CARLA rotation angles in degrees."""

    pitch: float
    yaw: float
    roll: float


@dataclass(frozen=True, slots=True)
class Transform(_JsonModel):
    """CARLA actor transform."""

    location: Location
    rotation: Rotation


@dataclass(frozen=True, slots=True)
class SpawnRequest(_JsonModel):
    """Request to spawn one CARLA actor."""

    blueprint_id: str
    transform: Transform
    attributes: dict[str, str]


@dataclass(frozen=True, slots=True)
class SpawnResult(_JsonModel):
    """Result for one CARLA actor spawn request."""

    request_index: int
    actor_id: int | None
    error: str | None


@dataclass(frozen=True, slots=True)
class ActorSnapshot(_JsonModel):
    """Readable snapshot for one CARLA actor."""

    actor_id: int
    type_id: str
    role_name: str | None
    transform: Transform
    speed_mps: float | None
    traffic_light_state: str | None
    semantic_tags: tuple[int, ...] = ()
    semantic_tags_truncated: bool = False


@dataclass(frozen=True, slots=True)
class DestroyResult(_JsonModel):
    """Result for one actor destroy request."""

    actor_id: int
    destroyed: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class TrafficPopulationRequest:
    """Request to populate a CARLA map with normal traffic."""

    vehicle_count: int = 30
    traffic_manager_port: int = 8000
    seed: int = 0
    safe_filter: bool = True
    global_distance_to_leading_vehicle: float = 2.5
    global_percentage_speed_difference: float = 10.0
    advance_world: bool = True


@dataclass(frozen=True, slots=True)
class TrafficDensityRequest:
    """Request to converge traffic to a target vehicle count."""

    vehicle_count: int = 30
    traffic_manager_port: int = 8000
    seed: int = 0
    safe_filter: bool = True
    reset_existing: bool = False
    global_distance_to_leading_vehicle: float = 4.0
    global_percentage_speed_difference: float = 0.0


@dataclass(frozen=True, slots=True)
class AutopilotRequest:
    """Request to toggle Traffic Manager autopilot for existing actors."""

    actor_ids: tuple[int, ...]
    enabled: bool = True
    traffic_manager_port: int = 8000
    advance_world: bool = True


@dataclass(frozen=True, slots=True)
class TrafficManagerRequest:
    """Request to configure CARLA Traffic Manager behavior."""

    traffic_manager_port: int = 8000
    global_distance_to_leading_vehicle: float | None = None
    global_percentage_speed_difference: float | None = None
    seed: int | None = None
    synchronous_mode: bool | None = None


@dataclass(frozen=True, slots=True)
class TrafficVehiclePathRequest:
    """One bounded path or route upload for a Traffic Manager vehicle."""

    actor_id: int
    traffic_manager_port: int = 8000
    path: tuple[Location, ...] = ()
    route: tuple[str, ...] = ()
    empty_buffer: bool = True


@dataclass(frozen=True, slots=True)
class VehicleBehaviorRequest:
    """Request to apply a named Traffic Manager behavior profile."""

    actor_ids: tuple[int, ...]
    profile: str
    traffic_manager_port: int = 8000


@dataclass(frozen=True, slots=True)
class TrafficManagerSettings(_JsonModel):
    """Traffic Manager settings applied through CARLA."""

    traffic_manager_port: int
    global_distance_to_leading_vehicle: float | None
    global_percentage_speed_difference: float | None
    seed: int | None
    safe_filter: bool | None
    synchronous_mode: bool | None = None


@dataclass(frozen=True, slots=True)
class TrafficPopulationResult(_JsonModel):
    """Result from populating or registering vehicles with Traffic Manager."""

    requested_vehicle_count: int
    spawned_vehicle_count: int
    actor_ids: tuple[int, ...]
    failed_spawns: tuple[SpawnResult, ...]
    settings: TrafficManagerSettings
    world_state: WorldState


@dataclass(frozen=True, slots=True)
class VehicleBehaviorResult(_JsonModel):
    """Result from applying a behavior profile to vehicles."""

    actor_ids: tuple[int, ...]
    profile: str
    applied_settings: dict[str, float | bool]
    failed_applications: tuple[DestroyResult, ...]


@dataclass(frozen=True, slots=True)
class TrafficControllerStartRequest:
    """Request to start a persistent Traffic Manager controller."""

    density: TrafficDensityRequest
    host: str = "127.0.0.1"
    port: int = 2000
    timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class TrafficControllerStatus(_JsonModel):
    """Status for the persistent Traffic Manager controller."""

    active: bool
    host: str
    port: int
    traffic_manager_port: int
    target_vehicle_count: int
    vehicle_count: int
    moving_vehicle_count: int
    last_error: str | None
    stopping: bool = False
    generation: int = 0
    desired_revision: int = 0
    applied_revision: int | None = None
    applied_target_vehicle_count: int | None = None
    owned_actor_ids: tuple[int, ...] = ()
    adopted_actor_ids: tuple[int, ...] = ()
    error_type: str | None = None
    conflict: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class RecordingInfo(_JsonModel):
    """CARLA recorder output state."""

    recording_id: str
    path: Path
    active: bool


@dataclass(frozen=True, slots=True)
class SensorInfo(_JsonModel):
    """CARLA sensor metadata."""

    sensor_id: int
    blueprint_id: str
    parent_actor_id: int | None
    attributes: dict[str, str]
    transform: Transform


@dataclass(frozen=True, slots=True)
class CameraAttachRequest:
    """Request to attach a CARLA camera sensor."""

    blueprint_id: str
    transform: Transform
    attributes: dict[str, str]
    parent_actor_id: int | None


@dataclass(frozen=True, slots=True)
class CaptureInfo(_JsonModel):
    """Metadata for one captured sensor frame."""

    capture_id: str
    sensor_id: int
    path: Path
    frame: int | None
    mime_type: str
