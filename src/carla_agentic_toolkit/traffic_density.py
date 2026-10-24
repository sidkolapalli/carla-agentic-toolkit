"""Creation-aware density convergence and asynchronous actor lifecycle operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.models import TrafficPopulationRequest
from carla_agentic_toolkit.traffic_frame_wait import (
    FrameWaitError,
    ResetProgress,
    wait_for_density_frame,
)
from carla_agentic_toolkit.traffic_runtime import populate_traffic_actors

if TYPE_CHECKING:
    from typing import Unpack

    from carla_agentic_toolkit.actor_creation import SpawnObservers
    from carla_agentic_toolkit.carla_protocols import (
        CarlaActor,
        CarlaTrafficManager,
        CarlaVector,
        CarlaWorld,
    )
    from carla_agentic_toolkit.models import TrafficDensityRequest
MOVING_SPEED_THRESHOLD_MPS = 0.5


@dataclass(frozen=True, slots=True)
class _DensityPlan:
    """Pure total-world density plan with explicit creation-based deletion rights."""

    trim: tuple[CarlaActor, ...]
    conflict: dict[str, object] | None


@dataclass(frozen=True, slots=True)
class _DensityPopulation:
    """Observed population after executing a density plan."""

    registered: frozenset[int]
    owned: frozenset[int]
    spawned: tuple[int, ...]
    conflict: dict[str, object] | None
    destroyed: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ControllerActors:
    """Keep autopilot registration distinct from creation-based deletion rights."""

    registered: frozenset[int] = frozenset()
    owned: frozenset[int] = frozenset()


def converge_density(
    world: CarlaWorld,
    traffic_manager_instance: CarlaTrafficManager,
    request: TrafficDensityRequest,
    registered_actor_ids: frozenset[int],
    owned_actor_ids: frozenset[int],
    **observers: Unpack[SpawnObservers],
) -> _DensityPopulation:
    """Execute a total-world target plan without deleting adopted vehicles."""
    vehicles = _vehicle_actors(world)
    plan = _plan_density(vehicles, request.vehicle_count, owned_actor_ids)
    destroyed = _destroy_vehicles(plan.trim)
    vehicles = _vehicle_actors(world)
    current_ids = frozenset(actor.id for actor in vehicles)
    registered, spawned = _register_population(
        world,
        traffic_manager_instance,
        request,
        registered_actor_ids,
        plan.conflict,
        **observers,
    )
    return _DensityPopulation(
        registered=registered,
        owned=(owned_actor_ids & current_ids) | frozenset(spawned),
        spawned=spawned,
        conflict=plan.conflict,
        destroyed=destroyed,
    )


def _register_population(
    world: CarlaWorld,
    traffic_manager_instance: CarlaTrafficManager,
    request: TrafficDensityRequest,
    registered_actor_ids: frozenset[int],
    conflict: dict[str, object] | None,
    **observers: Unpack[SpawnObservers],
) -> tuple[frozenset[int], tuple[int, ...]]:
    """Adopt or create the missing population only when protected density permits it."""
    vehicles = _vehicle_actors(world)
    current_ids = frozenset(actor.id for actor in vehicles)
    if conflict:
        return registered_actor_ids & current_ids, ()
    unregistered_vehicles = tuple(
        actor for actor in vehicles if actor.id not in registered_actor_ids
    )
    _enable_autopilot(unregistered_vehicles, traffic_manager_instance.get_port())
    spawned_ids = _spawn_missing_vehicles(
        world,
        traffic_manager_instance,
        request,
        len(vehicles),
        **observers,
    )
    return current_ids | frozenset(spawned_ids), spawned_ids


def _spawn_missing_vehicles(
    world: CarlaWorld,
    traffic_manager_instance: CarlaTrafficManager,
    request: TrafficDensityRequest,
    current_count: int,
    **observers: Unpack[SpawnObservers],
) -> tuple[int, ...]:
    """Spawn missing vehicles for a density target."""
    missing_count = request.vehicle_count - current_count
    if missing_count <= 0:
        return ()
    actor_ids, _failed_spawns = populate_traffic_actors(
        world=world,
        traffic_manager_instance=traffic_manager_instance,
        request=replace(_population_request(request), vehicle_count=missing_count),
        **observers,
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


def _plan_density(
    vehicles: tuple[CarlaActor, ...],
    target_count: int,
    owned_actor_ids: frozenset[int],
) -> _DensityPlan:
    """Plan removals from created actors, counting every live world vehicle.

    Adopted and other pre-existing actors remain protected. The separate,
    explicitly destructive reset path is the only way to delete them.
    """
    owned = sorted(
        (actor for actor in vehicles if actor.id in owned_actor_ids),
        key=lambda actor: actor.id,
    )
    surplus = max(0, len(vehicles) - target_count)
    trim = tuple(owned[-surplus:]) if surplus else ()
    return _DensityPlan(trim, _density_conflict(vehicles, target_count, owned_actor_ids))


def _density_conflict(
    vehicles: tuple[CarlaActor, ...], target_count: int, owned_actor_ids: frozenset[int]
) -> dict[str, object] | None:
    """Explain when protected actors alone prevent a total-world target."""
    protected_ids = sorted(actor.id for actor in vehicles if actor.id not in owned_actor_ids)
    if len(protected_ids) <= target_count:
        return None
    return {
        "error_type": "density_conflict",
        "target_vehicle_count": target_count,
        "protected_actor_ids": protected_ids,
    }


def reset_existing_vehicles(world: CarlaWorld, traffic_manager_port: int) -> tuple[int, ...]:
    """Disable autopilot and remove existing vehicles before opening Traffic Manager."""
    world_id = getattr(world, "id", None)
    vehicles = _vehicle_actors(world)
    for actor in vehicles:
        try:
            autopilot_enabled = False
            actor.set_autopilot(autopilot_enabled, traffic_manager_port)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    _wait_for_reset_frame(world, world_id=world_id, phase="reset_before_destroy")
    destroyed = _destroy_vehicles(vehicles)
    _wait_for_reset_frame(
        world,
        world_id=world_id,
        phase="reset_after_destroy",
        destroyed=destroyed,
        destroy_phase_completed=True,
    )
    return destroyed


def _wait_for_reset_frame(
    world: CarlaWorld,
    *,
    world_id: int | None,
    phase: str,
    destroyed: tuple[int, ...] = (),
    destroy_phase_completed: bool = False,
) -> None:
    """Attach only acknowledged actions when a reset frame cannot be delivered."""
    try:
        wait_for_tick(world, phase=phase)
    except FrameWaitError as exc:
        exc.record_reset(ResetProgress(phase, world_id, destroy_phase_completed, destroyed))
        raise


def _enable_autopilot(vehicles: tuple[CarlaActor, ...], traffic_manager_port: int) -> None:
    """Register existing vehicles with Traffic Manager autopilot."""
    for actor in vehicles:
        try:
            autopilot_enabled = True
            actor.set_autopilot(autopilot_enabled, traffic_manager_port)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue


def _destroy_vehicles(vehicles: tuple[CarlaActor, ...]) -> tuple[int, ...]:
    """Destroy vehicles and ignore actors already gone."""
    destroyed: list[int] = []
    for actor in vehicles:
        try:
            if actor.destroy():
                destroyed.append(actor.id)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            continue
    return tuple(destroyed)


def vehicle_counts(world: CarlaWorld) -> tuple[int, int]:
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


def wait_for_tick(world: CarlaWorld, *, phase: str = "maintenance_end") -> None:
    """Report one missed asynchronous frame as a fatal density failure."""
    wait_for_density_frame(world, phase=phase)
