"""Curated CARLA API exposed to sandboxed agent scripts.

This facade is the sole application path between agent scripts and the CARLA
adapter/runtime. CARLA-backed methods share one recoverable-error policy and
publish successful values as run-local snapshots.
"""

from __future__ import annotations

import json
import time
from functools import wraps
from inspect import signature
from pathlib import Path
from typing import TYPE_CHECKING, cast

from carla_mcp.errors import CarlaAdapterError
from carla_mcp.tool_inputs import (
    parse_autopilot_request,
    parse_camera_attach_request,
    parse_location,
    parse_spawn_requests,
    parse_traffic_controller_start_request,
    parse_traffic_density_request,
    parse_traffic_manager_request,
    parse_traffic_population_request,
    parse_transform,
    parse_vehicle_behavior_request,
)
from carla_mcp.traffic_controller_service import InProcessTrafficControllerService

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_mcp.adapter import CarlaAdapter
    from carla_mcp.models import JsonObject
    from carla_mcp.snapshots import RunSnapshots


def _recover(
    error_type: str,
    *,
    endpoint: bool = False,
) -> Callable[[Callable[..., JsonObject]], Callable[..., JsonObject]]:
    """Route one facade method through the recoverable operation policy."""

    def decorate(method: Callable[..., JsonObject]) -> Callable[..., JsonObject]:
        @wraps(method)
        def recovered(self: CarlaScriptApi, *args: object, **kwargs: object) -> JsonObject:
            details = {"host": self._adapter.host, "port": self._adapter.port} if endpoint else {}
            return self._operation(
                error_type,
                lambda: method(self, *args, **kwargs),
                **details,
            )

        return recovered

    return decorate


class CarlaScriptApi:
    """High-level CARLA operations callable from a sandboxed script."""

    def __init__(self, adapter: CarlaAdapter, snapshots: RunSnapshots) -> None:
        """Bind the API to a CARLA adapter and run-local snapshots."""
        self._adapter = adapter
        self._snapshots = snapshots
        self._traffic_controller = InProcessTrafficControllerService()

    def _operation(
        self,
        error_type: str,
        operation: Callable[[], JsonObject],
        **details: object,
    ) -> JsonObject:
        """Return a CARLA value or one uniform recoverable failure."""
        try:
            return operation()
        except CarlaAdapterError as exc:
            return {
                "ok": False,
                "error_type": error_type,
                "message": str(exc),
                "retryable": True,
                "error": str(exc),
                **details,
            }

    @_recover("carla_connection_error", endpoint=True)
    def health_check(self) -> JsonObject:
        """Return CARLA connection health."""
        payload = self._adapter.health_check().to_dict()
        self._snapshots.register_snapshot("carla-snapshot://session/status", payload)
        return payload

    @_recover("world_state_failed")
    def get_world_state(self) -> JsonObject:
        """Return the current CARLA world state."""
        payload = self._adapter.get_world_state().to_dict()
        self._snapshots.register_snapshot("carla-snapshot://world/current", payload)
        return payload

    @_recover("list_worlds_failed")
    def list_worlds(self) -> JsonObject:
        """Return available CARLA maps."""
        payload = {"worlds": sorted(self._adapter.list_worlds())}
        self._snapshots.register_snapshot("carla-snapshot://worlds", payload)
        return payload

    @_recover("load_world_failed")
    def load_world(self, map_name: str) -> JsonObject:
        """Load a CARLA map by name."""
        payload = self._adapter.load_world(map_name).to_dict()
        self._snapshots.register_snapshot("carla-snapshot://world/current", payload)
        return payload

    @_recover("set_sync_mode_failed")
    def set_sync_mode(
        self,
        *,
        enabled: bool,
        fixed_delta_seconds: float | None = 0.05,
    ) -> JsonObject:
        """Configure synchronous mode and fixed timestep."""
        payload = self._adapter.set_sync_mode(
            enabled=enabled,
            fixed_delta_seconds=fixed_delta_seconds,
        ).to_dict()
        self._snapshots.register_snapshot("carla-snapshot://world/current", payload)
        return payload

    @_recover("tick_failed")
    def tick(self) -> JsonObject:
        """Advance the simulation by one frame."""
        payload = {"frame": self._adapter.tick()}
        self._snapshots.register_snapshot("carla-snapshot://session/last-tick", payload)
        return payload

    @_recover("tick_failed")
    def tick_n(self, count: int) -> JsonObject:
        """Advance the simulation by several frames inside the sandbox."""
        frames = [self._adapter.tick() for _ in range(max(count, 0))]
        if frames:
            self._snapshots.register_snapshot(
                "carla-snapshot://session/last-tick", {"frame": frames[-1]}
            )
        return {"frames": frames, "count": len(frames)}

    @_recover("list_blueprints_failed")
    def list_blueprints(self, filter_pattern: str = "*") -> JsonObject:
        """List actor blueprints matching a wildcard filter."""
        payload = {
            "blueprints": [item.to_dict() for item in self._adapter.list_blueprints(filter_pattern)]
        }
        self._snapshots.register_snapshot(f"carla-snapshot://blueprints/{filter_pattern}", payload)
        return payload

    @_recover("spawn_actor_batch_failed")
    def spawn_actor_batch(self, requests: list[dict[str, object]]) -> JsonObject:
        """Spawn actors from JSON-compatible spawn requests."""
        results = self._adapter.spawn_actor_batch(parse_spawn_requests(requests))
        payload = {"results": [item.to_dict() for item in results]}
        self._snapshots.register_snapshot("carla-snapshot://actors", payload)
        return payload

    @_recover("list_actors_failed")
    def list_actors(self, filter_pattern: str = "*") -> JsonObject:
        """List current actors matching a wildcard filter."""
        payload = {"actors": [item.to_dict() for item in self._adapter.list_actors(filter_pattern)]}
        self._snapshots.register_snapshot("carla-snapshot://actors/current", payload)
        return payload

    @_recover("destroy_actors_failed")
    def destroy_actors(self, actor_ids: list[int]) -> JsonObject:
        """Destroy explicit actors by ID."""
        payload = {
            "results": [item.to_dict() for item in self._adapter.destroy_actors(tuple(actor_ids))]
        }
        self._snapshots.register_snapshot("carla-snapshot://actors/destroyed", payload)
        return payload

    @_recover("populate_traffic_failed")
    def populate_traffic(self, request: dict[str, object]) -> JsonObject:
        """Spawn Traffic Manager-controlled vehicles."""
        population = self._adapter.populate_traffic(
            request=parse_traffic_population_request(request)
        )
        payload = population.to_dict()
        self._snapshots.register_snapshot("carla-snapshot://traffic/population", payload)
        self._snapshots.register_snapshot(
            "carla-snapshot://world/current", population.world_state.to_dict()
        )
        return payload

    @_recover("set_autopilot_failed")
    def set_autopilot(self, request: dict[str, object]) -> JsonObject:
        """Toggle Traffic Manager autopilot for existing vehicles."""
        result = self._adapter.set_autopilot(request=parse_autopilot_request(request))
        payload = result.to_dict()
        self._snapshots.register_snapshot("carla-snapshot://traffic/autopilot", payload)
        self._snapshots.register_snapshot(
            "carla-snapshot://world/current", result.world_state.to_dict()
        )
        return payload

    @_recover("configure_traffic_manager_failed")
    def configure_traffic_manager(self, request: dict[str, object]) -> JsonObject:
        """Configure global Traffic Manager behavior."""
        payload = self._adapter.configure_traffic_manager(
            request=parse_traffic_manager_request(request)
        ).to_dict()
        self._snapshots.register_snapshot("carla-snapshot://traffic/manager", payload)
        return payload

    @_recover("start_traffic_controller_failed")
    def start_traffic_controller(self, request: dict[str, object]) -> JsonObject:
        """Start an in-script persistent Traffic Manager controller."""
        status = self._traffic_controller.start(
            parse_traffic_controller_start_request(
                request,
                host=self._adapter.host,
                port=self._adapter.port,
                timeout_seconds=self._adapter.timeout,
            )
        )
        return self._controller_status_payload(status.to_dict())

    @_recover("stop_traffic_controller_failed")
    def stop_traffic_controller(self) -> JsonObject:
        """Stop the in-script Traffic Manager controller."""
        return self._controller_status_payload(self._traffic_controller.stop().to_dict())

    @_recover("traffic_controller_status_failed")
    def traffic_controller_status(self) -> JsonObject:
        """Return in-script Traffic Manager controller status."""
        return self._controller_status_payload(self._traffic_controller.get_status().to_dict())

    @_recover("set_traffic_density_failed")
    def set_traffic_density(self, request: dict[str, object]) -> JsonObject:
        """Converge in-script traffic to a requested density."""
        status = self._traffic_controller.set_density(parse_traffic_density_request(request))
        return self._controller_status_payload(status.to_dict())

    @_recover("set_vehicle_behavior_failed")
    def set_vehicle_behavior(self, request: dict[str, object]) -> JsonObject:
        """Apply a behavior profile to explicit vehicle actors."""
        payload = self._traffic_controller.set_vehicle_behavior(
            parse_vehicle_behavior_request(request)
        ).to_dict()
        self._snapshots.register_snapshot("carla-snapshot://traffic/behaviors", payload)
        return payload

    def _controller_status_payload(self, payload: JsonObject) -> JsonObject:
        self._snapshots.register_snapshot("carla-snapshot://traffic/controller", payload)
        return payload

    @_recover("attach_camera_failed")
    def attach_camera(self, request: dict[str, object]) -> JsonObject:
        """Attach a camera sensor."""
        sensor = self._adapter.attach_camera(request=parse_camera_attach_request(request))
        payload = sensor.to_dict()
        self._snapshots.register_snapshot(f"carla-snapshot://sensors/{sensor.sensor_id}", payload)
        return payload

    @_recover("capture_sensor_frame_failed")
    def capture_sensor_frame(self, sensor_id: int, output_path: str) -> JsonObject:
        """Capture one sensor frame to disk."""
        capture = self._adapter.capture_sensor_frame(
            sensor_id=sensor_id,
            output_path=Path(output_path),
        )
        payload = capture.to_dict()
        self._snapshots.register_snapshot(
            f"carla-snapshot://captures/{capture.capture_id}", payload
        )
        return payload

    @_recover("attach_sensor_failed")
    def attach_sensor(
        self,
        kind: str,
        parent_id: int | None,
        transform: dict[str, object],
        attributes: dict[str, str] | None = None,
    ) -> JsonObject:
        """Attach any supported CARLA sensor kind to an actor."""
        sensor = self._adapter.attach_sensor(
            blueprint_id=_sensor_blueprint(kind),
            transform=parse_transform(transform),
            attributes=attributes or {},
            parent_actor_id=parent_id,
        )
        payload = sensor.to_dict()
        self._snapshots.register_snapshot(f"carla-snapshot://sensors/{sensor.sensor_id}", payload)
        return payload

    @_recover("read_sensor_stream_failed")
    def read_sensor_stream(
        self,
        sensor_id: int,
        frames: int = 1,
        output_dir: str | None = None,
    ) -> JsonObject:
        """Collect sensor frames server-side and return digests plus paths."""
        payload = self._adapter.read_sensor_stream(
            sensor_id=sensor_id,
            frame_count=frames,
            output_dir=Path(output_dir) if output_dir is not None else None,
        )
        self._snapshots.register_snapshot(f"carla-snapshot://sensors/{sensor_id}/stream", payload)
        return payload

    def attach_event_sensor(
        self,
        kind: str,
        parent_id: int,
        attributes: dict[str, str] | None = None,
    ) -> JsonObject:
        """Attach a collision, lane-invasion, or obstacle event sensor."""
        return self.attach_sensor(
            kind=kind,
            parent_id=parent_id,
            transform=_zero_transform(),
            attributes=attributes,
        )

    @_recover("detach_sensor_failed")
    def detach_sensor(self, sensor_id: int) -> JsonObject:
        """Stop and destroy a sensor actor."""
        payload = self._adapter.detach_sensor(sensor_id)
        self._snapshots.register_snapshot(f"carla-snapshot://sensors/{sensor_id}/detached", payload)
        return payload

    @_recover("get_spawn_points_failed")
    def get_spawn_points(self) -> JsonObject:
        """Return legal vehicle spawn transforms from the current map."""
        payload = self._adapter.get_spawn_points()
        self._snapshots.register_snapshot("carla-snapshot://map/spawn-points", payload)
        return payload

    @_recover("get_waypoint_failed")
    def get_waypoint(
        self,
        location: dict[str, object],
        lane_type: str = "Driving",
        *,
        project_to_road: bool = True,
    ) -> JsonObject:
        """Return waypoint metadata for a world location."""
        return self._adapter.get_waypoint(
            location=parse_location(location),
            lane_type=lane_type,
            project_to_road=project_to_road,
        )

    @_recover("generate_route_failed")
    def generate_route(
        self,
        start: dict[str, object],
        end: dict[str, object],
        step_meters: float = 2.0,
        max_steps: int = 200,
    ) -> JsonObject:
        """Generate a waypoint route between two locations."""
        payload = self._adapter.generate_route(
            start=parse_location(start),
            end=parse_location(end),
            step_meters=step_meters,
            max_steps=max_steps,
        )
        self._snapshots.register_snapshot("carla-snapshot://route/latest", payload)
        return payload

    @_recover("get_topology_failed")
    def get_topology(self, max_segments: int = 200) -> JsonObject:
        """Return a compact road topology graph."""
        payload = self._adapter.get_topology(max_segments=max_segments)
        self._snapshots.register_snapshot("carla-snapshot://map/topology", payload)
        return payload

    @_recover("get_landmarks_failed")
    def get_landmarks(self, max_count: int = 200) -> JsonObject:
        """Return map landmarks when supported by the loaded map."""
        payload = self._adapter.get_landmarks(max_count=max_count)
        self._snapshots.register_snapshot("carla-snapshot://map/landmarks", payload)
        return payload

    @_recover("apply_vehicle_control_failed")
    def apply_vehicle_control(self, actor_id: int, **control: object) -> JsonObject:
        """Apply direct throttle, steer, brake, and gear control to a vehicle."""
        return self._adapter.apply_vehicle_control(actor_id=actor_id, control=dict(control))

    @_recover("get_vehicle_telemetry_failed")
    def get_vehicle_telemetry(self, actor_id: int) -> JsonObject:
        """Return transform, speed, control, and traffic-light telemetry."""
        payload = self._adapter.get_vehicle_telemetry(actor_id)
        self._snapshots.register_snapshot(f"carla-snapshot://actors/{actor_id}/telemetry", payload)
        return payload

    @_recover("set_actor_transform_failed")
    def set_actor_transform(self, actor_id: int, transform: dict[str, object]) -> JsonObject:
        """Teleport an actor to a scenario start transform."""
        return self._adapter.set_actor_transform(
            actor_id=actor_id,
            transform=parse_transform(transform),
        )

    @_recover("set_vehicle_lights_failed")
    def set_vehicle_lights(self, actor_id: int, state: str | int) -> JsonObject:
        """Set vehicle light state by integer mask or pipe-separated names."""
        return self._adapter.set_vehicle_lights(actor_id=actor_id, state=state)

    @_recover("set_target_velocity_failed")
    def set_target_velocity(self, actor_id: int, velocity: dict[str, object]) -> JsonObject:
        """Set an actor target velocity vector."""
        return self._adapter.set_target_velocity(
            actor_id=actor_id,
            velocity=parse_location(velocity),
        )

    @_recover("spawn_walkers_failed")
    def spawn_walkers(
        self,
        count: int,
        speed: float = 1.4,
        seed: int | None = None,
    ) -> JsonObject:
        """Spawn pedestrians and AI walker controllers."""
        payload = self._adapter.spawn_walkers(count=count, speed=speed, seed=seed)
        self._snapshots.register_snapshot("carla-snapshot://walkers/latest", payload)
        return payload

    @_recover("set_walker_destination_failed")
    def set_walker_destination(
        self,
        controller_id: int,
        location: dict[str, object],
    ) -> JsonObject:
        """Send a walker controller to a destination."""
        return self._adapter.set_walker_destination(
            controller_id=controller_id,
            location=parse_location(location),
        )

    @_recover("apply_walker_control_failed")
    def apply_walker_control(
        self,
        actor_id: int,
        direction: dict[str, object],
        speed: float,
    ) -> JsonObject:
        """Apply manual direction and speed control to a walker."""
        return self._adapter.apply_walker_control(
            actor_id=actor_id,
            direction=parse_location(direction),
            speed=speed,
        )

    @_recover("freeze_traffic_lights_failed")
    def freeze_traffic_lights(self, *, enabled: bool) -> JsonObject:
        """Freeze or unfreeze all traffic lights."""
        return self._adapter.freeze_traffic_lights(enabled=enabled)

    @_recover("set_traffic_light_state_failed")
    def set_traffic_light_state(self, actor_id: int, state: str) -> JsonObject:
        """Set one traffic light state, such as Red, Yellow, or Green."""
        return self._adapter.set_traffic_light_state(actor_id=actor_id, state=state)

    @_recover("set_spectator_failed")
    def set_spectator(self, transform: dict[str, object]) -> JsonObject:
        """Move the spectator viewpoint."""
        return self._adapter.set_spectator(parse_transform(transform))

    @_recover("save_screenshot_failed")
    def save_screenshot(
        self,
        output_path: str,
        attributes: dict[str, str] | None = None,
    ) -> JsonObject:
        """Capture a temporary RGB camera frame from the spectator viewpoint."""
        return self._adapter.save_screenshot(
            output_path=Path(output_path),
            attributes=attributes or {"image_size_x": "1280", "image_size_y": "720"},
        )

    @_recover("get_weather_failed")
    def get_weather(self) -> JsonObject:
        """Return weather parameters when supported."""
        return self._adapter.get_weather()

    @_recover("set_weather_failed")
    def set_weather(self, parameters: dict[str, float]) -> JsonObject:
        """Set weather parameters when supported by this CARLA build."""
        return self._adapter.set_weather(parameters)

    @_recover("replay_recording_failed")
    def replay_recording(
        self,
        path: str,
        start: float = 0.0,
        duration: float = 0.0,
        follow_id: int = 0,
        *,
        replay_sensors: bool = False,
    ) -> JsonObject:
        """Replay a CARLA recorder file."""
        return self._adapter.replay_recording(
            path=Path(path),
            start=start,
            duration=duration,
            follow_id=follow_id,
            replay_sensors=replay_sensors,
        )

    @_recover("query_recording_collisions_failed")
    def query_recording_collisions(
        self,
        path: str,
        actor_type: str = "a",
        other_type: str = "a",
    ) -> JsonObject:
        """Return recorder collision report text."""
        return self._adapter.query_recording_collisions(
            path=Path(path),
            actor_type=actor_type,
            other_type=other_type,
        )

    @_recover("query_recording_actors_blocked_failed")
    def query_recording_actors_blocked(
        self,
        path: str,
        min_time: float = 30.0,
        min_distance: float = 10.0,
    ) -> JsonObject:
        """Return recorder blocked-actor report text."""
        return self._adapter.query_recording_actors_blocked(
            path=Path(path),
            min_time=min_time,
            min_distance=min_distance,
        )

    @_recover("reload_world_failed")
    def reload_world(self, *, reset_settings: bool = False) -> JsonObject:
        """Reload the current world for a clean scenario reset."""
        payload = self._adapter.reload_world(reset_settings=reset_settings)
        self._snapshots.register_snapshot("carla-snapshot://world/current", payload)
        return payload

    @_recover("apply_batch_failed")
    def apply_batch(self, commands: list[dict[str, object]]) -> JsonObject:
        """Apply supported bulk CARLA commands, such as destroy_actor."""
        return self._adapter.apply_batch(commands)

    @_recover("list_capabilities_failed")
    def list_capabilities(self) -> JsonObject:
        """Return live CARLA version and feature probes."""
        return self._adapter.list_capabilities()

    def describe_api(self) -> JsonObject:
        """Return the script API method catalog for runtime discovery."""
        methods = {
            name: _method_description(getattr(self, name))
            for name in dir(self)
            if _is_script_method(self, name)
        }
        payload = {"methods": methods}
        self._snapshots.register_snapshot("carla-snapshot://api", payload)
        return payload

    @_recover("record_episode_failed")
    def record_episode(self, output_path: str) -> JsonObject:
        """Start the CARLA recorder at a path."""
        recording = self._adapter.record_episode(Path(output_path))
        payload = recording.to_dict()
        self._snapshots.register_snapshot(
            f"carla-snapshot://recordings/{recording.recording_id}", payload
        )
        return payload

    @_recover("stop_recording_failed")
    def stop_recording(self) -> JsonObject:
        """Stop the active CARLA recorder."""
        recording = self._adapter.stop_recording()
        payload = recording.to_dict()
        self._snapshots.register_snapshot(
            f"carla-snapshot://recordings/{recording.recording_id}", payload
        )
        return payload

    def export_evidence_packet(self, output_dir: str) -> JsonObject:
        """Export a compact evidence manifest from script-created snapshots."""
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        snapshot_uris = self._snapshots.snapshot_uris()
        packet_id = "evidence-001"
        manifest_path = directory / f"{packet_id}.json"
        manifest = {"packet_id": packet_id, "snapshots": list(snapshot_uris)}
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        payload = {
            "packet_id": packet_id,
            "manifest_path": str(manifest_path),
            "snapshot_uri": f"carla-snapshot://evidence/{packet_id}",
            "snapshots": list(snapshot_uris),
        }
        self._snapshots.register_snapshot(str(payload["snapshot_uri"]), payload)
        return payload

    def wait(self, seconds: float) -> JsonObject:
        """Sleep inside the sandbox while CARLA async mode advances."""
        bounded_seconds = min(max(seconds, 0.0), 60.0)
        time.sleep(bounded_seconds)
        return {"waited_seconds": bounded_seconds}


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


def _sensor_blueprint(kind: str) -> str:
    """Resolve a friendly sensor kind to a CARLA blueprint id."""
    if kind.startswith("sensor."):
        return kind
    return _SENSOR_BLUEPRINTS[kind]


def _zero_transform() -> dict[str, object]:
    """Return a zero-relative transform payload."""
    return {
        "location": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
    }


def _is_script_method(api: CarlaScriptApi, name: str) -> bool:
    """Return whether a public callable belongs in the runtime API catalog."""
    value = getattr(api, name)
    return callable(value) and not name.startswith("_")


def _method_description(method: object) -> dict[str, object]:
    """Return a compact method description."""
    doc = getattr(method, "__doc__", "") or ""
    typed_method = cast("Callable[..., object]", method)
    return {
        "signature": str(signature(typed_method)),
        "doc": doc.strip().splitlines()[0] if doc.strip() else "",
    }
