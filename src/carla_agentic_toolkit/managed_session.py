"""Trusted dedicated-instance ownership, frame progression, and recovery."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from functools import partial
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit import experiment_replay
from carla_agentic_toolkit.authoritative_destroy import (
    destroy_authoritatively,
    destroy_result_cleaned,
)
from carla_agentic_toolkit.managed_creation import (
    CreationOptions,
    ManagedCreationJournal,
    OwnedActor,
    recover_owned,
    spawn_managed,
)
from carla_agentic_toolkit.managed_replacement import check_replaced_world
from carla_agentic_toolkit.managed_setup import (
    ManagedReload,
    require_setup_episode,
    reset_traffic_lights,
    returned_world_identity,
)
from carla_agentic_toolkit.managed_world import (
    SessionInvariantError,
    apply_world_settings,
    require_dedicated_world,
    settle_setup_frames,
    world_identity,
    world_settings,
)
from carla_agentic_toolkit.recovery_snapshots import fresh_cleanup_frame

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import (
        CarlaActor,
        CarlaBlueprint,
        CarlaClient,
        CarlaSnapshot,
        CarlaWorld,
    )
    from carla_agentic_toolkit.managed_spec import ExperimentSpec
    from carla_agentic_toolkit.simulator_lease import SimulatorLease


class ManagedSession:
    """Retain CARLA handles and hold the caller's lease across every cleanup action."""

    def __init__(
        self,
        spec: ExperimentSpec,
        client: CarlaClient,
        lease: SimulatorLease,
        run_id: str,
    ) -> None:
        """Bind trusted dependencies; do not mutate the simulator yet."""
        self.spec, self.client, self.lease, self.run_id = spec, client, lease, run_id
        self.world = client.get_world()
        self.map = self.world.get_map()
        self.world_generation = uuid.uuid4().hex
        self.world_id = world_identity(self.world)
        self.expected_frame = self.world.get_snapshot().frame
        self._original_settings: dict[str, object] = {}
        self._owned: dict[int, OwnedActor] = {}
        self._handles: dict[int, CarlaActor] = {}
        self.creation = ManagedCreationJournal(self.world_id, self._journal)
        self.reload = ManagedReload(self.world_id, self.map.name, self._journal)
        self._closing_callbacks: list[Callable[[], object]] = []
        self._opened = False
        self._ready = False
        self._cleanup: dict[str, object] | None = None
        self.setup_frame_barrier: dict[str, object] = {}
        self.initial_traffic_lights: dict[str, object] = {}
        self._recovering = False
        self._recovery_frame: int | None = None

    def open(self) -> None:
        """Validate dedicated use and journal settings before the first mutation."""
        if self._opened:
            message = "Session is already open."
            raise SessionInvariantError(message)
        if self._owned or self.creation.intents:
            message = "Cannot reload with retained actor ownership or creation intents."
            raise SessionInvariantError(message)
        require_dedicated_world(self.world, self.client, self.spec, self.world_id)
        self._original_settings = world_settings(self.world)
        self._journal()
        self._opened = True
        self.reload.prepare()
        values = self._original_settings | {
            "synchronous_mode": True,
            "fixed_delta_seconds": self.spec.fixed_delta_seconds,
            "substepping": True,
            "max_substeps": self.spec.max_substeps,
            "max_substep_delta_time": self.spec.max_substep_delta_time,
        }
        require_setup_episode(self.client, self.world_id)
        apply_world_settings(
            self.world,
            values,
            before_apply=partial(require_setup_episode, self.client, self.world_id),
        )
        self._reload_world()
        if world_settings(self.world) != values:
            message = "Managed reload did not preserve the requested world settings."
            raise SessionInvariantError(message)
        self.setup_frame_barrier = settle_setup_frames(
            self.world, self.client, self.spec, self.world_id
        )
        self.initial_traffic_lights = reset_traffic_lights(self.world, self.client, self.world_id)
        self.expected_frame = cast("int", self.initial_traffic_lights["frame"])
        self.setup_frame_barrier["traffic_light_reset_frame"] = self.expected_frame
        self._ready = True

    def _reload_world(self) -> None:
        require_setup_episode(self.client, self.world_id)
        self.reload.pending()
        require_setup_episode(self.client, self.world_id)
        returned = self.client.reload_world(reset_settings=False)
        identity = returned_world_identity(returned, self.world_id)
        self._bind_reloaded_world(returned, identity)
        self.reload.acknowledge(identity)
        require_setup_episode(self.client, identity)
        self.map = self.world.get_map()
        require_setup_episode(self.client, identity)
        self.reload.bind_map(self.map.name)

    def _bind_reloaded_world(self, world: CarlaWorld, world_id: int) -> None:
        self.world, self.world_id = world, world_id
        self.creation = ManagedCreationJournal(world_id, self._journal)

    def assert_current(self) -> None:
        """Reject world replacement and any unaccounted-for frame advancement."""
        if not self._ready or self._cleanup is not None:
            message = "Session is not running."
            raise SessionInvariantError(message)
        if world_identity(self.client.get_world()) != self.world_id:
            message = "The world was replaced; all actor and decision handles are invalid."
            raise SessionInvariantError(message)
        if self.world.get_snapshot().frame != self.expected_frame:
            message = "Unexpected frame advancement by an external client."
            raise SessionInvariantError(message)

    def step(self) -> CarlaSnapshot:
        """Advance exactly one frame; no background thread or helper may tick."""
        self.assert_current()
        expected = self.expected_frame + 1
        frame = self.world.tick()
        snapshot = self.world.get_snapshot()
        if frame != expected or snapshot.frame != expected:
            message = "Scheduled step did not advance exactly one frame."
            raise SessionInvariantError(message)
        self.expected_frame = expected
        return snapshot

    def own(self, actor: CarlaActor, *, controller: str, protected: bool = True) -> None:
        """Register successful creation immediately, independently from its controller."""
        if actor.id in self._owned:
            self.assert_current()
            self.assign_controller(actor.id, controller)
            return
        role_name = actor.attributes.get("role_name", "")
        self._handles[actor.id] = actor
        self._owned[actor.id] = OwnedActor(
            actor.id, actor.type_id, role_name, controller, protected
        )
        self._journal()
        self.assert_current()

    def spawn_actor(
        self,
        blueprint: CarlaBlueprint,
        transform: object,
        *,
        role_name: str,
        controller: str,
        attach_to: CarlaActor | None = None,
    ) -> CarlaActor:
        """Persist a plan before native creation and its raw returned ID before setup."""
        return spawn_managed(
            self,
            blueprint,
            transform,
            CreationOptions(role_name, controller, attach_to),
        )

    def record_returned(self, actor: CarlaActor, record: OwnedActor) -> None:
        """Retain the native handle before a raw-ID journal write can fail."""
        self._handles[record.actor_id] = actor
        self._owned[record.actor_id] = record

    def assign_controller(self, actor_id: int, controller: str) -> None:
        """Reject adoption or arbitration by a second controller."""
        existing = self._owned.get(actor_id)
        if existing is None or existing.controller != controller:
            message = "Actor is not assigned to this controller; adoption is unsupported."
            raise SessionInvariantError(message)

    def destroy_actor(self, actor_id: int) -> None:
        """Explicit mid-run destruction cannot remove a protected actor."""
        self.assert_current()
        record = self._owned[actor_id]
        if record.protected:
            message = "Cannot destroy a protected actor during the experiment."
            raise SessionInvariantError(message)
        error = self._destroy(record)
        if error:
            raise SessionInvariantError(error)
        del self._owned[actor_id]
        self._journal()

    def on_close(self, callback: Callable[[], object]) -> None:
        """Drain/close subscriptions before destroying actors or restoring the world."""
        self._closing_callbacks.append(callback)

    def _journal(self) -> None:
        self.lease.mark_dirty(
            {
                "kind": "managed",
                "run_id": self.run_id,
                "world_id": self.world_id,
                "world_generation": self.world_generation,
                "settings": self._original_settings,
                "actors": [asdict(actor) for actor in self._owned.values()],
                **self.creation.fields(),
                **self.reload.fields(),
            }
        )

    def close(self) -> dict[str, object]:
        """Return truthful, idempotent cleanup; failures leave the endpoint quarantined."""
        if self._cleanup is None:
            self._cleanup = self._close_once()
        return self._cleanup.copy()

    def _close_once(self) -> dict[str, object]:
        failures = self.creation.failures() + self.reload.failures()
        trailing = self._close_subscriptions(failures)
        if not self._opened:
            return {"ok": not failures, "failures": failures, "trailing": trailing}
        try:
            report = self._restore_world(failures)
        except (RuntimeError, OSError, ValueError) as exc:
            failures.append(str(exc))
            report = {
                "world_replaced": None,
                "world_identity_checked": False,
                "settings_restored": False,
            }
        report.update(ok=not failures, failures=failures, trailing=trailing)
        if not failures:
            self.lease.mark_clean()
        return report

    def _close_subscriptions(self, failures: list[str]) -> list[object]:
        trailing = []
        for callback in reversed(self._closing_callbacks):
            try:
                trailing.append(callback())
            except (RuntimeError, OSError, ValueError) as exc:
                failures.append(f"subscription close: {exc}")
        return trailing

    def _restore_world(self, failures: list[str]) -> dict[str, object]:
        if self.reload.failures():
            return {
                "world_replaced": None,
                "world_identity_checked": False,
                "settings_restored": False,
                "reload_unresolved": True,
            }
        current = self.client.get_world()
        if world_identity(current) != self.world_id:
            return check_replaced_world(self.client, current, self._original_settings, failures)
        if self._recovering:
            self._refresh_recovery_snapshot()
        self._destroy_owned(failures)
        return self._restore_settings(failures)

    def _restore_settings(self, failures: list[str]) -> dict[str, object]:
        """Recheck episode authority immediately before the settings mutation."""
        if world_identity(self.client.get_world()) != self.world_id:
            message = "The world was replaced during actor cleanup; settings restore refused."
            failures.append(message)
            return {"world_replaced": True, "settings_restored": False}
        apply_world_settings(
            self.world,
            self._original_settings,
            before_apply=partial(require_setup_episode, self.client, self.world_id),
        )
        restored = world_settings(self.world) == self._original_settings
        if not restored:
            failures.append("World settings restoration could not be verified.")
        return {
            "world_replaced": False,
            "settings_restored": restored,
            "recovery_frame": self._recovery_frame,
        }

    def _destroy_owned(self, failures: list[str]) -> None:
        for record in reversed(tuple(self._owned.values())):
            error = self._destroy(record)
            if error:
                failures.append(error)
            else:
                self.creation.confirmed_destroyed(record.actor_id)

    def _refresh_recovery_snapshot(self) -> None:
        """Publish one cleanup frame only after the dead worker's lease is reacquired."""
        frame = fresh_cleanup_frame(
            self.world,
            failure_message="Recovery actor snapshot did not reach the requested cleanup frame.",
        )
        if world_identity(self.client.get_world()) != self.world_id:
            message = "The world was replaced while refreshing recovery actor state."
            raise SessionInvariantError(message)
        self._recovery_frame = frame

    def _destroy(self, record: OwnedActor) -> str | None:
        actor = self._actor_handle(record.actor_id)
        if actor is not None and not _actor_matches(actor, record):
            return f"Actor {record.actor_id} identity changed; destruction refused."
        result = destroy_authoritatively(
            record.actor_id,
            expected_world_id=self.world_id,
            current_world_id=lambda: world_identity(self.client.get_world()),
            apply_batch=partial(experiment_replay.apply_batch, self.client),
        )
        if destroy_result_cleaned(result):
            return None
        return f"Actor {record.actor_id}: {result.error}"

    def _actor_handle(self, actor_id: int) -> CarlaActor | None:
        actor = self._handles.get(actor_id)
        if actor is None:
            actor = cast("CarlaActor | None", self.world.get_actors().find(actor_id))
        return actor

    @classmethod
    def recover(
        cls,
        client: CarlaClient,
        lease: SimulatorLease,
        spec: ExperimentSpec,
    ) -> dict[str, object]:
        """Recover a dead worker while the original endpoint lease remains exclusive."""
        state = lease.recovery_state
        if state.get("kind") != "managed":
            message = "Recovery state does not describe a managed experiment."
            raise SessionInvariantError(message)
        session = cls(spec, client, lease, str(state["run_id"]))
        session.world_id = int(str(state["world_id"]))
        session._original_settings = cast("dict[str, object]", state["settings"])
        session.creation = ManagedCreationJournal(session.world_id, session._journal)
        session.creation.load(state)
        session.reload.load(state.get("reload"), session.world_id)
        session._owned = recover_owned(state, session.creation)
        session._opened = True
        session._recovering = True
        return session.close()


def recover_managed_session(
    client: CarlaClient,
    lease: SimulatorLease,
    spec: ExperimentSpec,
) -> dict[str, object]:
    """Recover a dead worker under the same exclusive lease before any new run."""
    return ManagedSession.recover(client, lease, spec)


def _actor_matches(actor: CarlaActor, record: OwnedActor) -> bool:
    return (
        actor.type_id == record.type_id
        and actor.attributes.get("role_name", "") == record.role_name
    )
