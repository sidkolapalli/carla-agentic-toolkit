"""CARLA Python API adapter boundary."""

from __future__ import annotations

import os
from importlib import import_module
from pathlib import Path
from queue import Empty, Queue
from typing import cast

from carla_mcp.actor_runtime import actor_snapshot, destroy_actor
from carla_mcp.adapter_experiments import PythonCarlaExperimentMixin
from carla_mcp.adapter_protocols import (
    ActorAdapter,
    ActorManagementAdapter,
    CarlaAdapter,
    ExperimentAdapter,
    HealthAdapter,
    RecorderAdapter,
    SensorAdapter,
    TrafficAdapter,
    WorldAdapter,
)
from carla_mcp.carla_protocols import (
    CarlaBlueprint,
    CarlaBlueprintAttribute,
    CarlaClient,
    CarlaClientFactory,
    CarlaImage,
    CarlaSensor,
    CarlaWorld,
)
from carla_mcp.errors import CarlaAdapterError
from carla_mcp.experiment_common import (
    actor_counts,
    carla_transform,
    frame,
    map_name,
    require_sensor,
    sensor_actor,
    world_settings,
    world_state,
)
from carla_mcp.models import (
    ActorSnapshot,
    AutopilotRequest,
    BlueprintAttribute,
    BlueprintInfo,
    CameraAttachRequest,
    CaptureInfo,
    DestroyResult,
    HealthReport,
    RecordingInfo,
    SensorInfo,
    SpawnRequest,
    SpawnResult,
    TrafficManagerRequest,
    TrafficManagerSettings,
    TrafficPopulationRequest,
    TrafficPopulationResult,
    Transform,
    WorldState,
)
from carla_mcp.traffic_runtime import (
    advance_world_once,
    populate_traffic_actors,
    set_actor_autopilot,
    traffic_manager,
)
from carla_mcp.traffic_runtime import (
    configure_traffic_manager as configure_traffic_manager_runtime,
)

__all__ = [
    "ActorAdapter",
    "ActorManagementAdapter",
    "CarlaAdapter",
    "ExperimentAdapter",
    "HealthAdapter",
    "PythonCarlaAdapter",
    "RecorderAdapter",
    "SensorAdapter",
    "TrafficAdapter",
    "WorldAdapter",
]


class PythonCarlaAdapter(PythonCarlaExperimentMixin):
    """Adapter backed by CARLA's official Python API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2000, timeout: float = 10.0) -> None:
        """Create an adapter for a CARLA server."""
        self._host = host
        self._port = port
        self._timeout = timeout
        self._recording: RecordingInfo | None = None

    @property
    def host(self) -> str:
        """Return the configured CARLA host."""
        return self._host

    @property
    def port(self) -> int:
        """Return the configured CARLA RPC port."""
        return self._port

    @property
    def timeout(self) -> float:
        """Return the configured CARLA timeout in seconds."""
        return self._timeout

    def health_check(self) -> HealthReport:
        """Return health details for the connected CARLA server."""
        client = self._client()
        world = self._world(client)
        return HealthReport(
            connected=True,
            client_version=_safe_call(client, "get_client_version"),
            server_version=_safe_call(client, "get_server_version"),
            current_map=map_name(world),
            settings=world_settings(world),
            actor_counts=actor_counts(world),
            warnings=(),
        )

    def get_world_state(self) -> WorldState:
        """Return readable state for the current CARLA world."""
        world = self._world(self._client())
        return WorldState(
            current_map=map_name(world),
            settings=world_settings(world),
            actor_counts=actor_counts(world),
            frame=frame(world),
            warnings=(),
        )

    def list_worlds(self) -> tuple[str, ...]:
        """Return available CARLA world names."""
        client = self._client()
        try:
            worlds = client.get_available_maps()
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        return tuple(str(world) for world in worlds)

    def load_world(self, map_name: str) -> WorldState:
        """Load a CARLA world by map name."""
        client = self._client()
        try:
            world = client.load_world(map_name)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        return world_state(world)

    def set_sync_mode(self, *, enabled: bool, fixed_delta_seconds: float | None) -> WorldState:
        """Configure synchronous mode and fixed timestep."""
        world = self._world(self._client())
        settings = world.get_settings()
        settings.synchronous_mode = enabled
        settings.fixed_delta_seconds = fixed_delta_seconds
        try:
            world.apply_settings(settings)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        return world_state(world)

    def tick(self) -> int:
        """Advance the CARLA world by one frame."""
        world = self._world(self._client())
        try:
            return int(world.tick())
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc

    def list_blueprints(self, filter_pattern: str) -> tuple[BlueprintInfo, ...]:
        """Return CARLA blueprints matching a filter."""
        world = self._world(self._client())
        try:
            blueprints = world.get_blueprint_library().filter(filter_pattern)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        return tuple(
            sorted(
                (_blueprint_info(bp) for bp in blueprints),
                key=lambda bp: bp.blueprint_id,
            )
        )

    def spawn_actor_batch(self, requests: tuple[SpawnRequest, ...]) -> tuple[SpawnResult, ...]:
        """Spawn CARLA actors from a batch of requests."""
        world = self._world(self._client())
        return tuple(_spawn_actor(world, index, request) for index, request in enumerate(requests))

    def list_actors(self, filter_pattern: str) -> tuple[ActorSnapshot, ...]:
        """Return actor snapshots matching a CARLA wildcard filter."""
        world = self._world(self._client())
        try:
            actors = world.get_actors().filter(filter_pattern)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        return tuple(actor_snapshot(actor) for actor in actors)

    def destroy_actors(self, actor_ids: tuple[int, ...]) -> tuple[DestroyResult, ...]:
        """Destroy explicit CARLA actors by ID."""
        world = self._world(self._client())
        return tuple(destroy_actor(world, actor_id) for actor_id in actor_ids)

    def populate_traffic(
        self,
        *,
        request: TrafficPopulationRequest,
    ) -> TrafficPopulationResult:
        """Spawn vehicles and register them with Traffic Manager autopilot."""
        if request.vehicle_count < 0:
            msg = "vehicle_count must be greater than or equal to zero."
            raise CarlaAdapterError(msg)
        client = self._client()
        world = self._world(client)
        settings = self.configure_traffic_manager(
            request=TrafficManagerRequest(
                traffic_manager_port=request.traffic_manager_port,
                global_distance_to_leading_vehicle=request.global_distance_to_leading_vehicle,
                global_percentage_speed_difference=request.global_percentage_speed_difference,
                seed=request.seed,
                synchronous_mode=None,
            )
        )
        traffic_manager_instance = traffic_manager(client, request.traffic_manager_port)
        actor_ids, failures = populate_traffic_actors(
            world=world,
            traffic_manager_instance=traffic_manager_instance,
            request=request,
        )
        advance_world_once(world)
        return TrafficPopulationResult(
            requested_vehicle_count=request.vehicle_count,
            spawned_vehicle_count=len(actor_ids),
            actor_ids=tuple(actor_ids),
            failed_spawns=tuple(failures),
            settings=TrafficManagerSettings(
                traffic_manager_port=settings.traffic_manager_port,
                global_distance_to_leading_vehicle=settings.global_distance_to_leading_vehicle,
                global_percentage_speed_difference=settings.global_percentage_speed_difference,
                seed=settings.seed,
                safe_filter=request.safe_filter,
                synchronous_mode=settings.synchronous_mode,
            ),
            world_state=world_state(world),
        )

    def set_autopilot(
        self,
        *,
        request: AutopilotRequest,
    ) -> TrafficPopulationResult:
        """Toggle Traffic Manager autopilot for existing vehicles."""
        client = self._client()
        world = self._world(client)
        traffic_manager_instance = traffic_manager(client, request.traffic_manager_port)
        changed_ids, failures = set_actor_autopilot(
            world=world,
            request=request,
            traffic_manager_port=traffic_manager_instance.get_port(),
        )
        advance_world_once(world)
        return TrafficPopulationResult(
            requested_vehicle_count=len(request.actor_ids),
            spawned_vehicle_count=len(changed_ids),
            actor_ids=tuple(changed_ids),
            failed_spawns=tuple(failures),
            settings=TrafficManagerSettings(
                traffic_manager_port=traffic_manager_instance.get_port(),
                global_distance_to_leading_vehicle=None,
                global_percentage_speed_difference=None,
                seed=None,
                safe_filter=None,
            ),
            world_state=world_state(world),
        )

    def configure_traffic_manager(
        self,
        *,
        request: TrafficManagerRequest,
    ) -> TrafficManagerSettings:
        """Configure Traffic Manager behavior settings."""
        traffic_manager_instance = traffic_manager(self._client(), request.traffic_manager_port)
        return configure_traffic_manager_runtime(traffic_manager_instance, request)

    def record_episode(self, output_path: Path) -> RecordingInfo:
        """Start recording an episode at the simulator-side path."""
        client = self._client()
        requested_path = _server_recorder_path(output_path)
        try:
            accepted_path = client.start_recorder(requested_path)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        if not accepted_path:
            msg = f"CARLA did not open recorder path: {requested_path}"
            raise CarlaAdapterError(msg)
        path = Path(accepted_path)
        self._recording = RecordingInfo(recording_id=path.stem, path=path, active=True)
        return self._recording

    def stop_recording(self) -> RecordingInfo:
        """Stop the active recording."""
        client = self._client()
        try:
            client.stop_recorder()
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        if self._recording is None:
            return RecordingInfo(recording_id="latest", path=Path("latest.log"), active=False)
        stopped = RecordingInfo(
            recording_id=self._recording.recording_id,
            path=self._recording.path,
            active=False,
        )
        self._recording = None
        return stopped

    def attach_camera(
        self,
        *,
        request: CameraAttachRequest,
    ) -> SensorInfo:
        """Attach a camera sensor."""
        world = self._world(self._client())
        spawn_request = SpawnRequest(
            blueprint_id=request.blueprint_id,
            transform=request.transform,
            attributes=request.attributes,
        )
        blueprint = _configured_blueprint(world, spawn_request)
        parent = _parent_actor(world, request.parent_actor_id)
        sensor = _spawn_sensor(world, blueprint, request.transform, parent)
        return SensorInfo(
            sensor_id=sensor.id,
            blueprint_id=request.blueprint_id,
            parent_actor_id=request.parent_actor_id,
            attributes=dict(request.attributes),
            transform=request.transform,
        )

    def capture_sensor_frame(self, *, sensor_id: int, output_path: Path) -> CaptureInfo:
        """Capture one sensor frame to disk."""
        sensor = sensor_actor(self._world(self._client()), sensor_id)
        image = _capture_image(sensor)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save_to_disk(str(output_path))
        return CaptureInfo(
            capture_id=f"capture-{sensor_id:06d}",
            sensor_id=sensor_id,
            path=output_path,
            frame=int(image.frame),
            mime_type=_mime_type(output_path),
        )

    def _client(self) -> CarlaClient:
        """Create and configure a CARLA client."""
        try:
            client_factory = _carla_client_factory()
        except (ImportError, TypeError) as exc:  # pragma: no cover - optional CARLA package
            msg = "CARLA Python API is not importable."
            raise CarlaAdapterError(msg) from exc
        try:
            client_candidate = client_factory(self._host, self._port)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        client = _require_carla_client(client_candidate)
        try:
            client.set_timeout(self._timeout)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        return client

    @staticmethod
    def _world(client: CarlaClient) -> CarlaWorld:
        """Fetch the current CARLA world."""
        try:
            return client.get_world()
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc


def _server_recorder_path(output_path: Path) -> str:
    """Resolve a relative recording under an optional simulator-side directory."""
    path = output_path.as_posix()
    directory = os.environ.get("CARLA_MCP_RECORDER_DIR")
    if directory and not output_path.is_absolute() and not (len(path) > 1 and path[1] == ":"):
        base = directory.rstrip("/\\")
        return f"{base}/{path}"
    return path


def _carla_client_factory() -> CarlaClientFactory:
    """Import the optional CARLA Python module and return its Client factory."""
    module = import_module("carla")
    client_factory = getattr(module, "Client", None)
    if not callable(client_factory):
        msg = "Imported carla module does not expose Client."
        raise TypeError(msg)
    return cast("CarlaClientFactory", client_factory)


def _require_carla_client(candidate: object) -> CarlaClient:
    """Validate that a dynamic CARLA client exposes the expected API."""
    if isinstance(candidate, CarlaClient):
        return candidate
    msg = "CARLA Client object does not expose the expected API."
    raise CarlaAdapterError(msg)


def _safe_call(target: object, method_name: str) -> str | None:
    """Return a method result as text, or None when unavailable."""
    method = getattr(target, method_name, None)
    if not callable(method):
        return None
    try:
        value = method()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return str(value)


def _blueprint_info(blueprint: CarlaBlueprint) -> BlueprintInfo:
    """Convert a CARLA blueprint into a stable summary."""
    return BlueprintInfo(
        blueprint_id=str(blueprint.id),
        tags=tuple(str(tag) for tag in blueprint.tags),
        attributes=tuple(_blueprint_attribute(attribute) for attribute in blueprint),
    )


def _blueprint_attribute(attribute: object) -> BlueprintAttribute:
    """Convert a CARLA blueprint attribute into a stable summary."""
    typed_attribute = _require_blueprint_attribute(attribute)
    return BlueprintAttribute(
        attribute_id=str(typed_attribute.id),
        is_modifiable=bool(typed_attribute.is_modifiable),
        recommended_values=tuple(str(value) for value in typed_attribute.recommended_values),
    )


def _require_blueprint_attribute(candidate: object) -> CarlaBlueprintAttribute:
    """Validate a dynamic CARLA blueprint attribute."""
    missing_api = not all(
        hasattr(candidate, attribute_name)
        for attribute_name in ("id", "is_modifiable", "recommended_values")
    )
    if missing_api:
        msg = "CARLA blueprint attribute does not expose the expected API."
        raise CarlaAdapterError(msg)
    return cast("CarlaBlueprintAttribute", candidate)


def _spawn_actor(world: CarlaWorld, index: int, request: SpawnRequest) -> SpawnResult:
    """Spawn one actor and convert CARLA failures into a structured result."""
    try:
        blueprint = _configured_blueprint(world, request)
        actor = world.spawn_actor(blueprint, carla_transform(request.transform))
    except (AttributeError, RuntimeError, TypeError, ValueError, CarlaAdapterError) as exc:
        return SpawnResult(request_index=index, actor_id=None, error=str(exc))
    return SpawnResult(request_index=index, actor_id=int(actor.id), error=None)


def _parent_actor(world: CarlaWorld, actor_id: int | None) -> object | None:
    """Return an attach parent actor when requested."""
    if actor_id is None:
        return None
    actor = world.get_actors().find(actor_id)
    if actor is None:
        msg = f"Parent actor {actor_id} was not found."
        raise CarlaAdapterError(msg)
    return actor


def _spawn_sensor(
    world: CarlaWorld,
    blueprint: CarlaBlueprint,
    transform: Transform,
    parent: object | None,
) -> CarlaSensor:
    """Spawn a sensor actor."""
    try:
        actor = world.spawn_actor(blueprint, carla_transform(transform), parent)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc
    return require_sensor(actor)


def _capture_image(sensor: CarlaSensor) -> CarlaImage:
    """Capture one image from a CARLA sensor listener."""
    frames: Queue[object] = Queue(maxsize=1)
    sensor.listen(frames.put)
    try:
        frame = frames.get(timeout=5.0)
    except Empty as exc:
        msg = "Timed out waiting for a sensor frame."
        raise CarlaAdapterError(msg) from exc
    finally:
        sensor.stop()
    return _require_image(frame)


def _require_image(candidate: object) -> CarlaImage:
    """Validate a dynamic CARLA image/frame object."""
    missing_api = not all(hasattr(candidate, name) for name in ("frame", "save_to_disk"))
    if missing_api:
        msg = "CARLA sensor frame does not expose the expected image API."
        raise CarlaAdapterError(msg)
    return cast("CarlaImage", candidate)


def _mime_type(path: Path) -> str:
    """Infer a capture MIME type from the output path."""
    if path.suffix.lower() == ".jpg" or path.suffix.lower() == ".jpeg":
        return "image/jpeg"
    return "image/png"


def _configured_blueprint(world: CarlaWorld, request: SpawnRequest) -> CarlaBlueprint:
    """Find and configure a CARLA blueprint for a spawn request."""
    blueprint = world.get_blueprint_library().find(request.blueprint_id)
    for attribute_id, value in request.attributes.items():
        blueprint.set_attribute(attribute_id, value)
    return blueprint
