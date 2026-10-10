"""Journal native returned IDs before later actor setup can fail."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from carla_agentic_toolkit.authoritative_destroy import (
    destroy_authoritatively,
    destroy_result_cleaned,
)
from carla_agentic_toolkit.errors import CarlaAdapterError, OwnershipError
from carla_agentic_toolkit.models import DestroyResult
from carla_agentic_toolkit.ownership_release import release_controller_destroyed

if TYPE_CHECKING:
    from collections.abc import Iterable

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.ownership import RunOwnership


def install_creation_observers(adapter: PythonCarlaAdapter, ownership: RunOwnership | None) -> bool:
    """Use native observation where supported, retaining the legacy facade fallback."""
    setter = getattr(adapter, "set_actor_lifecycle_observers", None)
    if ownership is not None and callable(setter):
        setter(
            on_spawn=partial(track_actor_created, adapter, ownership),
            on_destroy=partial(release_controller_destroyed, adapter, ownership),
            before_spawn=partial(prepare_owned_spawn, adapter, ownership),
            on_no_actor=partial(complete_no_actor, adapter, ownership),
        )
        return True
    return False


def prepare_owned_spawn(adapter: PythonCarlaAdapter, ownership: RunOwnership) -> None:
    """Block native creation if durable intent cannot be written."""
    ownership.creation_health.require_healthy()
    try:
        ownership.begin_creation(adapter.get_world_identity())
    except (OwnershipError, CarlaAdapterError, RuntimeError) as exc:
        ownership.creation_health.record(str(exc), ())
        raise OwnershipError(str(exc)) from exc


def track_actor_created(
    adapter: PythonCarlaAdapter, ownership: RunOwnership, actor_id: int, world_id: int
) -> None:
    """Retain the raw ID and its origin even if journal reading fails."""
    _journal_created(adapter, ownership, (actor_id,), world_id)


def complete_no_actor(adapter: PythonCarlaAdapter, ownership: RunOwnership, world_id: int) -> None:
    """Clear a definite no-actor reply only while its originating episode is current."""
    try:
        _verify_creation_episode(adapter, world_id)
        ownership.complete_creation(world_id)
    except (OwnershipError, CarlaAdapterError, RuntimeError) as exc:
        ownership.creation_health.record(str(exc), ())
        raise OwnershipError(str(exc)) from exc


def track_created_result(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership | None,
    actor_ids: Iterable[int],
    *,
    world_id: int | None = None,
) -> None:
    """Support adapters that do not expose a native creation observer."""
    created = tuple(actor_ids)
    if ownership is None or not created:
        return
    ownership.creation_health.require_healthy()
    identity = world_id if world_id is not None else ownership.world_id()
    if identity is None:
        identity = adapter.get_world_identity()
    _journal_created(adapter, ownership, created, identity)


def _journal_created(
    adapter: PythonCarlaAdapter, ownership: RunOwnership, created: tuple[int, ...], identity: int
) -> None:
    try:
        ownership.add(created, world_id=identity)
        _verify_creation_episode(adapter, identity)
        ownership.complete_creation(identity)
    except (OwnershipError, CarlaAdapterError, RuntimeError) as exc:
        failures = _rollback_created(adapter, created, identity)
        if not failures:
            failures = _complete_rollback(adapter, ownership, identity, created[0])
        ownership.creation_health.record(str(exc), failures)
        raise OwnershipError(str(exc)) from exc


def _complete_rollback(
    adapter: PythonCarlaAdapter, ownership: RunOwnership, identity: int, actor_id: int
) -> tuple[DestroyResult, ...]:
    try:
        _verify_creation_episode(adapter, identity)
        ownership.complete_creation(identity)
    except (OwnershipError, CarlaAdapterError, RuntimeError) as exc:
        return (DestroyResult(actor_id, destroyed=False, error=str(exc)),)
    return ()


def _verify_creation_episode(adapter: PythonCarlaAdapter, identity: int) -> None:
    if adapter.get_world_identity() != identity:
        message = "CARLA world episode changed during actor creation."
        raise OwnershipError(message)


def _rollback_created(
    adapter: PythonCarlaAdapter, created: tuple[int, ...], identity: int
) -> tuple[DestroyResult, ...]:
    results = tuple(_rollback_one(adapter, actor_id, identity) for actor_id in reversed(created))
    return tuple(result for result in results if not destroy_result_cleaned(result))


def _rollback_one(adapter: PythonCarlaAdapter, actor_id: int, identity: int) -> DestroyResult:
    guarded_rollback = getattr(adapter, "rollback_created_actor", None)
    if callable(guarded_rollback):
        try:
            return guarded_rollback(actor_id, identity)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            return DestroyResult(actor_id, destroyed=False, error=str(exc))
    batch = getattr(adapter, "apply_batch", None)
    if callable(batch):
        return destroy_authoritatively(
            actor_id,
            expected_world_id=identity,
            current_world_id=adapter.get_world_identity,
            apply_batch=batch,
        )
    return _rollback_legacy(adapter, actor_id, identity)


def _rollback_legacy(adapter: PythonCarlaAdapter, actor_id: int, identity: int) -> DestroyResult:
    try:
        if adapter.get_world_identity() != identity:
            return DestroyResult(actor_id, destroyed=False, error="CARLA world episode changed.")
        results = adapter.destroy_actors((actor_id,))
        if len(results) == 1 and results[0].actor_id == actor_id:
            return results[0]
    except (CarlaAdapterError, RuntimeError) as exc:
        return DestroyResult(actor_id, destroyed=False, error=str(exc))
    return DestroyResult(actor_id, destroyed=False, error="Rollback acknowledgment was incomplete.")
