"""Optional native actor lifecycle hooks for immediate script ownership."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit.actor_creation import prepare_spawn, spawn_observers

if TYPE_CHECKING:
    from carla_agentic_toolkit.actor_creation import (
        ActorCleanupObserver,
        ActorCreationObserver,
        BeforeSpawn,
        NoActorObserver,
        SpawnObservers,
    )


class PythonCarlaCreationMixin:
    """Keep creation observers independent of the native connection and public facade."""

    _actor_creation_observer: ActorCreationObserver | None = None
    _actor_cleanup_observer: ActorCleanupObserver | None = None
    _actor_creation_guard: BeforeSpawn | None = None
    _no_actor_observer: NoActorObserver | None = None

    def set_actor_lifecycle_observers(
        self,
        *,
        on_spawn: ActorCreationObserver,
        on_destroy: ActorCleanupObserver,
        before_spawn: BeforeSpawn | None = None,
        on_no_actor: NoActorObserver | None = None,
    ) -> None:
        """Install the script's durable journal hooks without changing standalone callers."""
        self._actor_creation_observer = on_spawn
        self._actor_cleanup_observer = on_destroy
        self._actor_creation_guard = before_spawn
        self._no_actor_observer = on_no_actor

    def _creation_options(self) -> SpawnObservers:
        return spawn_observers(
            self._actor_creation_observer, self._prepare_actor_spawn, self._no_actor_observer
        )

    def _prepare_actor_spawn(self) -> None:
        prepare_spawn(getattr(self, "_refresh_rpc_timeout", None))
        prepare_spawn(self._actor_creation_guard)

    def _observe_actor_cleanup(self, actor_id: int, world_id: int) -> None:
        if self._actor_cleanup_observer is not None:
            self._actor_cleanup_observer((actor_id,), world_id)
