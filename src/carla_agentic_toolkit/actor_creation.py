"""Observe each raw CARLA actor ID before validation or later setup can interrupt creation."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

from carla_agentic_toolkit.managed_world import world_identity

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaWorld

type ActorCreationObserver = Callable[[int, int], None]
type ActorCleanupObserver = Callable[[tuple[int, ...], int | None], None]
type NoActorObserver = Callable[[int], None]
type BeforeSpawn = Callable[[], object]


class SpawnObservers(TypedDict, total=False):
    """Optional hooks preserve standalone helper and existing test-double behavior."""

    on_spawn: ActorCreationObserver | None
    before_spawn: BeforeSpawn | None
    on_no_actor: NoActorObserver | None


def creation_identity(world: CarlaWorld, observer: ActorCreationObserver | None) -> int | None:
    """Capture origin before the spawn RPC, only when durable ownership is installed."""
    return world_identity(world) if observer is not None else None


def observe_created_actor(
    actor: CarlaActor, identity: int | None, observer: ActorCreationObserver | None
) -> None:
    """Persist the returned ID before touching sensor metadata or Traffic Manager."""
    if observer is not None and identity is not None:
        observer(actor.id, identity)


def prepare_spawn(callback: BeforeSpawn | None) -> None:
    """Refresh the remaining RPC budget immediately before each native mutation."""
    if callback is not None:
        callback()


def observe_no_actor(identity: int | None, observer: NoActorObserver | None) -> None:
    """Resolve a definite collision without guessing an actor ID."""
    if observer is not None and identity is not None:
        observer(identity)


def spawn_observers(
    on_spawn: ActorCreationObserver | None,
    before_spawn: BeforeSpawn | None = None,
    on_no_actor: NoActorObserver | None = None,
) -> SpawnObservers:
    """Omit absent hooks so legacy helper doubles need no new keyword parameters."""
    options: SpawnObservers = {}
    if on_spawn is not None:
        options["on_spawn"] = on_spawn
    if before_spawn is not None:
        options["before_spawn"] = before_spawn
    if on_no_actor is not None:
        options["on_no_actor"] = on_no_actor
    return options
