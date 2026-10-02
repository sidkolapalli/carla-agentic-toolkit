"""Walker runtime helpers for script-only CARLA experiments."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.experiment_common import actor, carla_location, object_factory

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaBlueprint, CarlaWorld
    from carla_agentic_toolkit.models import Location


@dataclass(frozen=True, slots=True)
class WalkerSpawnContext:
    """Context for deterministic walker spawning."""

    world: CarlaWorld
    walker_blueprints: list[CarlaBlueprint]
    controller_blueprint: CarlaBlueprint
    speed: float
    seed: int | None
    count: int


@dataclass(slots=True)
class WalkerSpawnState:
    """Accumulated walker spawn results."""

    walker_ids: list[int]
    controller_ids: list[int]
    failures: list[str]


def spawn_walker_actors(
    world: CarlaWorld,
    *,
    count: int,
    speed: float,
    seed: int | None,
) -> dict[str, object]:
    """Spawn pedestrians and AI walker controllers."""
    walker_blueprints = world.get_blueprint_library().filter("walker.pedestrian.*")
    controller_blueprint = world.get_blueprint_library().find("controller.ai.walker")
    spawned = spawn_walkers(
        WalkerSpawnContext(
            world=world,
            walker_blueprints=walker_blueprints,
            controller_blueprint=controller_blueprint,
            speed=speed,
            seed=seed,
            count=count,
        )
    )
    return {"requested_walkers": count, **spawned}


def set_walker_destination(
    world: CarlaWorld,
    *,
    controller_id: int,
    location: Location,
) -> dict[str, object]:
    """Send a walker AI controller to a destination."""
    destination = carla_location(import_module("carla"), location)
    cast("Any", actor(world, controller_id)).go_to_location(destination)
    return {"controller_id": controller_id, "destination": location.to_dict()}


def apply_walker_control(
    world: CarlaWorld,
    *,
    actor_id: int,
    direction: Location,
    speed: float,
) -> dict[str, object]:
    """Apply manual WalkerControl to a pedestrian actor."""
    carla_module = import_module("carla")
    control = object_factory(carla_module, "WalkerControl")(
        direction=carla_location(carla_module, direction),
        speed=speed,
    )
    cast("Any", actor(world, actor_id)).apply_control(control)
    return {"actor_id": actor_id, "direction": direction.to_dict(), "speed": speed}


def spawn_walkers(context: WalkerSpawnContext) -> dict[str, object]:
    """Spawn pedestrians and AI controllers."""
    state = WalkerSpawnState(walker_ids=[], controller_ids=[], failures=[])
    for index in range(max(context.count, 0)):
        try_spawn_walker(context, state, index)
    return {
        "walker_ids": state.walker_ids,
        "controller_ids": state.controller_ids,
        "failed_spawns": state.failures,
    }


def try_spawn_walker(context: WalkerSpawnContext, state: WalkerSpawnState, index: int) -> None:
    """Try to spawn one walker/controller pair.

    The walker is tracked before controller spawn so a later failure
    does not leak an unowned pedestrian.
    """
    walker: CarlaActor | None = None
    controller: CarlaActor | None = None
    try:
        transform = random_walker_transform(context.world)
        walker = context.world.try_spawn_actor(walker_blueprint(context, index), transform)
        if walker is None:
            state.failures.append("walker spawn returned None")
            return
        state.walker_ids.append(int(walker.id))
        controller = context.world.spawn_actor(context.controller_blueprint, transform, walker)
        state.controller_ids.append(int(controller.id))
        cast("Any", controller).start()
        cast("Any", controller).set_max_speed(context.speed)
        cast("Any", controller).go_to_location(random_walker_location(context.world))
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        state.failures.append(str(exc))
        _rollback_spawned_actor(controller, state.controller_ids)
        _rollback_spawned_actor(walker, state.walker_ids)


def _rollback_spawned_actor(actor: CarlaActor | None, tracked_ids: list[int]) -> None:
    """Destroy a partially created actor and remove it from successful results."""
    if actor is None or not _destroy_actor(actor):
        return
    if tracked_ids and tracked_ids[-1] == int(actor.id):
        tracked_ids.pop()


def walker_blueprint(context: WalkerSpawnContext, index: int) -> CarlaBlueprint:
    """Select a walker blueprint deterministically."""
    if not context.walker_blueprints:
        msg = "No walker pedestrian blueprints are available."
        raise CarlaAdapterError(msg)
    offset = context.seed or 0
    return context.walker_blueprints[(index + offset) % len(context.walker_blueprints)]


def _destroy_actor(actor: object) -> bool:
    """Return whether a partial actor was destroyed without an error."""
    destroy = getattr(actor, "destroy", None)
    if callable(destroy):
        with contextlib.suppress(AttributeError, RuntimeError, TypeError, ValueError):
            return bool(destroy())
    return False


def random_walker_transform(world: CarlaWorld) -> object:
    """Return a CARLA transform for a pedestrian spawn point."""
    location = random_walker_location(world)
    transform_factory = object_factory(import_module("carla"), "Transform")
    return transform_factory(location)


def random_walker_location(world: CarlaWorld) -> object:
    """Return a random navigation location for a walker."""
    location = cast("Any", world).get_random_location_from_navigation()
    if location is None:
        msg = "CARLA did not return a navigation location for walkers."
        raise CarlaAdapterError(msg)
    return location
