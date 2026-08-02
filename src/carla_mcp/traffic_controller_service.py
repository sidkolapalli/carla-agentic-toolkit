"""Persistent Traffic Manager controller service."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from importlib import import_module
from typing import TYPE_CHECKING, cast

from carla_mcp.behavior_profiles import BehaviorProfile, behavior_profile
from carla_mcp.errors import CarlaAdapterError
from carla_mcp.models import (
    DestroyResult,
    TrafficControllerStartRequest,
    TrafficControllerStatus,
    TrafficDensityRequest,
    TrafficManagerRequest,
    TrafficPopulationRequest,
    VehicleBehaviorRequest,
    VehicleBehaviorResult,
)
from carla_mcp.traffic_runtime import (
    configure_traffic_manager,
    populate_traffic_actors,
    traffic_manager,
)

MOVING_SPEED_THRESHOLD_MPS = 0.5

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from carla_mcp.carla_protocols import (
        CarlaActor,
        CarlaClient,
        CarlaTrafficManager,
        CarlaVector,
        CarlaWorld,
    )


@dataclass(frozen=True, slots=True)
class _RuntimeState:
    """Mutable controller state stored behind a lock."""

    request: TrafficControllerStartRequest
    active: bool
    vehicle_count: int = 0
    moving_vehicle_count: int = 0
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class TrafficControllerStep:
    """Result of one Traffic Manager maintenance step."""

    request: TrafficControllerStartRequest
    vehicle_count: int
    moving_vehicle_count: int
    registered_actor_ids: frozenset[int]
    spawned_actor_ids: tuple[int, ...] = ()


class InProcessTrafficControllerService:
    """Persistent Traffic Manager controller held by the MCP server process."""

    def __init__(
        self,
        on_spawn: Callable[[tuple[int, ...]], None] | None = None,
    ) -> None:
        """Create an inactive controller service."""
        self._on_spawn = on_spawn
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = _RuntimeState(request=_default_start_request(), active=False)
        self._behaviors: dict[int, str] = {}
        self._registered_actor_ids: frozenset[int] = frozenset()
        self._configured_density: TrafficDensityRequest | None = None

    def start(self, request: TrafficControllerStartRequest) -> TrafficControllerStatus:
        """Start the persistent controller."""
        self.stop()
        with self._lock:
            self._stop_event = threading.Event()
            self._state = _RuntimeState(request=request, active=True)
            self._registered_actor_ids = frozenset()
            self._configured_density = None
            self._thread = threading.Thread(
                target=self._run,
                name="carla-mcp-traffic-controller",
                daemon=True,
            )
            self._thread.start()
            return self._status_locked()

    def stop(self) -> TrafficControllerStatus:
        """Stop the persistent controller."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            self._stop_event.set()
            thread.join(timeout=5.0)
        with self._lock:
            self._state = replace(self._state, active=False)
            self._thread = None
            return self._status_locked()

    def get_status(self) -> TrafficControllerStatus:
        """Return current controller status."""
        with self._lock:
            active = self._thread is not None and self._thread.is_alive()
            self._state = replace(self._state, active=active)
            return self._status_locked()

    def set_density(self, request: TrafficDensityRequest) -> TrafficControllerStatus:
        """Converge traffic to a target density."""
        with self._lock:
            start_request = replace(self._state.request, density=request)
        if request.reset_existing:
            return self.start(start_request)
        if not self.get_status().active:
            return self.start(start_request)
        with self._lock:
            self._state = replace(self._state, request=start_request, active=True)
            return self._status_locked()

    def set_vehicle_behavior(self, request: VehicleBehaviorRequest) -> VehicleBehaviorResult:
        """Apply a named behavior profile to explicit actor IDs."""
        profile = behavior_profile(request.profile)
        if profile is None:
            msg = f"Unknown behavior profile: {request.profile}."
            raise CarlaAdapterError(msg)
        with self._lock:
            self._behaviors.update(dict.fromkeys(request.actor_ids, request.profile))
        failures = _apply_behavior_once(self._state.request, request)
        return VehicleBehaviorResult(
            actor_ids=tuple(actor_id for actor_id in request.actor_ids if actor_id not in failures),
            profile=request.profile,
            applied_settings=profile.to_settings(),
            failed_applications=tuple(
                DestroyResult(actor_id=actor_id, destroyed=False, error=error)
                for actor_id, error in failures.items()
            ),
        )

    def _run(self) -> None:
        """Run the controller loop until stopped."""
        client: CarlaClient | None = None
        while not self._stop_event.is_set():
            try:
                client = client or _client(self._state.request)
                self._control_once(client)
            except (CarlaAdapterError, RuntimeError, TypeError, ValueError) as exc:
                self._record_error(str(exc))
                client = None
                time.sleep(1.0)

    def _control_once(self, client: CarlaClient) -> None:
        """Run one controller maintenance step."""
        with self._lock:
            request = self._state.request
            behaviors = dict(self._behaviors)
            registered_actor_ids = self._registered_actor_ids
            configure_manager = request.density != self._configured_density
        result = maintain_traffic_once(
            client,
            request,
            behaviors,
            registered_actor_ids,
            configure_manager=configure_manager,
        )
        self._record_step(result)

    def _record_step(self, result: TrafficControllerStep) -> None:
        """Record the completed controller step."""
        if result.spawned_actor_ids and self._on_spawn is not None:
            self._on_spawn(result.spawned_actor_ids)
        with self._lock:
            self._state = replace(
                self._state,
                request=result.request,
                active=True,
                vehicle_count=result.vehicle_count,
                moving_vehicle_count=result.moving_vehicle_count,
                last_error=None,
            )
            self._registered_actor_ids = result.registered_actor_ids
            self._configured_density = result.request.density

    def _record_error(self, message: str) -> None:
        """Record the latest controller error."""
        with self._lock:
            self._state = replace(self._state, last_error=message)

    def _status_locked(self) -> TrafficControllerStatus:
        """Build a status from locked state."""
        request = self._state.request
        return TrafficControllerStatus(
            active=self._state.active,
            host=request.host,
            port=request.port,
            traffic_manager_port=request.density.traffic_manager_port,
            target_vehicle_count=request.density.vehicle_count,
            vehicle_count=self._state.vehicle_count,
            moving_vehicle_count=self._state.moving_vehicle_count,
            last_error=self._state.last_error,
        )


def _default_start_request() -> TrafficControllerStartRequest:
    """Return default controller start settings."""
    return TrafficControllerStartRequest(density=TrafficDensityRequest(vehicle_count=0))


def _client(request: TrafficControllerStartRequest) -> CarlaClient:
    """Create a CARLA client for the controller."""
    module = import_module("carla")
    factory = getattr(module, "Client", None)
    if not callable(factory):
        msg = "Imported carla module does not expose Client."
        raise CarlaAdapterError(msg)
    client = factory(request.host, request.port)
    client.set_timeout(request.timeout_seconds)
    return cast("CarlaClient", client)


def maintain_traffic_once(
    client: CarlaClient,
    request: TrafficControllerStartRequest,
    behaviors: Mapping[int, str],
    registered_actor_ids: frozenset[int] = frozenset(),
    *,
    configure_manager: bool = True,
) -> TrafficControllerStep:
    """Run one Traffic Manager maintenance step."""
    world = client.get_world()
    density_request = request.density
    if density_request.reset_existing:
        _reset_existing_vehicles(world, density_request.traffic_manager_port)
        density_request = replace(density_request, reset_existing=False)
    traffic_manager_instance = traffic_manager(client, density_request.traffic_manager_port)
    if configure_manager:
        _configure_density_manager(traffic_manager_instance, density_request)
    registered_actor_ids, spawned_actor_ids = _converge_density(
        world,
        traffic_manager_instance,
        density_request,
        registered_actor_ids,
    )
    _apply_profiles(world, traffic_manager_instance, dict(behaviors))
    counts = _vehicle_counts(world)
    _wait_for_tick(world)
    return TrafficControllerStep(
        request=replace(request, density=density_request),
        vehicle_count=counts[0],
        moving_vehicle_count=counts[1],
        registered_actor_ids=registered_actor_ids,
        spawned_actor_ids=spawned_actor_ids,
    )


def _configure_density_manager(
    traffic_manager_instance: CarlaTrafficManager,
    request: TrafficDensityRequest,
) -> None:
    """Apply Traffic Manager settings used by density control."""
    configure_traffic_manager(
        traffic_manager_instance,
        TrafficManagerRequest(
            traffic_manager_port=request.traffic_manager_port,
            global_distance_to_leading_vehicle=request.global_distance_to_leading_vehicle,
            global_percentage_speed_difference=request.global_percentage_speed_difference,
            seed=request.seed,
            synchronous_mode=False,
        ),
    )


def _converge_density(
    world: CarlaWorld,
    traffic_manager_instance: CarlaTrafficManager,
    request: TrafficDensityRequest,
    registered_actor_ids: frozenset[int],
) -> tuple[frozenset[int], tuple[int, ...]]:
    """Converge current vehicles and return managed plus newly spawned IDs."""
    vehicles = _vehicle_actors(world)
    _trim_vehicles(vehicles, request.vehicle_count)
    vehicles = _vehicle_actors(world)
    current_count = min(len(vehicles), request.vehicle_count)
    managed_vehicles = vehicles[:current_count]
    current_ids = frozenset(actor.id for actor in managed_vehicles)
    known_ids = registered_actor_ids & current_ids
    unregistered_vehicles = tuple(actor for actor in managed_vehicles if actor.id not in known_ids)
    _enable_autopilot(unregistered_vehicles, traffic_manager_instance.get_port())
    spawned_ids = _spawn_missing_vehicles(
        world,
        traffic_manager_instance,
        request,
        current_count,
    )
    newly_registered_ids = frozenset(actor.id for actor in unregistered_vehicles)
    managed_ids = known_ids | newly_registered_ids | frozenset(spawned_ids)
    return managed_ids, spawned_ids


def _spawn_missing_vehicles(
    world: CarlaWorld,
    traffic_manager_instance: CarlaTrafficManager,
    request: TrafficDensityRequest,
    current_count: int,
) -> tuple[int, ...]:
    """Spawn missing vehicles for a density target."""
    missing_count = request.vehicle_count - current_count
    if missing_count <= 0:
        return ()
    actor_ids, _failed_spawns = populate_traffic_actors(
        world=world,
        traffic_manager_instance=traffic_manager_instance,
        request=replace(_population_request(request), vehicle_count=missing_count),
    )
    return tuple(actor_ids)


def _population_request(request: TrafficDensityRequest) -> TrafficPopulationRequest:
    """Convert density settings into a population request."""
    return TrafficPopulationRequest(
        vehicle_count=request.vehicle_count,
        traffic_manager_port=request.traffic_manager_port,
        seed=request.seed,
        safe_filter=request.safe_filter,
        global_distance_to_leading_vehicle=request.global_distance_to_leading_vehicle,
        global_percentage_speed_difference=request.global_percentage_speed_difference,
    )


def _vehicle_actors(world: CarlaWorld) -> tuple[CarlaActor, ...]:
    """Return current vehicle actors."""
    return tuple(cast("CarlaActor", actor) for actor in world.get_actors().filter("vehicle.*"))


def _trim_vehicles(vehicles: tuple[CarlaActor, ...], target_count: int) -> None:
    """Destroy surplus vehicles above target density."""
    if len(vehicles) <= target_count:
        return
    surplus = sorted(vehicles, key=lambda actor: actor.id)[target_count:]
    _destroy_vehicles(tuple(surplus))


def _reset_existing_vehicles(world: CarlaWorld, traffic_manager_port: int) -> None:
    """Disable autopilot and remove existing vehicles before opening Traffic Manager."""
    vehicles = _vehicle_actors(world)
    for actor in vehicles:
        try:
            autopilot_enabled = False
            actor.set_autopilot(autopilot_enabled, traffic_manager_port)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    _wait_for_tick(world)
    _destroy_vehicles(vehicles)
    _wait_for_tick(world)


def _enable_autopilot(vehicles: tuple[CarlaActor, ...], traffic_manager_port: int) -> None:
    """Register existing vehicles with Traffic Manager autopilot."""
    for actor in vehicles:
        try:
            autopilot_enabled = True
            actor.set_autopilot(autopilot_enabled, traffic_manager_port)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue


def _destroy_vehicles(vehicles: tuple[CarlaActor, ...]) -> None:
    """Destroy vehicles and ignore actors already gone."""
    for actor in vehicles:
        try:
            actor.destroy()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue


def _apply_profiles(
    world: CarlaWorld,
    traffic_manager_instance: CarlaTrafficManager,
    behaviors: dict[int, str],
) -> None:
    """Apply behavior profiles to registered actor IDs."""
    for actor_id, profile_name in behaviors.items():
        actor = world.get_actors().find(actor_id)
        profile = behavior_profile(profile_name)
        if actor is not None and profile is not None:
            _apply_profile(traffic_manager_instance, cast("CarlaActor", actor), profile)


def _apply_behavior_once(
    start_request: TrafficControllerStartRequest,
    request: VehicleBehaviorRequest,
) -> dict[int, str]:
    """Apply one behavior request immediately when CARLA is reachable."""
    client = _client(start_request)
    world = client.get_world()
    manager = traffic_manager(client, request.traffic_manager_port)
    return _apply_behavior_to_world(world, manager, request)


def _apply_behavior_to_world(
    world: CarlaWorld,
    traffic_manager_instance: CarlaTrafficManager,
    request: VehicleBehaviorRequest,
) -> dict[int, str]:
    """Apply a behavior request and return per-actor failures."""
    failures: dict[int, str] = {}
    profile = behavior_profile(request.profile)
    for actor_id in request.actor_ids:
        actor = world.get_actors().find(actor_id)
        if actor is None:
            failures[actor_id] = "Actor was not found."
        elif profile is not None:
            _apply_profile(traffic_manager_instance, cast("CarlaActor", actor), profile)
    return failures


def _apply_profile(
    traffic_manager_instance: CarlaTrafficManager,
    actor: CarlaActor,
    profile: BehaviorProfile,
) -> None:
    """Apply one behavior profile to one actor."""
    autopilot_enabled = True
    actor.set_autopilot(autopilot_enabled, traffic_manager_instance.get_port())
    _call_manager(
        traffic_manager_instance,
        "vehicle_percentage_speed_difference",
        actor,
        profile.speed_difference,
    )
    _call_manager(
        traffic_manager_instance,
        "distance_to_leading_vehicle",
        actor,
        profile.distance_to_leading_vehicle,
    )
    _call_manager(traffic_manager_instance, "auto_lane_change", actor, profile.auto_lane_change)
    _call_manager(
        traffic_manager_instance,
        "ignore_lights_percentage",
        actor,
        profile.ignore_lights_percentage,
    )
    _call_manager(
        traffic_manager_instance,
        "ignore_signs_percentage",
        actor,
        profile.ignore_signs_percentage,
    )
    _call_manager(
        traffic_manager_instance,
        "ignore_vehicles_percentage",
        actor,
        profile.ignore_vehicles_percentage,
    )


def _call_manager(manager: CarlaTrafficManager, method_name: str, *args: object) -> None:
    """Call an optional Traffic Manager method."""
    method = getattr(manager, method_name, None)
    if callable(method):
        method(*args)


def _vehicle_counts(world: CarlaWorld) -> tuple[int, int]:
    """Return total and moving vehicle counts."""
    vehicles = _vehicle_actors(world)
    moving = sum(1 for actor in vehicles if _speed_mps(actor) > MOVING_SPEED_THRESHOLD_MPS)
    return len(vehicles), moving


def _speed_mps(actor: CarlaActor) -> float:
    """Return actor speed in metres per second."""
    squared_speed = _squared_vector_magnitude(actor.get_velocity())
    return float(squared_speed**0.5)


def _squared_vector_magnitude(vector: CarlaVector) -> float:
    """Return squared vector magnitude for a CARLA vector-like object."""
    x = float(vector.x)
    y = float(vector.y)
    z = float(vector.z)
    return x * x + y * y + z * z


def _wait_for_tick(world: CarlaWorld) -> None:
    """Wait for a world tick without letting timeouts kill the controller."""
    try:
        world.wait_for_tick(1.0)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        time.sleep(0.2)
