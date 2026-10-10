"""Join optional local-TM authority to the existing managed session lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.managed_density import ManagedDensity
from carla_agentic_toolkit.managed_setup import require_setup_episode
from carla_agentic_toolkit.managed_tm import ManagedTrafficManager, failures_from_state
from carla_agentic_toolkit.managed_world import SessionInvariantError, world_identity

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaActor
    from carla_agentic_toolkit.managed_creation import OwnedActor
    from carla_agentic_toolkit.managed_session import ManagedSession


class ManagedBackground:
    """Disabled sessions keep their original world and serialization contracts."""

    def __init__(self, session: ManagedSession) -> None:
        """Retain the session's optional host and owned-only density state."""
        self.session = session
        self.host: ManagedTrafficManager | None = None
        self.density: ManagedDensity | None = None
        self._recovery_state: dict[str, object] = {}

    def fields(self) -> dict[str, object]:
        """Preserve both live and recovered host evidence in the existing lease journal."""
        host = self.host.fields() if self.host is not None else self._recovery_fields()
        density = self.density.fields() if self.density is not None else {}
        return host | density

    def open(self) -> None:
        """Prove a private async host before managed world setup mutates settings."""
        config = self.session.spec.background_density
        if config is not None:
            if self.session._original_settings.get("synchronous_mode") is not False:  # noqa: SLF001 -- Session-owned baseline.
                message = "Managed density requires an observed asynchronous dedicated world."
                raise SessionInvariantError(message)
            self.host = ManagedTrafficManager(
                self.session.client,
                port=config.traffic_manager_port,
                world_id=self.session.world_id,
                run_id=self.session.run_id,
                state_root=self.session.lease.state_root,
                persist=self.session._journal,  # noqa: SLF001 -- One durable session journal.
            )
            self.session._traffic_manager_host = self.host  # noqa: SLF001 -- Retain uncertain host for worker lifetime.
            self.host.open()

    def rebind(self, acknowledged_world_id: int) -> None:
        """Bind only an explicitly acknowledged reload while preserving host provenance."""
        if self.host is not None:
            self.host.rebind_world(acknowledged_world_id, self._current_world_id)

    def require_setup_owner(self) -> None:
        """Recheck episode and optional host immediately before native world setup."""
        require_setup_episode(self.session.client, self.session.world_id)
        if self.host is not None:
            self.host.assert_owned(self._current_world_id)

    def prepare(self) -> None:
        """Enable local synchronization and bounded population after fixture setup."""
        self.session.assert_current()
        config = self.session.spec.background_density
        if config is None or self.density is not None:
            return
        if self.host is None:
            message = "Managed density has no proven local Traffic Manager."
            raise SessionInvariantError(message)
        self.host.enable_sync(self._current_world_id)
        self._guard()
        self.density = ManagedDensity(self.session, config, self._guard)
        self.density.maintain()

    def before_step(self) -> None:
        """Prove async setup or sync maintenance before the sole owner advances."""
        if self.host is not None:
            self._guard()
        if self.density is not None:
            self.density.before_step()
            self._guard()

    def actors(self) -> tuple[CarlaActor, ...]:
        """Expose only retained background handles, never adopt scene actors."""
        return self.density.actors() if self.density is not None else ()

    def status(self) -> dict[str, object]:
        """Report requested density separately from registration acknowledgements."""
        if self.density is None:
            return {}
        return cast("dict[str, object]", self.density.fields()["background_density"])

    def failures(self) -> list[str]:
        """Keep live uncertainty and unresolved recovered provenance fail-closed."""
        host = (
            self.host.failures()
            if self.host is not None
            else failures_from_state(self._recovery_state)
        )
        density = self.density.failures() if self.density is not None else []
        return host + density

    def unregister(self, record: OwnedActor, failures: list[str]) -> None:
        """Collect cleanup failure without hiding later authoritative owned cleanup."""
        if self.density is not None:
            try:
                self.density.unregister(record)
            except (RuntimeError, OSError, ValueError) as error:
                failures.append(f"Managed density unregister {record.actor_id}: {error}")

    def close_host(self, failures: list[str]) -> bool:
        """Permit settings restoration only after the proven host is verified closed."""
        if self.host is None:
            return not failures_from_state(self._recovery_state)
        report = self.host.close(self._current_world_id)
        failures.extend(
            message for message in cast("list[str]", report["failures"]) if message not in failures
        )
        return report["ok"] is True

    def load(self, state: dict[str, object]) -> None:
        """Retain inconsistent declarations so recovery cannot erase uncertainty."""
        self._recovery_state = state.copy()

    def _recovery_fields(self) -> dict[str, object]:
        return {
            key: self._recovery_state[key]
            for key in ("managed_traffic_manager", "background_density")
            if key in self._recovery_state
        }

    def _current_world_id(self) -> int:
        return world_identity(self.session.client.get_world())

    def _guard(self) -> None:
        self.session.assert_current()
        if self.host is None:
            message = "Managed density has no proven local Traffic Manager."
            raise SessionInvariantError(message)
        self.host.assert_owned(self._current_world_id)
        self.session.assert_current()


class ManagedBackgroundMixin:
    """Expose optional owned-background operations without changing the session clock."""

    background: ManagedBackground

    def prepare_background(self) -> None:
        """Enable proven-local sync only after the fixture has completed its setup."""
        self.background.prepare()

    def background_actors(self) -> tuple[CarlaActor, ...]:
        """Expose owned background handles for same-frame range-filtered observations."""
        return self.background.actors()

    def density_status(self) -> dict[str, object]:
        """Distinguish the requested target from acknowledged registration and bounded work."""
        return self.background.status()
