"""Curated CARLA API exposed to sandboxed agent scripts.

This object is the single code API a code-execution MCP client composes against.
Each method mirrors a discrete CARLA tool and returns the same JSON-compatible
structured content, so scripts and direct tool calls share behavior and publish
the same session resources.
"""

from __future__ import annotations

import time
from inspect import signature
from pathlib import Path
from typing import TYPE_CHECKING, cast

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
from carla_mcp.tools.actor_management import destroy_actors, list_actors
from carla_mcp.tools.actors import list_blueprints, spawn_actor_batch
from carla_mcp.tools.diagnostics import health_check
from carla_mcp.tools.evidence import export_evidence_packet
from carla_mcp.tools.recorder import record_episode, stop_recording
from carla_mcp.tools.sensors import attach_camera, capture_sensor_frame
from carla_mcp.tools.traffic import configure_traffic_manager, populate_traffic, set_autopilot
from carla_mcp.tools.traffic_controller import (
    set_traffic_density,
    set_vehicle_behavior,
    start_traffic_controller,
    stop_traffic_controller,
    traffic_controller_status,
)
from carla_mcp.tools.world import get_world_state, list_worlds, load_world, set_sync_mode, tick
from carla_mcp.traffic_controller_service import InProcessTrafficControllerService

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_mcp.adapter import CarlaAdapter
    from carla_mcp.models import JsonObject
    from carla_mcp.session import CarlaSession


class CarlaScriptApi:
    """High-level CARLA operations callable from a sandboxed script."""

    def __init__(self, adapter: CarlaAdapter, session: CarlaSession) -> None:
        """Bind the API to a CARLA adapter and session."""
        self._adapter = adapter
        self._session = session
        self._traffic_controller = InProcessTrafficControllerService()

    def health_check(self) -> JsonObject:
        """Return CARLA connection health."""
        return health_check(adapter=self._adapter, session=self._session).structured_content

    def get_world_state(self) -> JsonObject:
        """Return the current CARLA world state."""
        return get_world_state(adapter=self._adapter, session=self._session).structured_content

    def list_worlds(self) -> JsonObject:
        """Return available CARLA maps."""
        return list_worlds(adapter=self._adapter, session=self._session).structured_content

    def load_world(self, map_name: str) -> JsonObject:
        """Load a CARLA map by name."""
        return load_world(
            adapter=self._adapter,
            session=self._session,
            map_name=map_name,
        ).structured_content

    def set_sync_mode(
        self,
        *,
        enabled: bool,
        fixed_delta_seconds: float | None = 0.05,
    ) -> JsonObject:
        """Configure synchronous mode and fixed timestep."""
        return set_sync_mode(
            adapter=self._adapter,
            session=self._session,
            enabled=enabled,
            fixed_delta_seconds=fixed_delta_seconds,
        ).structured_content

    def tick(self) -> JsonObject:
        """Advance the simulation by one frame."""
        return tick(adapter=self._adapter, session=self._session).structured_content

    def tick_n(self, count: int) -> JsonObject:
        """Advance the simulation by several frames inside the sandbox."""
        frames = [self.tick().get("frame") for _ in range(max(count, 0))]
        return {"frames": frames, "count": len(frames)}

    def list_blueprints(self, filter_pattern: str = "*") -> JsonObject:
        """List actor blueprints matching a wildcard filter."""
        return list_blueprints(
            adapter=self._adapter,
            session=self._session,
            filter_pattern=filter_pattern,
        ).structured_content

    def spawn_actor_batch(self, requests: list[dict[str, object]]) -> JsonObject:
        """Spawn actors from JSON-compatible spawn requests."""
        return spawn_actor_batch(
            adapter=self._adapter,
            session=self._session,
            requests=parse_spawn_requests(requests),
        ).structured_content

    def list_actors(self, filter_pattern: str = "*") -> JsonObject:
        """List current actors matching a wildcard filter."""
        return list_actors(
            adapter=self._adapter,
            session=self._session,
            filter_pattern=filter_pattern,
        ).structured_content

    def destroy_actors(self, actor_ids: list[int]) -> JsonObject:
        """Destroy explicit actors by ID."""
        return destroy_actors(
            adapter=self._adapter,
            session=self._session,
            actor_ids=tuple(actor_ids),
        ).structured_content

    def populate_traffic(self, request: dict[str, object]) -> JsonObject:
        """Spawn Traffic Manager-controlled vehicles."""
        return populate_traffic(
            adapter=self._adapter,
            session=self._session,
            request=parse_traffic_population_request(request),
        ).structured_content

    def set_autopilot(self, request: dict[str, object]) -> JsonObject:
        """Toggle Traffic Manager autopilot for existing vehicles."""
        return set_autopilot(
            adapter=self._adapter,
            session=self._session,
            request=parse_autopilot_request(request),
        ).structured_content

    def configure_traffic_manager(self, request: dict[str, object]) -> JsonObject:
        """Configure global Traffic Manager behavior."""
        return configure_traffic_manager(
            adapter=self._adapter,
            session=self._session,
            request=parse_traffic_manager_request(request),
        ).structured_content

    def start_traffic_controller(self, request: dict[str, object]) -> JsonObject:
        """Start an in-script persistent Traffic Manager controller."""
        return start_traffic_controller(
            service=self._traffic_controller,
            session=self._session,
            request=parse_traffic_controller_start_request(
                request,
                host=self._adapter.host,
                port=self._adapter.port,
                timeout_seconds=self._adapter.timeout,
            ),
        ).structured_content

    def stop_traffic_controller(self) -> JsonObject:
        """Stop the in-script Traffic Manager controller."""
        return stop_traffic_controller(
            service=self._traffic_controller,
            session=self._session,
        ).structured_content

    def traffic_controller_status(self) -> JsonObject:
        """Return in-script Traffic Manager controller status."""
        return traffic_controller_status(
            service=self._traffic_controller,
            session=self._session,
        ).structured_content

    def set_traffic_density(self, request: dict[str, object]) -> JsonObject:
        """Converge in-script traffic to a requested density."""
        return set_traffic_density(
            service=self._traffic_controller,
            session=self._session,
            request=parse_traffic_density_request(request),
        ).structured_content

    def set_vehicle_behavior(self, request: dict[str, object]) -> JsonObject:
        """Apply a behavior profile to explicit vehicle actors."""
        return set_vehicle_behavior(
            service=self._traffic_controller,
            session=self._session,
            request=parse_vehicle_behavior_request(request),
        ).structured_content

    def attach_camera(self, request: dict[str, object]) -> JsonObject:
        """Attach a camera sensor."""
        return attach_camera(
            adapter=self._adapter,
            session=self._session,
            request=parse_camera_attach_request(request),
        ).structured_content

    def capture_sensor_frame(self, sensor_id: int, output_path: str) -> JsonObject:
        """Capture one sensor frame to disk."""
        return capture_sensor_frame(
            adapter=self._adapter,
            session=self._session,
            sensor_id=sensor_id,
            output_path=Path(output_path),
        ).structured_content

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
        self._session.register_resource(f"carla://sensors/{sensor.sensor_id}", payload)
        return payload

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
        self._session.register_resource(f"carla://sensors/{sensor_id}/stream", payload)
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

    def detach_sensor(self, sensor_id: int) -> JsonObject:
        """Stop and destroy a sensor actor."""
        payload = self._adapter.detach_sensor(sensor_id)
        self._session.register_resource(f"carla://sensors/{sensor_id}/detached", payload)
        return payload

    def get_spawn_points(self) -> JsonObject:
        """Return legal vehicle spawn transforms from the current map."""
        payload = self._adapter.get_spawn_points()
        self._session.register_resource("carla://map/spawn-points", payload)
        return payload

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
        self._session.register_resource("carla://route/latest", payload)
        return payload

    def get_topology(self, max_segments: int = 200) -> JsonObject:
        """Return a compact road topology graph."""
        payload = self._adapter.get_topology(max_segments=max_segments)
        self._session.register_resource("carla://map/topology", payload)
        return payload

    def get_landmarks(self, max_count: int = 200) -> JsonObject:
        """Return map landmarks when supported by the loaded map."""
        payload = self._adapter.get_landmarks(max_count=max_count)
        self._session.register_resource("carla://map/landmarks", payload)
        return payload

    def apply_vehicle_control(self, actor_id: int, **control: object) -> JsonObject:
        """Apply direct throttle, steer, brake, and gear control to a vehicle."""
        return self._adapter.apply_vehicle_control(actor_id=actor_id, control=dict(control))

    def get_vehicle_telemetry(self, actor_id: int) -> JsonObject:
        """Return transform, speed, control, and traffic-light telemetry."""
        payload = self._adapter.get_vehicle_telemetry(actor_id)
        self._session.register_resource(f"carla://actors/{actor_id}/telemetry", payload)
        return payload

    def set_actor_transform(self, actor_id: int, transform: dict[str, object]) -> JsonObject:
        """Teleport an actor to a scenario start transform."""
        return self._adapter.set_actor_transform(
            actor_id=actor_id,
            transform=parse_transform(transform),
        )

    def set_vehicle_lights(self, actor_id: int, state: str | int) -> JsonObject:
        """Set vehicle light state by integer mask or pipe-separated names."""
        return self._adapter.set_vehicle_lights(actor_id=actor_id, state=state)

    def set_target_velocity(self, actor_id: int, velocity: dict[str, object]) -> JsonObject:
        """Set an actor target velocity vector."""
        return self._adapter.set_target_velocity(
            actor_id=actor_id,
            velocity=parse_location(velocity),
        )

    def spawn_walkers(
        self,
        count: int,
        speed: float = 1.4,
        seed: int | None = None,
    ) -> JsonObject:
        """Spawn pedestrians and AI walker controllers."""
        payload = self._adapter.spawn_walkers(count=count, speed=speed, seed=seed)
        self._session.register_resource("carla://walkers/latest", payload)
        return payload

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

    def freeze_traffic_lights(self, *, enabled: bool) -> JsonObject:
        """Freeze or unfreeze all traffic lights."""
        return self._adapter.freeze_traffic_lights(enabled=enabled)

    def set_traffic_light_state(self, actor_id: int, state: str) -> JsonObject:
        """Set one traffic light state, such as Red, Yellow, or Green."""
        return self._adapter.set_traffic_light_state(actor_id=actor_id, state=state)

    def set_spectator(self, transform: dict[str, object]) -> JsonObject:
        """Move the spectator viewpoint."""
        return self._adapter.set_spectator(parse_transform(transform))

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

    def get_weather(self) -> JsonObject:
        """Return weather parameters when supported."""
        return self._adapter.get_weather()

    def set_weather(self, parameters: dict[str, float]) -> JsonObject:
        """Set weather parameters when supported by this CARLA build."""
        return self._adapter.set_weather(parameters)

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

    def reload_world(self, *, reset_settings: bool = False) -> JsonObject:
        """Reload the current world for a clean scenario reset."""
        payload = self._adapter.reload_world(reset_settings=reset_settings)
        self._session.register_resource("carla://world/current", payload)
        return payload

    def apply_batch(self, commands: list[dict[str, object]]) -> JsonObject:
        """Apply supported bulk CARLA commands, such as destroy_actor."""
        return self._adapter.apply_batch(commands)

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
        self._session.register_resource("carla://api", payload)
        return payload

    def record_episode(self, output_path: str) -> JsonObject:
        """Start the CARLA recorder at a path."""
        return record_episode(
            adapter=self._adapter,
            session=self._session,
            output_path=Path(output_path),
        ).structured_content

    def stop_recording(self) -> JsonObject:
        """Stop the active CARLA recorder."""
        return stop_recording(adapter=self._adapter, session=self._session).structured_content

    def export_evidence_packet(self, output_dir: str) -> JsonObject:
        """Export a compact evidence manifest from script-created resources."""
        return export_evidence_packet(
            session=self._session,
            output_dir=Path(output_dir),
        ).structured_content

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
