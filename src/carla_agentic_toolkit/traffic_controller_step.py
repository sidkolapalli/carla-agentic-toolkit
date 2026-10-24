"""One synchronous-mode-guarded pass of asynchronous traffic maintenance."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from carla_agentic_toolkit.actor_creation import spawn_observers
from carla_agentic_toolkit.models import TrafficManagerRequest
from carla_agentic_toolkit.traffic_behavior import apply_profiles
from carla_agentic_toolkit.traffic_density import (
    ControllerActors,
    converge_density,
    reset_existing_vehicles,
    vehicle_counts,
    wait_for_tick,
)
from carla_agentic_toolkit.traffic_frame_wait import FrameWaitError
from carla_agentic_toolkit.traffic_manager_policy import (
    require_async_density_mode,
    require_async_traffic_world,
)
from carla_agentic_toolkit.traffic_runtime import configure_traffic_manager, traffic_manager

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Unpack

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaTrafficManager
    from carla_agentic_toolkit.models import TrafficControllerStartRequest, TrafficDensityRequest
    from carla_agentic_toolkit.traffic_manager_policy import (
        BeforeTrafficManagerSetting,
        TrafficMaintenanceObservers,
    )

__all__ = ["TrafficControllerStep", "maintain_traffic_once", "require_async_density_mode"]


@dataclass(frozen=True, slots=True)
class TrafficControllerStep:
    """Result of one Traffic Manager maintenance step."""

    request: TrafficControllerStartRequest
    vehicle_count: int
    moving_vehicle_count: int
    registered_actor_ids: frozenset[int]
    spawned_actor_ids: tuple[int, ...] = ()
    owned_actor_ids: frozenset[int] = frozenset()
    conflict: dict[str, object] | None = None
    destroyed_actor_ids: tuple[int, ...] = ()
    world_id: int | None = None


def maintain_traffic_once(
    client: CarlaClient,
    request: TrafficControllerStartRequest,
    behaviors: Mapping[int, str],
    registered_actor_ids: frozenset[int] | ControllerActors = frozenset(),
    *,
    configure_manager: bool = True,
    **observers: Unpack[TrafficMaintenanceObservers],
) -> TrafficControllerStep:
    """Maintain an asynchronous world without owning or advancing its clock.

    Synchronous background maintenance is unsupported. Check the world before
    any reset, TM access, or mutation, including on already configured passes.
    No timing ownership is acquired, so stop has no world timing to restore.
    """
    world = client.get_world()
    world_id = getattr(world, "id", None)
    require_async_traffic_world(world)
    actors = _controller_actors(registered_actor_ids)
    density_request = request.density
    destroyed: tuple[int, ...] = ()
    if density_request.reset_existing:
        destroyed = reset_existing_vehicles(world, density_request.traffic_manager_port)
        density_request = replace(density_request, reset_existing=False)
        actors = ControllerActors()
    traffic_manager_instance = traffic_manager(client, density_request.traffic_manager_port)
    if configure_manager:
        _configure_density_manager(
            traffic_manager_instance, density_request, observers.get("before_setting")
        )
    population = converge_density(
        world,
        traffic_manager_instance,
        density_request,
        actors.registered,
        actors.owned,
        **spawn_observers(
            observers.get("on_spawn"), observers.get("before_spawn"), observers.get("on_no_actor")
        ),
    )
    apply_profiles(world, traffic_manager_instance, dict(behaviors))
    counts = vehicle_counts(world)
    result = TrafficControllerStep(
        request=replace(request, density=density_request),
        vehicle_count=counts[0],
        moving_vehicle_count=counts[1],
        registered_actor_ids=population.registered,
        spawned_actor_ids=population.spawned,
        owned_actor_ids=population.owned,
        conflict=population.conflict,
        destroyed_actor_ids=(*destroyed, *population.destroyed),
        world_id=world_id,
    )
    try:
        wait_for_tick(world)
    except FrameWaitError as exc:
        exc.completed_step = result
        raise
    return result


def _controller_actors(value: frozenset[int] | ControllerActors) -> ControllerActors:
    """Treat legacy registration-only input as adoption without deletion rights."""
    return value if isinstance(value, ControllerActors) else ControllerActors(registered=value)


def _configure_density_manager(
    traffic_manager_instance: CarlaTrafficManager,
    request: TrafficDensityRequest,
    before_setting: BeforeTrafficManagerSetting | None,
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
        before_setting=before_setting,
    )
