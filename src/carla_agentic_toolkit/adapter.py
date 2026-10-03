"""CARLA Python API adapter boundary."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.actor_runtime import actor_snapshot, destroy_actor
from carla_agentic_toolkit.adapter_experiments import PythonCarlaExperimentMixin
from carla_agentic_toolkit.adapter_objects import (
    _blueprint_info,
    _capture_image,
    _carla_client_factory,
    _configured_blueprint,
    _mime_type,
    _parent_actor,
    _require_carla_client,
    _safe_call,
    _spawn_actor,
    _spawn_sensor,
)
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.experiment_common import (
    actor_counts,
    frame,
    map_name,
    world_settings,
    world_state,
)
from carla_agentic_toolkit.experiment_perception import require_async_sensor_read
from carla_agentic_toolkit.managed_session import world_identity
from carla_agentic_toolkit.models import (
    ActorSnapshot,
    AutopilotRequest,
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
    TrafficVehiclePathRequest,
    WorldState,
)
from carla_agentic_toolkit.ownership import cleanup_report
from carla_agentic_toolkit.recorder_paths import _server_recorder_path
from carla_agentic_toolkit.traffic_runtime import (
    advance_world_once,
    populate_traffic_actors,
    set_actor_autopilot,
    traffic_manager,
)
from carla_agentic_toolkit.traffic_runtime import (
    configure_traffic_manager as configure_traffic_manager_runtime,
)
from carla_agentic_toolkit.traffic_tuning import set_traffic_vehicle_path, tune_traffic_vehicle

__all__ = ["PythonCarlaAdapter"]

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaSensor, CarlaWorld
    from carla_agentic_toolkit.sensor_subscription import SensorSubscription


class PythonCarlaAdapter(PythonCarlaExperimentMixin):
    """Adapter backed by CARLA's official Python API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 2000, timeout: float = 10.0) -> None:
        """Create an adapter for a CARLA server."""
        self._host = host
        self._port = port
        self._timeout = timeout
        self._recording: RecordingInfo | None = None
        self._connected_client: CarlaClient | None = None
        self._sensor_subscriptions: dict[int, SensorSubscription] = {}
        self._sensor_handles: dict[int, CarlaSensor] = {}

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
        self.close_sensor_subscriptions()
        try:
            world = client.load_world(map_name)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        self._sensor_handles.clear()
        return world_state(world)

    def get_world_identity(self) -> int:
        """Read the connected episode identity without advancing the world."""
        try:
            return world_identity(self._client().get_world())
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc

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
        return tuple(self._destroy_actor(world, actor_id) for actor_id in actor_ids)

    def _destroy_actor(self, world: CarlaWorld, actor_id: int) -> DestroyResult:
        """Use created sensor handles even before the next snapshot exposes their IDs."""
        if actor_id not in self._sensor_handles:
            result = destroy_actor(world, actor_id)
            if result.error == "Actor was not found.":
                return self._destroy_uncached_actor(world, actor_id)
            return result
        try:
            result = self.detach_sensor(actor_id)
        except CarlaAdapterError as exc:
            return DestroyResult(actor_id, destroyed=False, error=str(exc))
        return DestroyResult(actor_id, destroyed=bool(result["destroyed"]), error=None)

    def _destroy_uncached_actor(self, world: CarlaWorld, actor_id: int) -> DestroyResult:
        """Use an authoritative server response when a cached snapshot omits an actor."""
        try:
            identity = world_identity(world)
            self._require_cleanup_episode(identity)
            payload = self.apply_batch(
                [{"action": "destroy_actor", "actor_id": actor_id}],
                do_tick=False,
            )
            self._require_cleanup_episode(identity)
            return _destroy_batch_result(actor_id, payload)
        except (CarlaAdapterError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
            return DestroyResult(actor_id, destroyed=False, error=str(exc))

    def _require_cleanup_episode(self, identity: int) -> None:
        if self.get_world_identity() != identity:
            message = "CARLA world episode changed during authoritative actor cleanup."
            raise CarlaAdapterError(message)

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
        if request.advance_world:
            self._advance_population(world, actor_ids)
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

    def _advance_population(self, world: CarlaWorld, actor_ids: list[int]) -> int:
        """Clean newly created actors if required advancement fails, retaining evidence."""
        try:
            return advance_world_once(world)
        except CarlaAdapterError as exc:
            cleanup = _population_cleanup(self, actor_ids)
            destroyed = cast("list[int]", cleanup["destroyed_actor_ids"])
            exc.details.update(
                actor_ids=actor_ids,
                remaining_actor_ids=[item for item in actor_ids if item not in destroyed],
                cleanup=cleanup,
            )
            raise

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
        if request.advance_world:
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

    def tune_traffic_vehicle(
        self,
        *,
        actor_id: int,
        traffic_manager_port: int,
        settings: dict[str, object],
    ) -> dict[str, object]:
        """Apply validated per-vehicle Traffic Manager settings."""
        return tune_traffic_vehicle(
            self._client(),
            actor_id=actor_id,
            traffic_manager_port=traffic_manager_port,
            settings=settings,
        )

    def set_traffic_vehicle_path(
        self,
        *,
        request: TrafficVehiclePathRequest,
    ) -> dict[str, object]:
        """Upload one bounded path or route for a Traffic Manager vehicle."""
        return set_traffic_vehicle_path(self._client(), request)

    def record_episode(self, output_path: Path) -> RecordingInfo:
        """Start recording an episode at the simulator-side path."""
        requested_path = _server_recorder_path(output_path)
        client = self._client()
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
        self._sensor_handles[sensor.id] = sensor
        return SensorInfo(
            sensor_id=sensor.id,
            blueprint_id=request.blueprint_id,
            parent_actor_id=request.parent_actor_id,
            attributes=dict(request.attributes),
            transform=request.transform,
        )

    def capture_sensor_frame(self, *, sensor_id: int, output_path: Path) -> CaptureInfo:
        """Capture one sensor frame to disk."""
        world = self._world(self._client())
        require_async_sensor_read(world)
        sensor = self._sensor_actor(sensor_id)
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
        """Retain one client stream so paused synchronous worlds keep observable state."""
        if self._connected_client is None:
            self._connected_client = self._connect()
        return self._connected_client

    def _connect(self) -> CarlaClient:
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


def _population_cleanup(adapter: PythonCarlaAdapter, actor_ids: list[int]) -> dict[str, object]:
    """Attempt every owned actor and preserve cleanup failures for the caller."""
    attempted = tuple(reversed(actor_ids))
    try:
        results = adapter.destroy_actors(attempted)
    except CarlaAdapterError as exc:
        failure: dict[str, object] = {"actor_id": None, "error": str(exc)}
        return cleanup_report(attempted, failures=(failure,))
    return _population_cleanup_results(attempted, results)


def _population_cleanup_results(
    attempted: tuple[int, ...], results: tuple[DestroyResult, ...]
) -> dict[str, object]:
    """Retain per-actor cleanup outcomes without discarding failures."""
    destroyed = [item.actor_id for item in results if item.destroyed]
    return cleanup_report(attempted, destroyed, _population_cleanup_failures(results))


def _population_cleanup_failures(results: tuple[DestroyResult, ...]) -> list[dict[str, object]]:
    """Keep enough evidence to retry cleanup of every remaining actor."""
    return [
        {"actor_id": item.actor_id, "error": item.error or "destroy returned false"}
        for item in results
        if not item.destroyed
    ]


def _destroy_batch_result(actor_id: int, payload: dict[str, object]) -> DestroyResult:
    """Require one matching server acknowledgement; incomplete responses retain ownership."""
    response = _single_destroy_response(payload)
    if not isinstance(response, dict) or response.get("actor_id") != actor_id:
        return DestroyResult(actor_id, destroyed=False, error="Invalid destroy response identity.")
    error = response.get("error")
    return DestroyResult(actor_id, destroyed=not error, error=str(error) if error else None)


def _single_destroy_response(payload: dict[str, object]) -> object:
    responses = payload.get("responses")
    if not isinstance(responses, list) or len(responses) != 1:
        message = "Invalid destroy response count."
        raise CarlaAdapterError(message)
    return responses[0]
