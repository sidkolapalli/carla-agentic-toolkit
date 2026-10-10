"""Creation lifetime and durable ownership integration for the script facade."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from carla_agentic_toolkit.actor_creation import spawn_observers
from carla_agentic_toolkit.creation_health import finish_creation_outcome
from carla_agentic_toolkit.errors import OwnershipError
from carla_agentic_toolkit.ownership_creation import (
    complete_no_actor,
    install_creation_observers,
    prepare_owned_spawn,
    track_actor_created,
    track_created_result,
)
from carla_agentic_toolkit.ownership_release import release_controller_destroyed
from carla_agentic_toolkit.script_timing_operations import ScriptTimingOperations
from carla_agentic_toolkit.traffic_controller_service import InProcessTrafficControllerService

if TYPE_CHECKING:
    from collections.abc import Iterable

    from carla_agentic_toolkit.actor_creation import SpawnObservers
    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.models import JsonObject
    from carla_agentic_toolkit.ownership import RunOwnership


class ScriptOwnershipOperations(ScriptTimingOperations):
    """Observe native creation while keeping legacy adapter doubles compatible."""

    _ownership: RunOwnership | None

    def _initialize_ownership(
        self, adapter: PythonCarlaAdapter, ownership: RunOwnership | None
    ) -> None:
        self._ownership = ownership
        self._native_creation_observer = install_creation_observers(adapter, ownership)
        self._creation_world_id: int | None = None
        self._traffic_controller = InProcessTrafficControllerService(
            on_spawn=self._track_owned,
            on_destroy=partial(release_controller_destroyed, adapter, ownership),
            creation_observers=_controller_observers(adapter, ownership),
            configure_rpc_timeout=getattr(adapter, "configure_rpc_timeout", None),
            before_setting=getattr(adapter, "capture_traffic_manager_setting", None),
        )

    def _replaced_world(self, payload: JsonObject) -> JsonObject:
        if self._ownership is not None:
            self._ownership.require_completed_creations()
            self._ownership.clear(require_completed=True)
        return self._snapshot("carla-snapshot://world/current", payload)

    def _track_owned(self, actor_ids: Iterable[int]) -> None:
        if not self._native_creation_observer:
            track_created_result(
                self._adapter, self._ownership, actor_ids, world_id=self._creation_world_id
            )

    def _observe_actor_created(self, actor_id: int, world_id: int) -> None:
        if self._ownership is not None:
            track_actor_created(self._adapter, self._ownership, actor_id, world_id)

    def _prepare_owned_controller(self) -> None:
        if self._ownership is not None:
            self._ownership.creation_health.require_healthy()
            self._ownership.bind_world(self._adapter.get_world_identity())

    def _prepare_owned_creation(self) -> None:
        """Preserve the originating episode before calling legacy creation adapters."""
        if self._ownership is not None:
            self._ownership.creation_health.require_healthy()
            self._creation_world_id = self._adapter.get_world_identity()
            if not self._native_creation_observer:
                self._ownership.begin_creation(self._creation_world_id)

    def _require_completed_creations(self) -> None:
        status = self._traffic_controller.get_status()
        if status.active or status.stopping:
            message = "Stop the traffic controller before replacing the CARLA world."
            raise OwnershipError(message)
        if self._ownership is not None:
            self._ownership.require_completed_creations()

    def _finalize_owned_outcome(self, outcome: dict[str, object]) -> dict[str, object]:
        return finish_creation_outcome(outcome, self._ownership)


def _controller_observers(
    adapter: PythonCarlaAdapter, ownership: RunOwnership | None
) -> SpawnObservers:
    if ownership is None:
        return {}
    return spawn_observers(
        partial(track_actor_created, adapter, ownership),
        partial(prepare_owned_spawn, adapter, ownership),
        partial(complete_no_actor, adapter, ownership),
    )
