"""Protocol contracts for the CARLA adapter boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from carla_mcp.models import (
        ActorSnapshot,
        AutopilotRequest,
        BlueprintInfo,
        CameraAttachRequest,
        CaptureInfo,
        DestroyResult,
        HealthReport,
        JsonObject,
        Location,
        RecordingInfo,
        SensorInfo,
        SpawnRequest,
        SpawnResult,
        TrafficManagerRequest,
        TrafficManagerSettings,
        TrafficPopulationRequest,
        TrafficPopulationResult,
        TrafficVehiclePathRequest,
        Transform,
        WorldState,
    )


class HealthAdapter(Protocol):
    """Adapter contract for diagnostic tools."""

    @property
    def host(self) -> str:
        """Return the attempted CARLA host."""

    @property
    def port(self) -> int:
        """Return the attempted CARLA port."""

    def health_check(self) -> HealthReport:
        """Return health details for the connected CARLA server."""


class WorldAdapter(Protocol):
    """Adapter contract for world tools."""

    def get_world_state(self) -> WorldState:
        """Return readable state for the current CARLA world."""

    def list_worlds(self) -> tuple[str, ...]:
        """Return available CARLA world names."""

    def load_world(self, map_name: str) -> WorldState:
        """Load a world by map name."""

    def set_sync_mode(self, *, enabled: bool, fixed_delta_seconds: float | None) -> WorldState:
        """Configure synchronous mode and fixed timestep."""

    def tick(self) -> int:
        """Advance one simulation frame."""


class ActorAdapter(Protocol):
    """Adapter contract for actor tools."""

    def list_blueprints(self, filter_pattern: str) -> tuple[BlueprintInfo, ...]:
        """Return CARLA blueprints matching a filter."""

    def spawn_actor_batch(self, requests: tuple[SpawnRequest, ...]) -> tuple[SpawnResult, ...]:
        """Spawn CARLA actors from a batch of requests."""


class ActorManagementAdapter(Protocol):
    """Adapter contract for actor inspection and cleanup."""

    def list_actors(self, filter_pattern: str) -> tuple[ActorSnapshot, ...]:
        """Return actor snapshots matching a filter."""

    def destroy_actors(self, actor_ids: tuple[int, ...]) -> tuple[DestroyResult, ...]:
        """Destroy explicit actors by ID."""


class RecorderAdapter(Protocol):
    """Adapter contract for recorder tools."""

    def record_episode(self, output_path: Path) -> RecordingInfo:
        """Start the CARLA recorder."""

    def stop_recording(self) -> RecordingInfo:
        """Stop the active recording."""


class SensorAdapter(Protocol):
    """Adapter contract for sensor tools."""

    def attach_camera(self, *, request: CameraAttachRequest) -> SensorInfo:
        """Attach a camera sensor."""

    def capture_sensor_frame(self, *, sensor_id: int, output_path: Path) -> CaptureInfo:
        """Capture one sensor frame to disk."""


class TrafficAdapter(Protocol):
    """Adapter contract for Traffic Manager tools."""

    def populate_traffic(self, *, request: TrafficPopulationRequest) -> TrafficPopulationResult:
        """Spawn vehicles and register them with Traffic Manager."""

    def set_autopilot(self, *, request: AutopilotRequest) -> TrafficPopulationResult:
        """Toggle Traffic Manager autopilot for existing vehicles."""

    def configure_traffic_manager(
        self,
        *,
        request: TrafficManagerRequest,
    ) -> TrafficManagerSettings:
        """Configure Traffic Manager behavior settings."""

    def tune_traffic_vehicle(
        self,
        *,
        actor_id: int,
        traffic_manager_port: int,
        settings: dict[str, object],
    ) -> JsonObject:
        """Apply validated per-vehicle Traffic Manager settings."""

    def set_traffic_vehicle_path(
        self,
        *,
        request: TrafficVehiclePathRequest,
    ) -> JsonObject:
        """Upload one bounded path or route for a vehicle."""


class ExperimentAdapter(Protocol):
    """Adapter contract for script-only CARLA experiment capabilities."""

    def attach_sensor(
        self,
        *,
        blueprint_id: str,
        transform: Transform,
        attributes: dict[str, str],
        parent_actor_id: int | None,
    ) -> SensorInfo:
        """Attach any CARLA sensor blueprint to an actor."""

    def read_sensor_stream(
        self,
        *,
        sensor_id: int,
        frame_count: int,
        output_dir: Path | None,
    ) -> JsonObject:
        """Read several frames from a sensor and optionally persist files."""

    def detach_sensor(self, sensor_id: int) -> JsonObject:
        """Stop and destroy a sensor actor."""

    def get_spawn_points(self) -> JsonObject:
        """Return legal vehicle spawn transforms."""

    def get_waypoint(
        self,
        *,
        location: Location,
        lane_type: str,
        project_to_road: bool,
    ) -> JsonObject:
        """Return map waypoint metadata for a location."""

    def generate_route(
        self,
        *,
        start: Location,
        end: Location,
        step_meters: float,
        max_steps: int,
    ) -> JsonObject:
        """Generate a waypoint route."""

    def get_topology(self, *, max_segments: int) -> JsonObject:
        """Return a compact road topology graph."""

    def get_landmarks(
        self,
        *,
        max_count: int,
        landmark_type: str | None,
        landmark_id: str | None,
    ) -> JsonObject:
        """Return bounded map landmarks through official query variants."""

    def configure_actor_physics(
        self,
        *,
        actor_id: int,
        simulate_physics: bool | None,
        gravity: bool | None,
    ) -> JsonObject:
        """Toggle actor physics and gravity when supported."""

    def apply_actor_physics(
        self,
        *,
        actor_id: int,
        action: str,
        vector: Location,
    ) -> JsonObject:
        """Apply one vector-based actor physics operation."""

    def get_vehicle_physics(self, actor_id: int) -> JsonObject:
        """Return a bounded vehicle physics payload."""

    def update_vehicle_physics(
        self,
        *,
        actor_id: int,
        changes: dict[str, object],
    ) -> JsonObject:
        """Update supported vehicle physics fields."""

    def get_environment_objects(
        self,
        *,
        label: str,
        max_count: int,
        include_level_bounds: bool,
    ) -> JsonObject:
        """Return bounded static objects and optional semantic level bounds."""

    def enable_environment_objects(
        self,
        *,
        object_ids: tuple[int, ...],
        enabled: bool,
    ) -> JsonObject:
        """Enable or disable explicit environment object IDs."""

    def set_map_layer(self, *, layer: str, loaded: bool) -> JsonObject:
        """Load or unload one runtime map layer."""

    def generate_opendrive_world(
        self,
        *,
        opendrive: str,
        parameters: dict[str, object],
        reset_settings: bool,
    ) -> JsonObject:
        """Generate a world from bounded OpenDRIVE text."""

    def apply_vehicle_control(self, *, actor_id: int, control: dict[str, object]) -> JsonObject:
        """Apply direct vehicle control."""

    def get_vehicle_telemetry(self, actor_id: int) -> JsonObject:
        """Return vehicle state useful for closed-loop control."""

    def set_actor_transform(self, *, actor_id: int, transform: Transform) -> JsonObject:
        """Teleport an actor to a transform."""

    def set_vehicle_lights(self, *, actor_id: int, state: str | int) -> JsonObject:
        """Set vehicle light state."""

    def set_target_velocity(self, *, actor_id: int, velocity: Location) -> JsonObject:
        """Set actor target velocity."""

    def spawn_walkers(self, *, count: int, speed: float, seed: int | None) -> JsonObject:
        """Spawn pedestrians and AI controllers."""

    def set_walker_destination(self, *, controller_id: int, location: Location) -> JsonObject:
        """Send a walker controller to a destination."""

    def apply_walker_control(
        self,
        *,
        actor_id: int,
        direction: Location,
        speed: float,
    ) -> JsonObject:
        """Apply manual walker control."""

    def freeze_traffic_lights(self, *, enabled: bool) -> JsonObject:
        """Freeze or unfreeze all traffic lights."""

    def set_traffic_light_state(self, *, actor_id: int, state: str) -> JsonObject:
        """Set one traffic light state."""

    def set_spectator(self, transform: Transform) -> JsonObject:
        """Move the spectator camera."""

    def watch_actor(
        self,
        *,
        actor_id: int,
        seconds: float,
        distance: float,
        height: float,
    ) -> JsonObject:
        """Follow one actor with a bounded simulator-tick chase camera."""

    def save_screenshot(self, *, output_path: Path, attributes: dict[str, str]) -> JsonObject:
        """Capture a temporary RGB camera frame from the spectator viewpoint."""

    def get_weather(self) -> JsonObject:
        """Return current weather when supported."""

    def set_weather(self, parameters: dict[str, float]) -> JsonObject:
        """Set weather parameters when supported."""

    def replay_recording(
        self,
        *,
        path: Path,
        start: float,
        duration: float,
        follow_id: int,
        replay_sensors: bool,
    ) -> JsonObject:
        """Replay a CARLA recorder file."""

    def query_recording_collisions(
        self,
        *,
        path: Path,
        actor_type: str,
        other_type: str,
    ) -> JsonObject:
        """Return recorder collision report text."""

    def query_recording_actors_blocked(
        self,
        *,
        path: Path,
        min_time: float,
        min_distance: float,
    ) -> JsonObject:
        """Return recorder blocked-actor report text."""

    def reload_world(self, *, reset_settings: bool) -> JsonObject:
        """Reload the current world."""

    def apply_batch(self, commands: list[dict[str, object]]) -> JsonObject:
        """Apply a small JSON-compatible command batch."""

    def list_capabilities(self) -> JsonObject:
        """Probe available CARLA capabilities."""


class CarlaAdapter(
    HealthAdapter,
    WorldAdapter,
    ActorAdapter,
    ActorManagementAdapter,
    RecorderAdapter,
    SensorAdapter,
    TrafficAdapter,
    ExperimentAdapter,
    Protocol,
):
    """Complete adapter contract implemented by the CARLA Python adapter."""

    @property
    def host(self) -> str:
        """Return the configured CARLA host."""

    @property
    def port(self) -> int:
        """Return the configured CARLA RPC port."""

    @property
    def timeout(self) -> float:
        """Return the configured CARLA timeout in seconds."""
