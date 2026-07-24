"""Python CARLA adapter mixin for script-only experiment capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from carla_mcp import experiment_runtime
from carla_mcp.models import CameraAttachRequest, Location, SensorInfo, Transform

if TYPE_CHECKING:
    from pathlib import Path

    from carla_mcp.carla_protocols import CarlaClient, CarlaWorld
    from carla_mcp.models import CaptureInfo


class PythonCarlaExperimentMixin:
    """Experiment capabilities layered onto the core Python CARLA adapter."""

    def _client(self) -> CarlaClient:
        """Return a configured CARLA client."""
        raise NotImplementedError

    @staticmethod
    def _world(client: CarlaClient) -> CarlaWorld:
        """Return the current CARLA world."""
        raise NotImplementedError

    def attach_camera(self, *, request: CameraAttachRequest) -> SensorInfo:
        """Attach a camera sensor."""
        raise NotImplementedError

    def capture_sensor_frame(self, *, sensor_id: int, output_path: Path) -> CaptureInfo:
        """Capture one sensor frame to disk."""
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
        return experiment_runtime.read_sensor_stream(
            self._world(self._client()),
            sensor_id=sensor_id,
            frame_count=frame_count,
            output_dir=output_dir,
        )

    def detach_sensor(self, sensor_id: int) -> dict[str, object]:
        """Stop and destroy a sensor actor."""
        return experiment_runtime.detach_sensor(self._world(self._client()), sensor_id)

    def get_spawn_points(self) -> dict[str, object]:
        """Return legal vehicle spawn transforms from the loaded map."""
        return experiment_runtime.spawn_points(self._world(self._client()))

    def get_waypoint(
        self,
        *,
        location: Location,
        lane_type: str = "Driving",
        project_to_road: bool = True,
    ) -> dict[str, object]:
        """Return map waypoint metadata for a location."""
        return experiment_runtime.waypoint(
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
        """Generate an A-to-B waypoint route by following official waypoint.next links."""
        return experiment_runtime.route(
            self._world(self._client()),
            start=start,
            end=end,
            step_meters=step_meters,
            max_steps=max_steps,
        )

    def get_topology(self, *, max_segments: int = 200) -> dict[str, object]:
        """Return a compact road topology graph."""
        return experiment_runtime.topology(self._world(self._client()), max_segments=max_segments)

    def get_landmarks(self, *, max_count: int = 200) -> dict[str, object]:
        """Return map landmarks when the CARLA map exposes them."""
        return experiment_runtime.landmarks(self._world(self._client()), max_count=max_count)

    def apply_vehicle_control(
        self,
        *,
        actor_id: int,
        control: dict[str, object],
    ) -> dict[str, object]:
        """Apply direct VehicleControl to one actor."""
        return experiment_runtime.apply_vehicle_control(
            self._world(self._client()),
            actor_id=actor_id,
            control=control,
        )

    def get_vehicle_telemetry(self, actor_id: int) -> dict[str, object]:
        """Return vehicle state useful for closed-loop control."""
        return experiment_runtime.vehicle_telemetry(self._world(self._client()), actor_id)

    def set_actor_transform(self, *, actor_id: int, transform: Transform) -> dict[str, object]:
        """Teleport an actor to a transform."""
        return experiment_runtime.set_actor_transform(
            self._world(self._client()),
            actor_id=actor_id,
            transform=transform,
        )

    def set_vehicle_lights(self, *, actor_id: int, state: str | int) -> dict[str, object]:
        """Set vehicle light state by integer mask or pipe-separated names."""
        return experiment_runtime.set_vehicle_lights(
            self._world(self._client()),
            actor_id=actor_id,
            state=state,
        )

    def set_target_velocity(self, *, actor_id: int, velocity: Location) -> dict[str, object]:
        """Set actor target velocity."""
        return experiment_runtime.set_target_velocity(
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
        return experiment_runtime.spawn_walker_actors(
            self._world(self._client()),
            count=count,
            speed=speed,
            seed=seed,
        )

    def set_walker_destination(
        self,
        *,
        controller_id: int,
        location: Location,
    ) -> dict[str, object]:
        """Send a walker AI controller to a destination."""
        return experiment_runtime.set_walker_destination(
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
        return experiment_runtime.apply_walker_control(
            self._world(self._client()),
            actor_id=actor_id,
            direction=direction,
            speed=speed,
        )

    def freeze_traffic_lights(self, *, enabled: bool) -> dict[str, object]:
        """Freeze or unfreeze all traffic lights."""
        return experiment_runtime.freeze_traffic_lights(
            self._world(self._client()),
            enabled=enabled,
        )

    def set_traffic_light_state(self, *, actor_id: int, state: str) -> dict[str, object]:
        """Set one traffic light state."""
        return experiment_runtime.set_traffic_light_state(
            self._world(self._client()),
            actor_id=actor_id,
            state=state,
        )

    def set_spectator(self, transform: Transform) -> dict[str, object]:
        """Move the spectator camera."""
        return experiment_runtime.set_spectator(self._world(self._client()), transform)

    def save_screenshot(
        self,
        *,
        output_path: Path,
        attributes: dict[str, str],
    ) -> dict[str, object]:
        """Capture a temporary RGB camera frame from the spectator viewpoint."""
        world = self._world(self._client())
        spectator_transform = experiment_runtime.spectator_transform(world)
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
        return capture.to_dict()

    def get_weather(self) -> dict[str, object]:
        """Return current weather when supported."""
        return experiment_runtime.weather(self._world(self._client()))

    def set_weather(self, parameters: dict[str, float]) -> dict[str, object]:
        """Set weather parameters when supported."""
        return experiment_runtime.set_weather(self._world(self._client()), parameters)

    def replay_recording(
        self,
        *,
        path: Path,
        start: float = 0.0,
        duration: float = 0.0,
        follow_id: int = 0,
        replay_sensors: bool = False,
    ) -> dict[str, object]:
        """Replay a CARLA recorder file."""
        return experiment_runtime.replay_recording(
            self._client(),
            experiment_runtime.ReplayRequest(
                path=path,
                start=start,
                duration=duration,
                follow_id=follow_id,
                replay_sensors=replay_sensors,
            ),
        )

    def query_recording_collisions(
        self,
        *,
        path: Path,
        actor_type: str = "a",
        other_type: str = "a",
    ) -> dict[str, object]:
        """Return recorder collision report text."""
        return experiment_runtime.recording_collisions(
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
        return experiment_runtime.recording_actors_blocked(
            self._client(),
            path=path,
            min_time=min_time,
            min_distance=min_distance,
        )

    def reload_world(self, *, reset_settings: bool = False) -> dict[str, object]:
        """Reload the current world."""
        world = cast("Any", self._client()).reload_world(reset_settings)
        return experiment_runtime.world_state_payload(world)

    def apply_batch(self, commands: list[dict[str, object]]) -> dict[str, object]:
        """Apply a small JSON-compatible batch using carla.command."""
        return experiment_runtime.apply_batch(self._client(), commands)

    def list_capabilities(self) -> dict[str, object]:
        """Probe available CARLA capabilities."""
        client = self._client()
        return experiment_runtime.capability_report(client, self._world(client))
