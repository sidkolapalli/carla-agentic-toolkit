"""Stop explicitly owned AI controllers before deleting their walker population."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.actor_runtime import actor_by_id
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import DestroyResult
from carla_agentic_toolkit.persistent_connection import record_operation_failure

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaWorld

_NATIVE_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)


def prepare_walker_cleanup(
    world: CarlaWorld,
    actor_ids: tuple[int, ...],
    *,
    require_episode: Callable[[], None],
    skip_actor_ids: frozenset[int] = frozenset(),
) -> dict[int, DestroyResult]:
    """Stop controllers first, independent of input order; do not adopt other actors.

    The legacy journal has IDs, not controller-parent relationships. A failed
    Stop therefore conservatively retains all owned walkers in this cleanup.
    """
    try:
        actors = _resolve_owned(world, actor_ids, require_episode, skip_actor_ids)
        failures = _stop_controllers(actors, require_episode)
    except _NATIVE_ERRORS as exc:
        record_operation_failure(exc)
        return {actor_id: _failure(actor_id, str(exc)) for actor_id in actor_ids}
    if failures:
        _retain_walkers(actors, failures)
    return failures


def _resolve_owned(
    world: CarlaWorld,
    actor_ids: tuple[int, ...],
    require_episode: Callable[[], None],
    skipped: frozenset[int],
) -> dict[int, object]:
    actors: dict[int, object] = {}
    for actor_id in actor_ids:
        if actor_id in skipped:
            continue
        require_episode()
        candidate = actor_by_id(world, actor_id)
        require_episode()
        if candidate is not None:
            _require_owned_identity(candidate, actor_id)
            actors[actor_id] = candidate
    return actors


def _require_owned_identity(candidate: object, actor_id: int) -> None:
    identity = getattr(candidate, "id", None)
    if type(identity) is not int or identity != actor_id:
        message = "Explicit owned-actor lookup returned a different actor identity."
        raise CarlaAdapterError(message)


def _stop_controllers(
    actors: dict[int, object], require_episode: Callable[[], None]
) -> dict[int, DestroyResult]:
    failures: dict[int, DestroyResult] = {}
    for actor_id, candidate in actors.items():
        if getattr(candidate, "type_id", None) != "controller.ai.walker":
            continue
        try:
            require_episode()
            cast("Any", candidate).stop()
            require_episode()
        except _NATIVE_ERRORS as exc:
            record_operation_failure(exc)
            failures[actor_id] = _failure(actor_id, str(exc))
    return failures


def _retain_walkers(actors: dict[int, object], failures: dict[int, DestroyResult]) -> None:
    for actor_id, candidate in actors.items():
        type_id = getattr(candidate, "type_id", "")
        if isinstance(type_id, str) and type_id.startswith("walker."):
            failures[actor_id] = _failure(
                actor_id, "An owned walker controller Stop was not acknowledged."
            )


def _failure(actor_id: int, error: str) -> DestroyResult:
    return DestroyResult(actor_id, destroyed=False, error=f"Walker cleanup failed: {error}")
