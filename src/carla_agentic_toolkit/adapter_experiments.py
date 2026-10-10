"""Python CARLA adapter mixin for script-only experiment capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit import (
    experiment_common,
    experiment_environment,
    experiment_navigation,
    experiment_perception,
    experiment_physics,
    experiment_replay,
    experiment_scene,
    experiment_vehicle,
    experiment_walkers,
)
from carla_agentic_toolkit.authoritative_destroy import destroy_result_cleaned
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.managed_world import world_identity
from carla_agentic_toolkit.models import CameraAttachRequest, Location, SensorInfo, Transform
from carla_agentic_toolkit.rpc_timeouts import RpcTimeoutPolicy, call_map_rpc
from carla_agentic_toolkit.sensor_rendering import require_sensor_rendering
from carla_agentic_toolkit.sensor_subscription import EVENT_SENSOR_TYPES, SensorSubscription

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.actor_creation import SpawnObservers
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaSensor, CarlaWorld
    from carla_agentic_toolkit.models import CaptureInfo, DestroyResult, WorldState
    from carla_agentic_toolkit.script_settings import RunSettings


class PythonCarlaExperimentMixin:
    """Experiment capabilities layered onto the core Python CARLA adapter."""

    _sensor_subscriptions: dict[int, SensorSubscription]
    _sensor_handles: dict[int, CarlaSensor]
    _sensor_world_ids: dict[int, int]
    _subscribed_sensor_handles: dict[int, CarlaSensor]
    _subscription_world_ids: dict[int, int]
    _settings_journal: RunSettings | None
    _rpc_timeout_policy: RpcTimeoutPolicy

    def _client(self) -> CarlaClient:
        """Return a configured CARLA client."""
        raise NotImplementedError

    def configure_rpc_timeout(self, client: object) -> None:
        """Refresh a native client against the execution's remaining RPC budget."""
        raise NotImplementedError

    @staticmethod
    def _world(client: CarlaClient) -> CarlaWorld:
        """Return the current CARLA world."""
        raise NotImplementedError

    def _world_state(self, world: CarlaWorld, *, client: CarlaClient) -> WorldState:
        """Build a world result using the operation's retained client."""
        raise NotImplementedError

    def attach_camera(self, *, request: CameraAttachRequest) -> SensorInfo:
        """Attach a camera sensor."""
        raise NotImplementedError

    def capture_sensor_frame(
        self, *, sensor_id: int, output_path: Path, color_converter: str | None = None
    ) -> CaptureInfo:
        """Capture one sensor frame to disk."""
        raise NotImplementedError

    def _creation_options(self) -> SpawnObservers:
        """Receive the optional native creation hooks from the concrete adapter."""
        raise NotImplementedError

    def _observe_actor_cleanup(self, actor_id: int, world_id: int) -> None:
        """Release only confirmed destruction through the optional journal hook."""
        raise NotImplementedError

    def attach_sensor(
        self,
        *,
        blueprint_id: str,
        transform: Transform,
        attributes: dict[str, str],
        parent_actor_id: int | None,
    ) -> SensorInfo:
        """Attach any CARLA sensor blueprint to an actor."""
        return self.attach_camera(
            request=CameraAttachRequest(
                blueprint_id=blueprint_id,
                transform=transform,
                attributes=attributes,
                parent_actor_id=parent_actor_id,
            )
        )

    def read_sensor_stream(
        self,
        *,
        sensor_id: int,
        frame_count: int,
        output_dir: Path | None,
    ) -> dict[str, object]:
        """Read several frames from a CARLA sensor and optionally persist captures."""
        world = self._world(self._client())
        identity = world_identity(world)
        return experiment_perception.read_sensor_stream(
            world,
            sensor_id=sensor_id,
            frame_count=frame_count,
            output_dir=output_dir,
            after_rendering_check=lambda: self._require_cleanup_episode(identity),
        )

    def detach_sensor(self, sensor_id: int) -> dict[str, object]:
        """Stop and destroy a sensor actor."""
        world = self._world(self._client())
        identity = self._sensor_cleanup_identity(sensor_id, world)
        self._require_cleanup_episode(identity)
        sensor = self._sensor_actor(sensor_id)
        self._require_cleanup_episode(identity)
        self.close_sensor_subscription(sensor_id)
        self._require_cleanup_episode(identity)
        experiment_perception.stop_sensor_handle(sensor)
        self._require_cleanup_episode(identity)
        result = self._destroy_uncached_actor(world, sensor_id, expected_world_id=identity)
        if not destroy_result_cleaned(result):
            raise CarlaAdapterError(result.error or "Sensor destruction failed.")
        self._sensor_handles.pop(sensor_id, None)
        self._sensor_world_ids.pop(sensor_id, None)
        self._subscribed_sensor_handles.pop(sensor_id, None)
        self._subscription_world_ids.pop(sensor_id, None)
        payload: dict[str, object] = {"sensor_id": sensor_id, "destroyed": result.destroyed}
        if result.error is not None:
            payload["error"] = result.error
        return payload

    def _sensor_cleanup_identity(self, sensor_id: int, world: CarlaWorld) -> int:
        """Authorize retained created or subscribed handles only in their recorded episode."""
        if sensor_id in self._sensor_handles:
            return self._recorded_sensor_identity(self._sensor_world_ids, sensor_id, "creation")
        if sensor_id in self._subscribed_sensor_handles:
            return self._recorded_sensor_identity(
                self._subscription_world_ids, sensor_id, "subscription"
            )
        return world_identity(world)

    @staticmethod
    def _recorded_sensor_identity(records: dict[int, int], sensor_id: int, source: str) -> int:
        """Require explicit episode evidence for every retained native sensor handle."""
        identity = records.get(sensor_id)
        if identity is None:
            message = f"Sensor {source} episode is unavailable; cleanup refused."
            raise CarlaAdapterError(message)
        return identity

    def _forget_sensor_records(self) -> None:
        """Forget old handles only after an explicitly acknowledged world replacement."""
        self._sensor_handles.clear()
        self._sensor_world_ids.clear()
        self._subscribed_sensor_handles.clear()
        self._subscription_world_ids.clear()

    def _has_retained_sensor(self, sensor_id: int) -> bool:
        """Route every retained sensor through origin-guarded authoritative cleanup."""
        return sensor_id in self._sensor_handles or sensor_id in self._subscribed_sensor_handles

    def _require_cleanup_episode(self, identity: int) -> None:
        """Require the originating episode before stopping a resolved sensor handle."""
        raise NotImplementedError

    def _destroy_uncached_actor(
        self, world: CarlaWorld, actor_id: int, *, expected_world_id: int | None = None
    ) -> DestroyResult:
        """Use the adapter's guarded, non-ticking server destroy path."""
        raise NotImplementedError

    def _sensor_actor(self, sensor_id: int) -> CarlaSensor:
        """Prefer original subscribed or created handles over a fresh snapshot lookup."""
        sensor = self._subscribed_sensor_handles.get(sensor_id)
        if sensor is not None:
            identity = self._recorded_sensor_identity(
                self._subscription_world_ids, sensor_id, "subscription"
            )
            self._require_cleanup_episode(identity)
            return sensor
        sensor = self._sensor_handles.get(sensor_id)
        if sensor is not None:
            return sensor
        return experiment_common.sensor_actor(self._world(self._client()), sensor_id)

    def subscribe_sensor(
        self, sensor_id: int, *, event_sensor: bool | None = None, capacity: int = 32
    ) -> dict[str, object]:
        """Install a bounded listener before the owner advances the world."""
        if sensor_id in self._sensor_subscriptions:
            message = f"Sensor {sensor_id} already has an active subscription."
            raise CarlaAdapterError(message)
        world = self._world(self._client())
        identity = self._sensor_cleanup_identity(sensor_id, world)
        self._require_cleanup_episode(identity)
        sensor = self._sensor_actor(sensor_id)
        self._require_cleanup_episode(identity)
        require_sensor_rendering(world, sensor.type_id)
        if sensor.type_id.startswith("sensor.camera."):
            self._require_cleanup_episode(identity)
        is_event = sensor.type_id in EVENT_SENSOR_TYPES if event_sensor is None else event_sensor
        self._sensor_subscriptions[sensor_id] = SensorSubscription(
            sensor, event_sensor=is_event, capacity=capacity
        )
        self._subscribed_sensor_handles[sensor_id] = sensor
        self._subscription_world_ids[sensor_id] = identity
        return {"sensor_id": sensor_id, "event_sensor": is_event, "capacity": capacity}

    def drain_sensor(
        self,
        sensor_id: int,
        frame: int,
        *,
        timeout_seconds: float = 0.0,
        output_dir: Path | None = None,
    ) -> dict[str, object]:
        """Read available samples for an owner frame without issuing simulator ticks."""
        subscription = self._subscription(sensor_id)
        batch = subscription.drain(frame, timeout_seconds=timeout_seconds)
        frames = list(batch.frames)
        paths = experiment_perception.save_sensor_frames(
            frames,
            sensor_id,
            output_dir,
            sensor_type=self._subscribed_sensor_handles[sensor_id].type_id,
        )
        return {
            "sensor_id": sensor_id,
            **batch.to_dict(),
            "frames": [experiment_perception.sensor_frame_digest(item) for item in frames],
            "paths": [str(path) for path in paths],
        }

    def _subscription(self, sensor_id: int) -> SensorSubscription:
        """Require a listener owned by this adapter execution."""
        try:
            return self._sensor_subscriptions[sensor_id]
        except KeyError as exc:
            message = f"Sensor {sensor_id} has no subscription; call subscribe_sensor first."
            raise CarlaAdapterError(message) from exc

    def close_sensor_subscription(self, sensor_id: int) -> dict[str, object]:
        """Idempotently close one owned listener without destroying its sensor actor."""
        subscription = self._sensor_subscriptions.get(sensor_id)
        if subscription is not None:
            identity = self._recorded_sensor_identity(
                self._subscription_world_ids, sensor_id, "subscription"
            )
            self._require_cleanup_episode(identity)
            subscription.close()
            self._require_cleanup_episode(identity)
            self._sensor_subscriptions.pop(sensor_id, None)
        return {"sensor_id": sensor_id, "closed": True}

    def close_sensor_subscriptions(self) -> None:
        """Close all listener queues before ending an execution or replacing a world."""
        errors: list[str] = []
        for sensor_id in tuple(self._sensor_subscriptions):
            self._close_subscription_recording_error(sensor_id, errors)
        if errors:
            raise CarlaAdapterError("; ".join(errors))

    def _close_subscription_recording_error(self, sensor_id: int, errors: list[str]) -> None:
        """Continue releasing other listeners if one sensor's stop fails."""
        try:
            self.close_sensor_subscription(sensor_id)
        except CarlaAdapterError as exc:
            errors.append(str(exc))

    def get_spawn_points(self) -> dict[str, object]:
        """Return legal vehicle spawn transforms from the loaded map."""
        return experiment_navigation.spawn_points(self._world(self._client()))

    def get_waypoint(
        self,
        *,
        location: Location,
        lane_type: str = "Driving",
        project_to_road: bool = True,
    ) -> dict[str, object]:
        """Return map waypoint metadata for a location."""
        return experiment_navigation.waypoint(
            self._world(self._client()),
            location=location,
            lane_type_name=lane_type,
            project_to_road=project_to_road,
        )

    def generate_route(
        self,
        *,
        start: Location,
        end: Location,
        step_meters: float = 2.0,
        max_steps: int = 200,
    ) -> dict[str, object]:
        """Follow greedy waypoint links and report actual remaining destination distance."""
        return experiment_navigation.route(
            self._world(self._client()),
            start=start,
            end=end,
            step_meters=step_meters,
            max_steps=max_steps,
        )

    def get_topology(self, *, max_segments: int = 200) -> dict[str, object]:
        """Return a compact road topology graph."""
        return experiment_navigation.topology(
            self._world(self._client()), max_segments=max_segments
        )

    def get_landmarks(
        self,
        *,
        max_count: int = 200,
        landmark_type: str | None = None,
        landmark_id: str | None = None,
    ) -> dict[str, object]:
        """Return bounded map landmarks through official query variants."""
        return experiment_navigation.landmarks(
            self._world(self._client()),
            max_count=max_count,
            landmark_type=landmark_type,
            landmark_id=landmark_id,
        )

    def get_environment_objects(
        self,
        *,
        label: str,
        max_count: int,
        include_level_bounds: bool,
    ) -> dict[str, object]:
        """Return bounded static objects and optional semantic level bounds."""
        return experiment_environment.get_environment_objects(
            self._world(self._client()),
            label=label,
            max_count=max_count,
            include_level_bounds=include_level_bounds,
        )

    def enable_environment_objects(
        self,
        *,
        object_ids: tuple[int, ...],
        enabled: bool,
    ) -> dict[str, object]:
        """Enable or disable explicit environment object IDs."""
        return experiment_environment.enable_environment_objects(
            self._world(self._client()), object_ids=object_ids, enabled=enabled
        )

    def set_map_layer(self, *, layer: str, loaded: bool) -> dict[str, object]:
        """Load or unload one runtime map layer."""
        return experiment_environment.set_map_layer(
            self._world(self._client()), layer=layer, loaded=loaded
        )

    def generate_opendrive_world(
        self,
        *,
        opendrive: str,
        parameters: dict[str, object],
        reset_settings: bool,
    ) -> dict[str, object]:
        """Generate a world from bounded OpenDRIVE text."""
        self.close_sensor_subscriptions()
        self._capture_replacement_settings()
        client = self._client()
        world = call_map_rpc(
            client,
            self._rpc_timeout_policy,
            lambda: experiment_environment.generate_opendrive_world(
                client,
                opendrive=opendrive,
                parameters=parameters,
                reset_settings=reset_settings,
            ),
        )
        self.configure_rpc_timeout(client)
        self._rebind_replacement_settings(cast("CarlaWorld", world))
        self._forget_sensor_records()
        return self._world_state(cast("CarlaWorld", world), client=client).to_dict()

    def configure_actor_physics(
        self,
        *,
        actor_id: int,
        simulate_physics: bool | None,
        gravity: bool | None,
    ) -> dict[str, object]:
        """Toggle actor physics and gravity when supported."""
        return experiment_physics.configure_actor_physics(
            self._world(self._client()),
            actor_id=actor_id,
            simulate_physics=simulate_physics,
            gravity=gravity,
        )

    def apply_actor_physics(
        self,
        *,
        actor_id: int,
        action: str,
        vector: Location,
    ) -> dict[str, object]:
        """Apply one vector-based actor physics operation."""
        return experiment_physics.apply_actor_physics(
            self._world(self._client()),
            actor_id=actor_id,
            action=action,
            vector=vector,
        )

    def get_vehicle_physics(self, actor_id: int) -> dict[str, object]:
        """Return bounded vehicle physics fields."""
        return experiment_physics.get_vehicle_physics(self._world(self._client()), actor_id)

    def update_vehicle_physics(
        self,
        *,
        actor_id: int,
        changes: dict[str, object],
    ) -> dict[str, object]:
        """Update supported vehicle physics fields."""
        return experiment_physics.update_vehicle_physics(
            self._world(self._client()),
            actor_id=actor_id,
            changes=changes,
        )

    def apply_vehicle_control(
        self,
        *,
        actor_id: int,
        control: dict[str, object],
    ) -> dict[str, object]:
        """Apply direct VehicleControl to one actor."""
        return experiment_vehicle.apply_vehicle_control(
            self._world(self._client()),
            actor_id=actor_id,
            control=control,
        )

    def get_vehicle_telemetry(self, actor_id: int) -> dict[str, object]:
        """Return frame-coherent motion with separately read control and road state."""
        return experiment_vehicle.vehicle_telemetry(self._world(self._client()), actor_id)

    def set_actor_transform(self, *, actor_id: int, transform: Transform) -> dict[str, object]:
        """Teleport an actor to a transform."""
        return experiment_vehicle.set_actor_transform(
            self._world(self._client()),
            actor_id=actor_id,
            transform=transform,
        )

    def set_vehicle_lights(self, *, actor_id: int, state: str | int) -> dict[str, object]:
        """Set vehicle light state by integer mask or pipe-separated names."""
        return experiment_vehicle.set_vehicle_lights(
            self._world(self._client()),
            actor_id=actor_id,
            state=state,
        )

    def set_target_velocity(self, *, actor_id: int, velocity: Location) -> dict[str, object]:
        """Set actor target velocity."""
        return experiment_vehicle.set_target_velocity(
            self._world(self._client()),
            actor_id=actor_id,
            velocity=velocity,
        )

    def spawn_walkers(
        self,
        *,
        count: int,
        speed: float = 1.4,
        seed: int | None = None,
    ) -> dict[str, object]:
        """Spawn pedestrians and AI walker controllers."""
        return experiment_walkers.spawn_walker_actors(
            self._world(self._client()),
            count=count,
            speed=speed,
            seed=seed,
            **self._creation_options(),
        )

    def set_walker_destination(
        self,
        *,
        controller_id: int,
        location: Location,
    ) -> dict[str, object]:
        """Send a walker AI controller to a destination."""
        return experiment_walkers.set_walker_destination(
            self._world(self._client()),
            controller_id=controller_id,
            location=location,
        )

    def apply_walker_control(
        self,
        *,
        actor_id: int,
        direction: Location,
        speed: float,
    ) -> dict[str, object]:
        """Apply manual WalkerControl to a pedestrian actor."""
        return experiment_walkers.apply_walker_control(
            self._world(self._client()),
            actor_id=actor_id,
            direction=direction,
            speed=speed,
        )

    def freeze_traffic_lights(self, *, enabled: bool) -> dict[str, object]:
        """Freeze or unfreeze all traffic lights."""
        return experiment_scene.freeze_traffic_lights(
            self._world(self._client()),
            enabled=enabled,
        )

    def set_traffic_light_state(self, *, actor_id: int, state: str) -> dict[str, object]:
        """Set one traffic light state."""
        return experiment_scene.set_traffic_light_state(
            self._world(self._client()),
            actor_id=actor_id,
            state=state,
        )

    def set_spectator(self, transform: Transform) -> dict[str, object]:
        """Move the spectator camera."""
        return experiment_scene.set_spectator(self._world(self._client()), transform)

    def watch_actor(
        self,
        *,
        actor_id: int,
        seconds: float,
        distance: float,
        height: float,
    ) -> dict[str, object]:
        """Follow one actor with a smooth simulator-tick chase camera."""
        return experiment_scene.watch_actor(
            self._world(self._client()),
            actor_id=actor_id,
            seconds=seconds,
            distance=distance,
            height=height,
        )

    def save_screenshot(
        self,
        *,
        output_path: Path,
        attributes: dict[str, str],
    ) -> dict[str, object]:
        """Capture a temporary RGB camera frame from the spectator viewpoint."""
        world = self._world(self._client())
        identity = world_identity(world)
        require_sensor_rendering(world, "sensor.camera.rgb")
        self._require_cleanup_episode(identity)
        spectator_transform = experiment_scene.spectator_transform(world)
        info = self.attach_sensor(
            blueprint_id="sensor.camera.rgb",
            transform=spectator_transform,
            attributes=attributes,
            parent_actor_id=None,
        )
        try:
            capture = self.capture_sensor_frame(sensor_id=info.sensor_id, output_path=output_path)
        finally:
            self.detach_sensor(info.sensor_id)
            self._observe_actor_cleanup(info.sensor_id, identity)
        return capture.to_dict()

    def get_weather(self) -> dict[str, object]:
        """Return current weather when supported."""
        return experiment_scene.weather(self._world(self._client()))

    def set_weather(self, parameters: dict[str, float]) -> dict[str, object]:
        """Set weather parameters when supported."""
        return experiment_scene.set_weather(self._world(self._client()), parameters)

    def replay_recording(  # noqa: PLR0913 -- Preserve the existing public recorder arguments.
        self,
        *,
        path: Path,
        start: float = 0.0,
        duration: float = 0.0,
        follow_id: int = 0,
        replay_sensors: bool = False,
        do_tick: bool = True,
    ) -> dict[str, object]:
        """Replay a CARLA recorder file; only do_tick=True is supported."""
        return experiment_replay.replay_recording(
            self._client(),
            experiment_replay.ReplayRequest(
                path=path,
                start=start,
                duration=duration,
                follow_id=follow_id,
                replay_sensors=replay_sensors,
                do_tick=do_tick,
            ),
        )

    def query_recording_collisions(
        self,
        *,
        path: Path,
        actor_type: str = "a",
        other_type: str = "a",
    ) -> dict[str, object]:
        """Query collisions: h=hero, v=vehicle, w=walker, t=traffic light, o=other, a=any."""
        return experiment_replay.recording_collisions(
            self._client(),
            path=path,
            actor_type=actor_type,
            other_type=other_type,
        )

    def query_recording_actors_blocked(
        self,
        *,
        path: Path,
        min_time: float = 30.0,
        min_distance: float = 10.0,
    ) -> dict[str, object]:
        """Return recorder blocked-actor report text."""
        return experiment_replay.recording_actors_blocked(
            self._client(),
            path=path,
            min_time=min_time,
            min_distance=min_distance,
        )

    def reload_world(self, *, reset_settings: bool) -> dict[str, object]:
        """Reload with an explicit reset choice; False intentionally keeps settings."""
        self.close_sensor_subscriptions()
        self._capture_replacement_settings()
        client = self._client()
        world = call_map_rpc(
            client,
            self._rpc_timeout_policy,
            lambda: cast("Any", client).reload_world(reset_settings),
        )
        self.configure_rpc_timeout(client)
        self._rebind_replacement_settings(world)
        self._forget_sensor_records()
        return self._world_state(world, client=client).to_dict()

    def _capture_replacement_settings(self) -> None:
        if self._settings_journal is not None:
            self._settings_journal.capture_world(self._world(self._client()))

    def _rebind_replacement_settings(self, world: CarlaWorld) -> None:
        if self._settings_journal is not None:
            self._settings_journal.rebind_world(world)

    def apply_batch(
        self, commands: list[dict[str, object]], *, do_tick: bool = False
    ) -> dict[str, object]:
        """Apply a response batch without ticking unless explicitly requested."""
        return experiment_replay.apply_batch(self._client(), commands, do_tick=do_tick)

    def list_capabilities(self) -> dict[str, object]:
        """Probe available CARLA capabilities."""
        client = self._client()
        return experiment_replay.capability_report(client, self._world(client))
