"""Guarded owned-actor deletion with AI walker navigation shutdown."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit.managed_world import world_identity
from carla_agentic_toolkit.models import DestroyResult
from carla_agentic_toolkit.persistent_connection import record_operation_failure
from carla_agentic_toolkit.walker_cleanup import prepare_walker_cleanup

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld


class PythonCarlaActorCleanupMixin:
    """Keep lifecycle preparation separate from native and retained-sensor deletion."""

    def _client(self) -> CarlaClient:
        """Return the concrete adapter's retained native client."""
        raise NotImplementedError

    @staticmethod
    def _world(client: CarlaClient) -> CarlaWorld:
        """Fetch its current world without changing the episode."""
        raise NotImplementedError

    def _has_retained_sensor(self, actor_id: int) -> bool:
        """Identify sensors handled by the existing listener/origin cleanup path."""
        raise NotImplementedError

    def _require_cleanup_episode(self, identity: int) -> None:
        """Validate the current native client episode and remaining RPC budget."""
        raise NotImplementedError

    def _destroy_actor(self, world: CarlaWorld, actor_id: int) -> DestroyResult:
        """Use the concrete native or retained-sensor destruction implementation."""
        raise NotImplementedError

    def _destroy_uncached_actor(
        self, world: CarlaWorld, actor_id: int, *, expected_world_id: int | None = None
    ) -> DestroyResult:
        """Require the existing non-ticking authoritative server acknowledgement."""
        raise NotImplementedError

    def destroy_actors(self, actor_ids: tuple[int, ...]) -> tuple[DestroyResult, ...]:
        """Stop known AI controllers before deleting explicit same-episode actors."""
        world = self._world(self._client())
        identity = world_identity(world)
        failures = prepare_walker_cleanup(
            world,
            actor_ids,
            require_episode=lambda: self._require_cleanup_episode(identity),
            skip_actor_ids=frozenset(
                actor_id for actor_id in actor_ids if self._has_retained_sensor(actor_id)
            ),
        )
        return tuple(
            failures[actor_id]
            if actor_id in failures
            else self._destroy_in_episode(world, actor_id, identity)
            for actor_id in actor_ids
        )

    def _destroy_in_episode(self, world: CarlaWorld, actor_id: int, identity: int) -> DestroyResult:
        try:
            self._require_cleanup_episode(identity)
            result = self._destroy_actor(world, actor_id)
            self._require_cleanup_episode(identity)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            record_operation_failure(exc)
            return DestroyResult(actor_id, destroyed=False, error=str(exc))
        else:
            return result

    def _delete_walker_rollback(
        self, world: CarlaWorld, actor_id: int, identity: int
    ) -> DestroyResult:
        return self._destroy_uncached_actor(world, actor_id, expected_world_id=identity)

    def rollback_created_actor(self, actor_id: int, identity: int) -> DestroyResult:
        """Preserve AI controller Stop on a known-ID journal failure."""
        world = self._world(self._client())
        failures = prepare_walker_cleanup(
            world, (actor_id,), require_episode=lambda: self._require_cleanup_episode(identity)
        )
        if actor_id in failures:
            return failures[actor_id]
        return self._delete_walker_rollback(world, actor_id, identity)
