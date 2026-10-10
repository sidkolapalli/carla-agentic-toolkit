"""Bounded, owned-only background work at the managed owner's current frame."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.managed_creation import CreationOptions, OwnedActor, spawn_managed
from carla_agentic_toolkit.managed_world import SessionInvariantError
from carla_agentic_toolkit.traffic_runtime import _traffic_vehicle_blueprints

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import (
        CarlaActor,
        CarlaBlueprint,
        CarlaTelemetrySnapshot,
    )
    from carla_agentic_toolkit.managed_session import ManagedSession
    from carla_agentic_toolkit.managed_spec import ManagedDensitySpec

CONTROLLER = "managed-density"
MAX_SPAWN_ATTEMPTS = 4
MAX_MISSING_REMOVALS = 4


class ManagedDensity:
    """Never adopt existing actors, run a worker thread, or advance the world."""

    def __init__(
        self, session: ManagedSession, config: ManagedDensitySpec, guard: Callable[[], None]
    ) -> None:
        """Retain explicit target/work budgets and one trusted owner-boundary guard."""
        self.session, self.config, self.guard = session, config, guard
        self._created_frames: dict[int, int] = {}
        self._acknowledged: set[int] = set()
        self._attempt = 0
        self._steps = 0
        self._failure: str | None = None
        self.spawn_attempts = 0
        self.removals = 0

    def fields(self) -> dict[str, object]:
        """Registration ACKs are not a measured or deterministic population claim."""
        return {
            "background_density": {
                "configured_vehicle_count": self.config.vehicle_count,
                "acknowledged_vehicle_count": len(self._acknowledged),
                "retained_actor_ids": sorted(self._created_frames),
                "max_spawn_attempts_per_boundary": MAX_SPAWN_ATTEMPTS,
                "max_missing_removals_per_boundary": MAX_MISSING_REMOVALS,
                "maintenance_interval_steps": self.config.maintenance_interval_steps,
                "spawn_attempts": self.spawn_attempts,
                "confirmed_missing_removals": self.removals,
                "failures": self.failures(),
            }
        }

    def actors(self) -> tuple[CarlaActor, ...]:
        """Return only raw-returned actors still owned by this controller."""
        return tuple(
            self.session._handles[identity]  # noqa: SLF001 -- Raw returned handles owned by this session.
            for identity in self._created_frames
        )

    def failures(self) -> list[str]:
        """Expose sticky runtime uncertainty independently of cleanup acknowledgements."""
        return [self._failure] if self._failure is not None else []

    def before_step(self) -> None:
        """Run maintenance only at the configured sole-owner interval."""
        self._require_available()
        self.guard()
        self._steps += 1
        if self._steps % self.config.maintenance_interval_steps == 0:
            self.maintain()

    def maintain(self) -> None:
        """Each boundary has independent spawn and authoritative removal budgets."""
        self._require_available()
        try:
            self._maintain()
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as error:
            self._remember_failure(error)
            raise

    def _maintain(self) -> None:
        self.guard()
        snapshot = cast("CarlaTelemetrySnapshot", self.session.world.get_snapshot())
        self.guard()
        self._remove_missing(snapshot)
        self._populate()
        self._save()

    def _remove_missing(self, snapshot: CarlaTelemetrySnapshot) -> None:
        candidates = (
            identity
            for identity, created in self._created_frames.items()
            if created < snapshot.frame and snapshot.find(identity) is None
        )
        for identity in tuple(candidates)[:MAX_MISSING_REMOVALS]:
            self.guard()
            self.session.destroy_actor(identity)
            del self._created_frames[identity]
            self._acknowledged.discard(identity)
            self.removals += 1

    def _populate(self) -> None:
        self.guard()
        blueprints = _traffic_vehicle_blueprints(self.session.world, safe_filter=True)
        points = self.session.map.get_spawn_points()
        self.guard()
        if not points:
            message = "Managed density requires native map spawn points."
            raise SessionInvariantError(message)
        for _index in range(MAX_SPAWN_ATTEMPTS):
            if len(self._created_frames) >= self.config.vehicle_count:
                break
            blueprint = blueprints[self._attempt % len(blueprints)]
            point = points[self._attempt % len(points)]
            self._attempt += 1
            role = f"managed:{self.session.run_id}:background-{self._attempt}"
            blueprint.set_attribute("role_name", role)
            self._spawn(blueprint, point, role)

    def _spawn(self, blueprint: CarlaBlueprint, point: object, role: str) -> None:
        self.guard()
        self.spawn_attempts += 1
        actor = spawn_managed(
            self.session,
            blueprint,
            point,
            CreationOptions(role, CONTROLLER, protected=False, try_spawn=True),
            before_spawn=self.guard,
        )
        if actor is not None:
            self._created_frames[actor.id] = self.session.expected_frame
            self.session.assign_controller(actor.id, CONTROLLER)
            self._save()
            self.guard()
            enabled = True
            actor.set_autopilot(enabled, self.config.traffic_manager_port)
            self.guard()
            self._acknowledged.add(actor.id)
            self._save()

    def unregister(self, record: OwnedActor) -> None:
        """Only an explicit density assignment authorizes its own unregister call."""
        if record.controller != CONTROLLER or record.protected:
            return
        self.guard()
        actor = self.session._actor_handle(record.actor_id)  # noqa: SLF001 -- Retained original ownership handle.
        self.guard()
        if actor is not None:
            _require_identity(actor, record)
            enabled = False
            actor.set_autopilot(enabled, self.config.traffic_manager_port)
            self.guard()

    def _require_available(self) -> None:
        if self._failure is not None:
            message = "Managed density state is uncertain; further runtime mutations are frozen."
            raise SessionInvariantError(message)

    def _remember_failure(self, error: Exception) -> None:
        self._failure = f"Managed density state is uncertain: {error}"[:512]
        with suppress(RuntimeError, OSError, ValueError):
            self._save()

    def _save(self) -> None:
        try:
            self.session._journal()  # noqa: SLF001 -- Share the session's single durable journal.
        except (OSError, RuntimeError, ValueError) as error:
            self._failure = f"Managed density evidence persistence failed: {error}"[:512]
            raise


def _require_identity(actor: CarlaActor, record: OwnedActor) -> None:
    if actor.type_id != record.type_id or actor.attributes.get("role_name", "") != record.role_name:
        message = f"Managed density actor {record.actor_id} identity changed; unregister refused."
        raise SessionInvariantError(message)
