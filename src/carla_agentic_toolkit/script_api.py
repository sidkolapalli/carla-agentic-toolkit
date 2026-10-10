"""Curated CARLA API exposed to sandboxed agent scripts.

This facade is the sole application path between agent scripts and the CARLA
adapter/runtime. CARLA-backed methods share one recoverable-error policy and
publish successful values as run-local snapshots.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from carla_agentic_toolkit.actor_registry import ActorRegistry, actor_registry_path
from carla_agentic_toolkit.api_discovery import method_catalog
from carla_agentic_toolkit.errors import (
    CarlaAdapterError,
)
from carla_agentic_toolkit.evidence import export_evidence_packet as export_evidence
from carla_agentic_toolkit.ownership import (
    RunOwnership,
    cleanup_owned_actors,
    payload_actor_ids,
    release_batch_destroyed,
    release_destroyed,
)
from carla_agentic_toolkit.ownership_release import release_controller_destroyed
from carla_agentic_toolkit.rpc_timeouts import MAP_FAILURE_HINT
from carla_agentic_toolkit.script_operations import recover as _recover
from carla_agentic_toolkit.script_ownership_operations import ScriptOwnershipOperations
from carla_agentic_toolkit.tool_inputs import (
    parse_autopilot_request,
    parse_camera_attach_request,
    parse_location,
    parse_spawn_requests,
    parse_traffic_controller_start_request,
    parse_traffic_density_request,
    parse_traffic_manager_request,
    parse_traffic_population_request,
    parse_traffic_vehicle_path_request,
    parse_transform,
    parse_vehicle_behavior_request,
    sensor_blueprint,
    zero_transform,
)
from carla_agentic_toolkit.traffic_controller_service import (
    require_async_density_mode,
)

if TYPE_CHECKING:
    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.models import JsonObject, SpawnResult
    from carla_agentic_toolkit.snapshots import RunSnapshots


class CarlaScriptApi(ScriptOwnershipOperations):
    """High-level CARLA operations callable from a sandboxed script."""

    def __init__(
        self,
        adapter: PythonCarlaAdapter,
        snapshots: RunSnapshots,
        actor_registry: ActorRegistry | None = None,
        ownership: RunOwnership | None = None,
    ) -> None:
        """Bind the API to a CARLA adapter and run-local snapshots."""
        self._adapter = adapter
        self._snapshots = snapshots
        self._actor_registry = actor_registry
        self._initialize_ownership(adapter, ownership)

    # Connection and world lifecycle.
    @_recover("carla_connection_error", endpoint=True)
    def health_check(self) -> JsonObject:
        """Return CARLA connection health."""
        payload = self._adapter.health_check().to_dict()
        return self._snapshot("carla-snapshot://session/status", payload)

    @_recover("world_state_failed")
    def get_world_state(self) -> JsonObject:
        """Return the current CARLA world state."""
        payload = self._adapter.get_world_state().to_dict()
        return self._snapshot("carla-snapshot://world/current", payload)

    @_recover("list_worlds_failed")
    def list_worlds(self) -> JsonObject:
        """Return available CARLA maps."""
        payload = {"worlds": sorted(self._adapter.list_worlds())}
        return self._snapshot("carla-snapshot://worlds", payload)

    @_recover("load_world_failed", retryable=False, hint=MAP_FAILURE_HINT)
    def load_world(self, map_name: str, *, reset_settings: bool = True) -> JsonObject:
        """Load a CARLA map; reset_settings=True matches CARLA's default."""
        self._require_completed_creations()
        previous_mode = self._current_synchronous_mode()
        payload = self._adapter.load_world(map_name, reset_settings=reset_settings).to_dict()
        return self._replaced_world(self._mode_change(payload, previous_mode=previous_mode))

    @_recover("tick_failed")
    def tick(self) -> JsonObject:
        """Advance the simulation by one frame."""
        payload = {"frame": self._adapter.tick()}
        return self._snapshot("carla-snapshot://session/last-tick", payload)

    @_recover("tick_failed")
    def tick_n(self, count: int) -> JsonObject:
        """Advance the simulation by several frames inside the sandbox."""
        self._adapter.require_sync_tick()
        frames = [self._adapter.tick() for _ in range(max(count, 0))]
        if frames:
            self._snapshots.register_snapshot(
                "carla-snapshot://session/last-tick", {"frame": frames[-1]}
            )
        return {"frames": frames, "count": len(frames)}

    # Actor discovery, creation, naming, and cleanup.
    @_recover("list_blueprints_failed")
    def list_blueprints(self, filter_pattern: str = "*") -> JsonObject:
        """List actor blueprints matching a wildcard filter."""
        payload = {
            "blueprints": [item.to_dict() for item in self._adapter.list_blueprints(filter_pattern)]
        }
        return self._snapshot(f"carla-snapshot://blueprints/{filter_pattern}", payload)

    @_recover("spawn_actor_batch_failed")
    def spawn_actor_batch(self, requests: list[dict[str, object]]) -> JsonObject:
        """Spawn actors from JSON-compatible spawn requests."""
        parsed = parse_spawn_requests(requests)
        self._prepare_owned_creation()
        results = self._adapter.spawn_actor_batch(parsed)
        self._track_owned(result.actor_id for result in results if result.actor_id is not None)
        return self._snapshot("carla-snapshot://actors", _spawn_batch_payload(results))

    @_recover("list_actors_failed")
    def list_actors(self, filter_pattern: str = "*") -> JsonObject:
        """List current actors matching a wildcard filter."""
        payload = {"actors": [item.to_dict() for item in self._adapter.list_actors(filter_pattern)]}
        return self._snapshot("carla-snapshot://actors/current", payload)

    @_recover("destroy_actors_failed")
    def destroy_actors(self, actor_ids: list[int]) -> JsonObject:
        """Destroy explicit actors by ID."""
        results = self._adapter.destroy_actors(tuple(actor_ids))
        release_destroyed(self._ownership, results)
        payload = {"results": [item.to_dict() for item in results]}
        return self._snapshot("carla-snapshot://actors/destroyed", payload)

    @_recover("name_actor_failed")
    def name_actor(self, name: str, actor_id: int) -> JsonObject:
        """Assign a conversational name to one live CARLA actor."""
        live_ids = {actor.actor_id for actor in self._adapter.list_actors("*")}
        return self._named_actor_result(self._registry().name_actor(name, actor_id, live_ids))

    @_recover("resolve_actor_failed")
    def resolve_actor(self, name: str) -> JsonObject:
        """Resolve a conversational actor name and verify that it is still live."""
        live_ids = {actor.actor_id for actor in self._adapter.list_actors("*")}
        return self._named_actor_result(self._registry().resolve_actor(name, live_ids))

    @_recover("list_named_actors_failed")
    def list_named_actors(self) -> JsonObject:
        """List persistent actor names for this CARLA endpoint."""
        return self._named_actor_result(self._registry().list_actors())

    @_recover("forget_actor_failed")
    def forget_actor(self, name: str) -> JsonObject:
        """Forget one conversational actor name without destroying the actor."""
        return self._named_actor_result(self._registry().forget_actor(name))

    def _registry(self) -> ActorRegistry:
        if self._actor_registry is None:
            path = actor_registry_path(Path.cwd(), self._adapter.host, self._adapter.port)
            self._actor_registry = ActorRegistry(path)
        return self._actor_registry

    def _named_actor_result(self, result: JsonObject) -> JsonObject:
        self._snapshots.register_snapshot(
            "carla-snapshot://actors/named", self._registry().list_actors()
        )
        return result

    # Traffic Manager workflows.
    @_recover("populate_traffic_failed")
    def populate_traffic(self, request: dict[str, object]) -> JsonObject:
        """Spawn Traffic Manager-controlled vehicles in an asynchronous world only."""
        parsed = parse_traffic_population_request(request)
        self._require_async_traffic_controller()
        self._prepare_owned_creation()
        try:
            population = self._adapter.populate_traffic(request=parsed)
        except CarlaAdapterError as exc:
            self._track_owned(payload_actor_ids(exc.details, "remaining_actor_ids"))
            raise
        self._track_owned(population.actor_ids)
        payload = population.to_dict()
        self._snapshots.register_snapshot("carla-snapshot://traffic/population", payload)
        self._snapshots.register_snapshot(
            "carla-snapshot://world/current", population.world_state.to_dict()
        )
        return payload

    @_recover("set_autopilot_failed")
    def set_autopilot(self, request: dict[str, object]) -> JsonObject:
        """Toggle Traffic Manager autopilot in an asynchronous world only."""
        result = self._adapter.set_autopilot(request=parse_autopilot_request(request))
        payload = result.to_dict()
        self._snapshots.register_snapshot("carla-snapshot://traffic/autopilot", payload)
        self._snapshots.register_snapshot(
            "carla-snapshot://world/current", result.world_state.to_dict()
        )
        return payload

    @_recover("configure_traffic_manager_failed")
    def configure_traffic_manager(self, request: dict[str, object]) -> JsonObject:
        """Configure toolkit-owned asynchronous TM globals; seeds do not ensure reproducibility."""
        payload = self._adapter.configure_traffic_manager(
            request=parse_traffic_manager_request(request)
        ).to_dict()
        return self._snapshot("carla-snapshot://traffic/manager", payload)

    @_recover("tune_traffic_vehicle_failed")
    def tune_traffic_vehicle(
        self,
        actor_id: int,
        settings: dict[str, object],
        traffic_manager_port: int = 8000,
    ) -> JsonObject:
        """Set per-vehicle Traffic Manager behavior in an asynchronous world only."""
        return self._adapter.tune_traffic_vehicle(
            actor_id=actor_id,
            traffic_manager_port=traffic_manager_port,
            settings=settings,
        )

    @_recover("set_traffic_vehicle_path_failed")
    def set_traffic_vehicle_path(
        self,
        actor_id: int,
        request: dict[str, object],
    ) -> JsonObject:
        """Upload path locations or route strings for one vehicle in an asynchronous world only."""
        parsed = parse_traffic_vehicle_path_request(actor_id, request)
        return self._adapter.set_traffic_vehicle_path(request=parsed)

    @_recover("start_traffic_controller_failed")
    def start_traffic_controller(self, request: dict[str, object]) -> JsonObject:
        """Start an in-script Traffic Manager controller in an asynchronous world only."""
        self._require_async_traffic_controller()
        self._prepare_owned_controller()
        parsed = parse_traffic_controller_start_request(
            request,
            host=self._adapter.host,
            port=self._adapter.port,
            timeout_seconds=self._adapter.timeout,
        )
        status = self._traffic_controller.start(parsed)
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
        """Converge in-script traffic to a requested density in an asynchronous world only."""
        self._require_async_traffic_controller()
        self._prepare_owned_controller()
        parsed = parse_traffic_density_request(request)
        status = self._traffic_controller.set_density(parsed)
        return self._controller_status_payload(status.to_dict())

    def _require_async_traffic_controller(self) -> None:
        """Reject unsupported tick ownership before starting background mutations."""
        require_async_density_mode(
            synchronous_mode=self._adapter.get_synchronous_mode(),
        )

    @_recover("set_vehicle_behavior_failed")
    def set_vehicle_behavior(self, request: dict[str, object]) -> JsonObject:
        """Apply a behavior profile to explicit vehicle actors in an asynchronous world only."""
        self._require_async_traffic_controller()
        payload = self._traffic_controller.set_vehicle_behavior(
            parse_vehicle_behavior_request(request)
        ).to_dict()
        return self._snapshot("carla-snapshot://traffic/behaviors", payload)

    def _controller_status_payload(self, payload: JsonObject) -> JsonObject:
        return self._snapshot("carla-snapshot://traffic/controller", payload)

    # Sensors and durable captures.
    @_recover("attach_camera_failed")
    def attach_camera(self, request: dict[str, object]) -> JsonObject:
        """Attach a camera sensor."""
        parsed = parse_camera_attach_request(request)
        self._prepare_owned_creation()
        sensor = self._adapter.attach_camera(request=parsed)
        self._track_owned((sensor.sensor_id,))
        return self._snapshot(f"carla-snapshot://sensors/{sensor.sensor_id}", sensor.to_dict())

    @_recover("capture_sensor_frame_failed")
    def capture_sensor_frame(
        self,
        sensor_id: int,
        output_path: str,
        *,
        publish: bool = False,
    ) -> JsonObject:
        """Capture one sensor frame to disk and optionally publish it through MCP."""
        capture = self._adapter.capture_sensor_frame(
            sensor_id=sensor_id,
            output_path=Path(output_path),
        )
        payload = capture.to_dict()
        if publish:
            payload["publish"] = True
        return self._snapshot(f"carla-snapshot://captures/{capture.capture_id}", payload)

    @_recover("attach_sensor_failed")
    def attach_sensor(
        self,
        kind: str,
        parent_id: int | None,
        transform: dict[str, object],
        attributes: dict[str, str] | None = None,
    ) -> JsonObject:
        """Attach any supported CARLA sensor kind to an actor."""
        blueprint_id = sensor_blueprint(kind)
        parsed_transform = parse_transform(transform)
        self._prepare_owned_creation()
        sensor = self._adapter.attach_sensor(
            blueprint_id=blueprint_id,
            transform=parsed_transform,
            attributes=attributes or {},
            parent_actor_id=parent_id,
        )
        self._track_owned((sensor.sensor_id,))
        return self._snapshot(f"carla-snapshot://sensors/{sensor.sensor_id}", sensor.to_dict())

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
        return self._snapshot(f"carla-snapshot://sensors/{sensor_id}/stream", payload)

    @_recover("subscribe_sensor_failed")
    def subscribe_sensor(
        self, sensor_id: int, *, event_sensor: bool | None = None, capacity: int = 32
    ) -> JsonObject:
        """Listen before the owner ticks; event sensors never require an event each frame."""
        return self._adapter.subscribe_sensor(
            sensor_id, event_sensor=event_sensor, capacity=capacity
        )

    @_recover("drain_sensor_failed")
    def drain_sensor(
        self,
        sensor_id: int,
        frame: int,
        *,
        timeout_seconds: float = 0.0,
        output_dir: str | None = None,
    ) -> JsonObject:
        """Drain by owner frame, exposing missing/late/dropped samples without ticking."""
        payload = self._adapter.drain_sensor(
            sensor_id,
            frame,
            timeout_seconds=timeout_seconds,
            output_dir=Path(output_dir) if output_dir is not None else None,
        )
        return self._snapshot(f"carla-snapshot://sensors/{sensor_id}/drain", payload)

    @_recover("close_sensor_subscription_failed")
    def close_sensor_subscription(self, sensor_id: int) -> JsonObject:
        """Stop one subscription when the owner finishes reading its sensor."""
        return self._adapter.close_sensor_subscription(sensor_id)

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
            transform=zero_transform(),
            attributes=attributes,
        )

    @_recover("detach_sensor_failed")
    def detach_sensor(self, sensor_id: int) -> JsonObject:
        """Stop and destroy a sensor actor."""
        world_id = self._adapter.get_world_identity() if self._ownership is not None else None
        payload = self._adapter.detach_sensor(sensor_id)
        if payload.get("destroyed") is True or payload.get("error") == "Actor was not found.":
            release_controller_destroyed(self._adapter, self._ownership, (sensor_id,), world_id)
        return self._snapshot(f"carla-snapshot://sensors/{sensor_id}/detached", payload)

    # Navigation and map semantics.
    @_recover("get_spawn_points_failed")
    def get_spawn_points(self) -> JsonObject:
        """Return legal vehicle spawn transforms from the current map."""
        payload = self._adapter.get_spawn_points()
        return self._snapshot("carla-snapshot://map/spawn-points", payload)

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
        return self._snapshot("carla-snapshot://route/latest", payload)

    @_recover("get_topology_failed")
    def get_topology(self, max_segments: int = 200) -> JsonObject:
        """Return a compact road topology graph."""
        payload = self._adapter.get_topology(max_segments=max_segments)
        return self._snapshot("carla-snapshot://map/topology", payload)

    @_recover("get_landmarks_failed")
    def get_landmarks(
        self,
        max_count: int = 200,
        *,
        landmark_type: str | None = None,
        landmark_id: str | None = None,
    ) -> JsonObject:
        """Return landmarks, optionally filtered by type or OpenDRIVE ID."""
        return self._snapshot(
            "carla-snapshot://map/landmarks",
            self._adapter.get_landmarks(
                max_count=max_count,
                landmark_type=landmark_type,
                landmark_id=landmark_id,
            ),
        )

    @_recover("get_environment_objects_failed")
    def get_environment_objects(
        self,
        label: str = "Any",
        max_count: int = 200,
        *,
        include_level_bounds: bool = False,
    ) -> JsonObject:
        """Return bounded objects and optional level bounds by semantic label."""
        return self._snapshot(
            f"carla-snapshot://environment/{label}",
            self._adapter.get_environment_objects(
                label=label,
                max_count=max_count,
                include_level_bounds=include_level_bounds,
            ),
        )

    @_recover("enable_environment_objects_failed")
    def enable_environment_objects(
        self,
        object_ids: list[int],
        *,
        enabled: bool,
    ) -> JsonObject:
        """Enable or disable explicit static environment object IDs."""
        return self._adapter.enable_environment_objects(
            object_ids=tuple(object_ids), enabled=enabled
        )

    @_recover("set_map_layer_failed")
    def set_map_layer(self, layer: str, *, loaded: bool) -> JsonObject:
        """Load or unload one runtime MapLayer by name."""
        return self._adapter.set_map_layer(layer=layer, loaded=loaded)

    @_recover("generate_opendrive_world_failed", retryable=False, hint=MAP_FAILURE_HINT)
    def generate_opendrive_world(
        self,
        opendrive: str,
        parameters: dict[str, object] | None = None,
        *,
        reset_settings: bool = True,
    ) -> JsonObject:
        """Replace the world from bounded OpenDRIVE text and known parameters."""
        self._require_completed_creations()
        previous_mode = self._current_synchronous_mode()
        payload = self._adapter.generate_opendrive_world(
            opendrive=opendrive,
            parameters=parameters or {},
            reset_settings=reset_settings,
        )
        return self._replaced_world(self._mode_change(payload, previous_mode=previous_mode))

    # Actor and vehicle physics.
    @_recover("configure_actor_physics_failed")
    def configure_actor_physics(
        self,
        actor_id: int,
        *,
        simulate_physics: bool | None = None,
        gravity: bool | None = None,
    ) -> JsonObject:
        """Toggle physics simulation and gravity for one actor."""
        return self._adapter.configure_actor_physics(
            actor_id=actor_id,
            simulate_physics=simulate_physics,
            gravity=gravity,
        )

    @_recover("apply_actor_physics_failed")
    def apply_actor_physics(
        self,
        actor_id: int,
        action: str,
        vector: dict[str, object],
    ) -> JsonObject:
        """Apply impulse, force, torque, or target angular velocity to an actor."""
        return self._adapter.apply_actor_physics(
            actor_id=actor_id,
            action=action,
            vector=parse_location(vector),
        )

    @_recover("get_vehicle_physics_failed")
    def get_vehicle_physics(self, actor_id: int) -> JsonObject:
        """Return a bounded common subset of vehicle physics control."""
        payload = self._adapter.get_vehicle_physics(actor_id)
        return self._snapshot(f"carla-snapshot://actors/{actor_id}/physics", payload)

    @_recover("update_vehicle_physics_failed")
    def update_vehicle_physics(
        self,
        actor_id: int,
        changes: dict[str, object],
    ) -> JsonObject:
        """Update supported scalar and center-of-mass vehicle physics fields."""
        payload = self._adapter.update_vehicle_physics(actor_id=actor_id, changes=changes)
        return self._snapshot(f"carla-snapshot://actors/{actor_id}/physics", payload)

    # Direct vehicle and walker control.
    @_recover("apply_vehicle_control_failed")
    def apply_vehicle_control(self, actor_id: int, **control: object) -> JsonObject:
        """Apply direct throttle, steer, brake, and gear control to a vehicle."""
        return self._adapter.apply_vehicle_control(actor_id=actor_id, control=dict(control))

    @_recover("get_vehicle_telemetry_failed")
    def get_vehicle_telemetry(self, actor_id: int) -> JsonObject:
        """Return transform, speed, control, and traffic-light telemetry."""
        payload = self._adapter.get_vehicle_telemetry(actor_id)
        return self._snapshot(f"carla-snapshot://actors/{actor_id}/telemetry", payload)

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
        self._prepare_owned_creation()
        payload = self._adapter.spawn_walkers(count=count, speed=speed, seed=seed)
        self._track_owned(payload_actor_ids(payload, "walker_ids", "controller_ids"))
        return self._snapshot("carla-snapshot://walkers/latest", payload)

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

    # Visible scene, spectator, and weather control.
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

    @_recover("watch_actor_failed")
    def watch_actor(self, actor_id: int, seconds: float = 8.0) -> JsonObject:
        """Show an actor through a bounded simulator-tick chase camera."""
        return self._adapter.watch_actor(
            actor_id=actor_id, seconds=seconds, distance=8.0, height=4.0
        )

    @_recover("save_screenshot_failed")
    def save_screenshot(
        self,
        output_path: str,
        attributes: dict[str, str] | None = None,
        *,
        publish: bool = False,
    ) -> JsonObject:
        """Capture a spectator RGB frame and optionally publish it through MCP."""
        self._prepare_owned_creation()
        payload = self._adapter.save_screenshot(
            output_path=Path(output_path),
            attributes=attributes or {"image_size_x": "1280", "image_size_y": "720"},
        )
        if publish:
            payload["publish"] = True
        capture_id = str(payload.get("capture_id", "screenshot"))
        return self._snapshot(f"carla-snapshot://captures/{capture_id}", payload)

    @_recover("get_weather_failed")
    def get_weather(self) -> JsonObject:
        """Return weather parameters when supported."""
        return self._adapter.get_weather()

    @_recover("set_weather_failed")
    def set_weather(self, parameters: dict[str, float]) -> JsonObject:
        """Set weather parameters when supported by this CARLA build."""
        return self._adapter.set_weather(parameters)

    # Replay, batch operations, and run cleanup.
    @_recover("replay_recording_failed")
    def replay_recording(  # noqa: PLR0913 -- Preserve the existing public recorder arguments.
        self,
        path: str,
        start: float = 0.0,
        duration: float = 0.0,
        follow_id: int = 0,
        *,
        replay_sensors: bool = False,
        do_tick: bool = True,
    ) -> JsonObject:
        """Replay a CARLA recorder file."""
        return self._adapter.replay_recording(
            path=Path(path),
            start=start,
            duration=duration,
            follow_id=follow_id,
            replay_sensors=replay_sensors,
            do_tick=do_tick,
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

    @_recover("reload_world_failed", retryable=False, hint=MAP_FAILURE_HINT)
    def reload_world(self, *, reset_settings: bool) -> JsonObject:
        """Reload with required reset_settings; False deliberately keeps current settings."""
        self._require_completed_creations()
        previous_mode = self._current_synchronous_mode()
        payload = self._adapter.reload_world(reset_settings=reset_settings)
        return self._replaced_world(self._mode_change(payload, previous_mode=previous_mode))

    @_recover("apply_batch_failed")
    def apply_batch(
        self, commands: list[dict[str, object]], *, do_tick: bool = False
    ) -> JsonObject:
        """Apply response batches with do_tick=False, matching CARLA apply_batch_sync."""
        previous_mode = self._current_synchronous_mode()
        payload = self._adapter.apply_batch(commands, do_tick=do_tick)
        release_batch_destroyed(self._ownership, payload, commands=commands)
        current_mode = self._current_synchronous_mode()
        return payload | {
            "synchronous_mode": current_mode,
            "synchronous_mode_changed": current_mode is not previous_mode,
        }

    @_recover("cleanup_owned_actors_failed")
    def cleanup_owned_actors(self) -> JsonObject:
        """Destroy every actor created by this script execution."""
        return cleanup_owned_actors(self._adapter, self._ownership)

    @_recover("list_capabilities_failed")
    def list_capabilities(self) -> JsonObject:
        """Return live CARLA version and feature probes."""
        return self._adapter.list_capabilities()

    def describe_api(self) -> JsonObject:
        """Return the script API method catalog for runtime discovery."""
        payload = {"methods": method_catalog(self)}
        return self._snapshot("carla-snapshot://api", payload)

    # Recording, evidence, and bounded pacing.
    @_recover("record_episode_failed")
    def record_episode(self, output_path: str) -> JsonObject:
        """Start the CARLA recorder at a path."""
        recording = self._adapter.record_episode(Path(output_path))
        return self._snapshot(
            f"carla-snapshot://recordings/{recording.recording_id}", recording.to_dict()
        )

    @_recover("stop_recording_failed")
    def stop_recording(self) -> JsonObject:
        """Stop the active CARLA recorder."""
        recording = self._adapter.stop_recording()
        return self._snapshot(
            f"carla-snapshot://recordings/{recording.recording_id}", recording.to_dict()
        )

    def export_evidence_packet(self, output_dir: str) -> JsonObject:
        """Export a compact evidence manifest from script-created snapshots."""
        return export_evidence(self._snapshots, output_dir)

    @_recover("wait_failed")
    def wait(self, seconds: float) -> JsonObject:
        """Observe asynchronous simulator frames for at most sixty seconds."""
        return {"waited_seconds": self._adapter.wait(seconds)}


def _spawn_batch_payload(results: tuple[SpawnResult, ...]) -> JsonObject:
    """Flag partial failure without changing the all-success result shape."""
    payload: JsonObject = {"results": [item.to_dict() for item in results]}
    if any(item.error is not None for item in results):
        payload["ok"] = False
    return payload
