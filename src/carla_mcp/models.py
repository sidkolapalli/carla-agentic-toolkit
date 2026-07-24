"""Typed public models for CARLA MCP tool results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

type JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class ActorCounts:
    """Counts for major actor groups in a CARLA world."""

    vehicles: int = 0
    walkers: int = 0
    sensors: int = 0
    traffic: int = 0

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WorldSettings:
    """Relevant CARLA world timing and rendering settings."""

    synchronous_mode: bool
    fixed_delta_seconds: float | None
    no_rendering_mode: bool

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Health details for a CARLA client/server connection."""

    connected: bool
    client_version: str | None
    server_version: str | None
    current_map: str | None
    settings: WorldSettings
    actor_counts: ActorCounts
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "connected": self.connected,
            "client_version": self.client_version,
            "server_version": self.server_version,
            "current_map": self.current_map,
            "settings": self.settings.to_dict(),
            "actor_counts": self.actor_counts.to_dict(),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class WorldState:
    """Readable state for the currently loaded CARLA world."""

    current_map: str | None
    settings: WorldSettings
    actor_counts: ActorCounts
    frame: int | None
    warnings: tuple[str, ...] = ()

    def with_map(self, map_name: str) -> WorldState:
        """Return this state with a different loaded map."""
        return WorldState(
            current_map=map_name,
            settings=self.settings,
            actor_counts=self.actor_counts,
            frame=self.frame,
            warnings=self.warnings,
        )

    def with_settings(self, settings: WorldSettings) -> WorldState:
        """Return this state with updated world settings."""
        return WorldState(
            current_map=self.current_map,
            settings=settings,
            actor_counts=self.actor_counts,
            frame=self.frame,
            warnings=self.warnings,
        )

    def with_frame(self, frame: int | None) -> WorldState:
        """Return this state with an updated frame."""
        return WorldState(
            current_map=self.current_map,
            settings=self.settings,
            actor_counts=self.actor_counts,
            frame=frame,
            warnings=self.warnings,
        )

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "current_map": self.current_map,
            "settings": self.settings.to_dict(),
            "actor_counts": self.actor_counts.to_dict(),
            "frame": self.frame,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Internal tool result that maps cleanly to MCP structured content."""

    structured_content: JsonObject
    is_error: bool = False
    message: str | None = None

    @classmethod
    def ok(cls, structured_content: JsonObject, message: str | None = None) -> ToolResult:
        """Create a successful tool result."""
        return cls(structured_content=structured_content, is_error=False, message=message)

    @classmethod
    def error(cls, structured_content: JsonObject, message: str | None = None) -> ToolResult:
        """Create a tool execution error result."""
        return cls(structured_content=structured_content, is_error=True, message=message)


@dataclass(frozen=True, slots=True)
class BlueprintAttribute:
    """Summary of a CARLA blueprint attribute."""

    attribute_id: str
    is_modifiable: bool
    recommended_values: tuple[str, ...] = ()

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "attribute_id": self.attribute_id,
            "is_modifiable": self.is_modifiable,
            "recommended_values": list(self.recommended_values),
        }


@dataclass(frozen=True, slots=True)
class BlueprintInfo:
    """Summary of a CARLA blueprint."""

    blueprint_id: str
    tags: tuple[str, ...]
    attributes: tuple[BlueprintAttribute, ...]

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "blueprint_id": self.blueprint_id,
            "tags": list(self.tags),
            "attributes": [attribute.to_dict() for attribute in self.attributes],
        }


@dataclass(frozen=True, slots=True)
class Location:
    """CARLA location coordinates."""

    x: float
    y: float
    z: float

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Rotation:
    """CARLA rotation angles in degrees."""

    pitch: float
    yaw: float
    roll: float

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Transform:
    """CARLA actor transform."""

    location: Location
    rotation: Rotation

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "location": self.location.to_dict(),
            "rotation": self.rotation.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class SpawnRequest:
    """Request to spawn one CARLA actor."""

    blueprint_id: str
    transform: Transform
    attributes: dict[str, str]

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "blueprint_id": self.blueprint_id,
            "transform": self.transform.to_dict(),
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class SpawnResult:
    """Result for one CARLA actor spawn request."""

    request_index: int
    actor_id: int | None
    error: str | None

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "request_index": self.request_index,
            "actor_id": self.actor_id,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class ActorSnapshot:
    """Readable snapshot for one CARLA actor."""

    actor_id: int
    type_id: str
    role_name: str | None
    transform: Transform
    speed_mps: float | None
    traffic_light_state: str | None

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "actor_id": self.actor_id,
            "type_id": self.type_id,
            "role_name": self.role_name,
            "transform": self.transform.to_dict(),
            "speed_mps": self.speed_mps,
            "traffic_light_state": self.traffic_light_state,
        }


@dataclass(frozen=True, slots=True)
class DestroyResult:
    """Result for one actor destroy request."""

    actor_id: int
    destroyed: bool
    error: str | None

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "actor_id": self.actor_id,
            "destroyed": self.destroyed,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class TrafficPopulationRequest:
    """Request to populate a CARLA map with normal traffic."""

    vehicle_count: int = 30
    traffic_manager_port: int = 8000
    seed: int = 0
    safe_filter: bool = True
    global_distance_to_leading_vehicle: float = 2.5
    global_percentage_speed_difference: float = 10.0


@dataclass(frozen=True, slots=True)
class TrafficDensityRequest:
    """Request to converge traffic to a target vehicle count."""

    vehicle_count: int
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


@dataclass(frozen=True, slots=True)
class TrafficManagerRequest:
    """Request to configure CARLA Traffic Manager behavior."""

    traffic_manager_port: int = 8000
    global_distance_to_leading_vehicle: float | None = None
    global_percentage_speed_difference: float | None = None
    seed: int | None = None
    synchronous_mode: bool | None = None


@dataclass(frozen=True, slots=True)
class VehicleBehaviorRequest:
    """Request to apply a named Traffic Manager behavior profile."""

    actor_ids: tuple[int, ...]
    profile: str
    traffic_manager_port: int = 8000


@dataclass(frozen=True, slots=True)
class TrafficManagerSettings:
    """Traffic Manager settings applied through CARLA."""

    traffic_manager_port: int
    global_distance_to_leading_vehicle: float | None
    global_percentage_speed_difference: float | None
    seed: int | None
    safe_filter: bool | None
    synchronous_mode: bool | None = None

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "traffic_manager_port": self.traffic_manager_port,
            "global_distance_to_leading_vehicle": self.global_distance_to_leading_vehicle,
            "global_percentage_speed_difference": self.global_percentage_speed_difference,
            "seed": self.seed,
            "safe_filter": self.safe_filter,
            "synchronous_mode": self.synchronous_mode,
        }


@dataclass(frozen=True, slots=True)
class TrafficPopulationResult:
    """Result from populating or registering vehicles with Traffic Manager."""

    requested_vehicle_count: int
    spawned_vehicle_count: int
    actor_ids: tuple[int, ...]
    failed_spawns: tuple[SpawnResult, ...]
    settings: TrafficManagerSettings
    world_state: WorldState

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "requested_vehicle_count": self.requested_vehicle_count,
            "spawned_vehicle_count": self.spawned_vehicle_count,
            "actor_ids": list(self.actor_ids),
            "failed_spawns": [failure.to_dict() for failure in self.failed_spawns],
            "settings": self.settings.to_dict(),
            "world_state": self.world_state.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class VehicleBehaviorResult:
    """Result from applying a behavior profile to vehicles."""

    actor_ids: tuple[int, ...]
    profile: str
    applied_settings: dict[str, float | bool]
    failed_applications: tuple[DestroyResult, ...]

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "actor_ids": list(self.actor_ids),
            "profile": self.profile,
            "applied_settings": dict(self.applied_settings),
            "failed_applications": [failure.to_dict() for failure in self.failed_applications],
        }


@dataclass(frozen=True, slots=True)
class TrafficControllerStartRequest:
    """Request to start a persistent Traffic Manager controller."""

    density: TrafficDensityRequest
    host: str = "127.0.0.1"
    port: int = 2000
    timeout_seconds: float = 10.0


@dataclass(frozen=True, slots=True)
class TrafficControllerStatus:
    """Status for the persistent Traffic Manager controller."""

    active: bool
    host: str
    port: int
    traffic_manager_port: int
    target_vehicle_count: int
    vehicle_count: int
    moving_vehicle_count: int
    last_error: str | None

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "active": self.active,
            "host": self.host,
            "port": self.port,
            "traffic_manager_port": self.traffic_manager_port,
            "target_vehicle_count": self.target_vehicle_count,
            "vehicle_count": self.vehicle_count,
            "moving_vehicle_count": self.moving_vehicle_count,
            "last_error": self.last_error,
        }


@dataclass(frozen=True, slots=True)
class RecordingInfo:
    """CARLA recorder output state."""

    recording_id: str
    path: Path
    active: bool

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "recording_id": self.recording_id,
            "path": str(self.path),
            "active": self.active,
        }


@dataclass(frozen=True, slots=True)
class SensorInfo:
    """CARLA sensor metadata."""

    sensor_id: int
    blueprint_id: str
    parent_actor_id: int | None
    attributes: dict[str, str]
    transform: Transform

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "sensor_id": self.sensor_id,
            "blueprint_id": self.blueprint_id,
            "parent_actor_id": self.parent_actor_id,
            "attributes": dict(self.attributes),
            "transform": self.transform.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CameraAttachRequest:
    """Request to attach a CARLA camera sensor."""

    blueprint_id: str
    transform: Transform
    attributes: dict[str, str]
    parent_actor_id: int | None


@dataclass(frozen=True, slots=True)
class CaptureInfo:
    """Metadata for one captured sensor frame."""

    capture_id: str
    sensor_id: int
    path: Path
    frame: int | None
    mime_type: str

    def with_path(self, path: Path) -> CaptureInfo:
        """Return this capture with a different file path."""
        return CaptureInfo(
            capture_id=self.capture_id,
            sensor_id=self.sensor_id,
            path=path,
            frame=self.frame,
            mime_type=self.mime_type,
        )

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible representation."""
        return {
            "capture_id": self.capture_id,
            "sensor_id": self.sensor_id,
            "path": str(self.path),
            "frame": self.frame,
            "mime_type": self.mime_type,
        }
