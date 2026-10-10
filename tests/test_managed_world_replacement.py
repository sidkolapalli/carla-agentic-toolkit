"""An external episode cannot become clean without a stable read-only settings check."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import experiment_replay
from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_managed_session import FakeWorld, _apply_destroy_batch

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaClient

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Managed leases require Linux")
RUN_ID = "replacement-check"
MISMATCHES: list[tuple[str, object]] = [
    ("synchronous_mode", True),
    ("fixed_delta_seconds", 0.05),
    ("no_rendering_mode", True),
    ("substepping", False),
    ("max_substeps", 5),
    ("max_substep_delta_time", 0.02),
]


@dataclass
class ReplacementWorld(FakeWorld):
    """Expose reads while making every forbidden replacement-world action observable."""

    read_error: type[Exception] | None = None
    on_read: Callable[[], None] | None = None
    actions: list[str] = field(default_factory=list)

    def get_settings(self) -> SimpleNamespace:
        """Return one coherent settings value, with optional read or identity failure."""
        self.actions.append("read-settings")
        if self.read_error is not None:
            message = "replacement settings unavailable"
            raise self.read_error(message)
        values = super().get_settings()
        if self.on_read is not None:
            self.on_read()
        return values

    def apply_settings(self, settings: SimpleNamespace) -> int:
        """Record writes that must never be attempted in an unknown episode."""
        self.actions.append("apply-settings")
        return super().apply_settings(settings)

    def tick(self) -> int:
        """Record an owner tick that is forbidden after external replacement."""
        self.actions.append("tick")
        return super().tick()

    def wait_for_tick(self, _seconds: float) -> SimpleNamespace:
        """Record frame waits so recovery cannot silently use setup preflight."""
        self.actions.append("wait")
        return super().wait_for_tick(_seconds)

    def get_actors(self) -> object:
        """Record inventory queries rather than allowing old actor ownership to rebind."""
        self.actions.append("actors")
        return super().get_actors()


@dataclass
class ReplacingClient:
    """Return the actual current episode rather than the session's retained handle."""

    world: FakeWorld = field(default_factory=FakeWorld)

    def get_world(self) -> FakeWorld:
        """Permit external clients to replace the episode independently of the session."""
        return self.world


@pytest.fixture
def batch(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Observe the authoritative destruction boundary separately from actor handles."""
    callback = Mock(side_effect=_apply_destroy_batch)
    monkeypatch.setattr(experiment_replay, "apply_batch", callback)
    return callback


def running_session(client: ReplacingClient, lease: SimulatorLease) -> ManagedSession:
    """Journal originals and a known actor through the real managed setup lifecycle."""
    session = ManagedSession(ExperimentSpec(), cast("CarlaClient", client), lease, RUN_ID)
    session.open()
    actor = SimpleNamespace(
        id=11,
        type_id="vehicle.test",
        attributes={"role_name": f"managed:{RUN_ID}:ego"},
        destroy=Mock(return_value=True),
    )
    client.world.actors.append(actor)
    session.own(cast("CarlaActor", actor), controller="ego")
    return session


def finish(
    boundary: str, session: ManagedSession, client: ReplacingClient, lease: SimulatorLease
) -> dict[str, object]:
    """Exercise both retained-handle close and a newly constructed recovery session."""
    if boundary == "close":
        return session.close()
    return ManagedSession.recover(cast("CarlaClient", client), lease, ExperimentSpec())


def replace_with_originals(client: ReplacingClient, lease: SimulatorLease) -> ReplacementWorld:
    """Keep fresh episode actor IDs separate from a matching old settings baseline."""
    replacement = ReplacementWorld(
        id=client.world.id + 1,
        settings=cast("dict[str, object]", lease.recovery_state["settings"]).copy(),
        actors=[
            SimpleNamespace(id=11, type_id="vehicle.external", attributes={"role_name": "external"})
        ],
    )
    client.world = replacement
    return replacement


def assert_read_only_replacement(world: ReplacementWorld, batch: Mock) -> None:
    """Only settings reads are authorized; old IDs cannot control this episode."""
    assert world.actions == ["read-settings"]
    assert world.destroyed == []
    batch.assert_not_called()


def assert_dirty(report: dict[str, object], lease: SimulatorLease, *, checked: bool) -> None:
    """Failure evidence must agree with the retained endpoint quarantine."""
    assert (report["ok"], report["world_replaced"], report["settings_restored"]) == (
        False,
        True,
        False,
    )
    assert report["settings_checked"] is checked
    assert report["failures"]
    assert lease.recovery_state


@pytest.mark.parametrize("boundary", ["close", "recover"])
@pytest.mark.parametrize(("setting", "value"), MISMATCHES)
def test_each_replacement_setting_mismatch_keeps_endpoint_dirty(
    tmp_path: Path, batch: Mock, boundary: str, setting: str, value: object
) -> None:
    """None of the six captured fields can be skipped when releasing a replaced episode."""
    client = ReplacingClient()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = running_session(client, lease)
        replacement = replace_with_originals(client, lease)
        replacement.settings[setting] = value

        report = finish(boundary, session, client, lease)

        assert_dirty(report, lease, checked=True)
        assert_read_only_replacement(replacement, batch)


@pytest.mark.parametrize("boundary", ["close", "recover"])
def test_stable_full_replacement_match_may_release_endpoint_without_mutation(
    tmp_path: Path, batch: Mock, boundary: str
) -> None:
    """Matching settings are checked, not rewritten, while old actor IDs remain unusable."""
    client = ReplacingClient()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = running_session(client, lease)
        replacement = replace_with_originals(client, lease)

        report = finish(boundary, session, client, lease)

        assert (
            report["ok"],
            report["world_replaced"],
            report["settings_checked"],
            report["settings_restored"],
        ) == (True, True, True, True)
        assert (report["failures"], lease.recovery_state) == ([], {})
        assert_read_only_replacement(replacement, batch)


def test_stable_settings_match_cannot_hide_subscription_cleanup_failure(
    tmp_path: Path, batch: Mock
) -> None:
    """An independently failed listener close still prevents endpoint release."""
    client = ReplacingClient()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = running_session(client, lease)
        session.on_close(Mock(side_effect=RuntimeError("listener close failed")))
        replacement = replace_with_originals(client, lease)

        report = session.close()

        assert (report["ok"], report["settings_checked"], report["settings_restored"]) == (
            False,
            True,
            True,
        )
        assert "listener close failed" in str(report["failures"])
        assert lease.recovery_state
        assert_read_only_replacement(replacement, batch)


@pytest.mark.parametrize("boundary", ["close", "recover"])
def test_reload_retaining_managed_sync_settings_keeps_endpoint_dirty(
    tmp_path: Path, batch: Mock, boundary: str
) -> None:
    """Model reload_world(False), preserving the actual active managed settings."""
    client = ReplacingClient()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = running_session(client, lease)
        managed_settings = client.world.settings.copy()
        replacement = replace_with_originals(client, lease)
        replacement.settings = managed_settings

        report = finish(boundary, session, client, lease)

        assert_dirty(report, lease, checked=True)
        assert replacement.settings == managed_settings
        assert_read_only_replacement(replacement, batch)


@pytest.mark.parametrize("boundary", ["close", "recover"])
@pytest.mark.parametrize("error", [AttributeError, OSError, RuntimeError, TypeError, ValueError])
def test_unreadable_replacement_settings_keep_endpoint_dirty(
    tmp_path: Path, batch: Mock, boundary: str, error: type[Exception]
) -> None:
    """A native read failure is cleanup failure, never absence of restoration work."""
    client = ReplacingClient()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = running_session(client, lease)
        replacement = replace_with_originals(client, lease)
        replacement.read_error = error

        report = finish(boundary, session, client, lease)

        assert_dirty(report, lease, checked=False)
        assert_read_only_replacement(replacement, batch)


@pytest.mark.parametrize("boundary", ["close", "recover"])
def test_missing_replacement_setting_capability_keeps_endpoint_dirty(
    tmp_path: Path, batch: Mock, boundary: str
) -> None:
    """Partial capabilities cannot stand in for a full six-field check."""
    client = ReplacingClient()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = running_session(client, lease)
        replacement = replace_with_originals(client, lease)
        del replacement.settings["max_substep_delta_time"]

        report = finish(boundary, session, client, lease)

        assert_dirty(report, lease, checked=False)
        assert_read_only_replacement(replacement, batch)


@pytest.mark.parametrize("boundary", ["close", "recover"])
def test_identity_change_during_replacement_settings_read_keeps_endpoint_dirty(
    tmp_path: Path, batch: Mock, boundary: str
) -> None:
    """A matching stale snapshot cannot authorize a newly changed episode."""
    client = ReplacingClient()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = running_session(client, lease)
        replacement = replace_with_originals(client, lease)
        replacement.on_read = lambda: setattr(replacement, "id", replacement.id + 1)

        report = finish(boundary, session, client, lease)

        assert_dirty(report, lease, checked=False)
        assert_read_only_replacement(replacement, batch)


@pytest.mark.parametrize("boundary", ["close", "recover"])
@pytest.mark.parametrize(("setting", "value"), [("synchronous_mode", 0), ("max_substeps", 10.0)])
def test_equal_python_values_with_different_native_types_do_not_release_endpoint(
    tmp_path: Path, batch: Mock, boundary: str, setting: str, value: object
) -> None:
    """Python's False == 0 and int == float do not prove a native settings match."""
    client = ReplacingClient()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = running_session(client, lease)
        replacement = replace_with_originals(client, lease)
        replacement.settings[setting] = value

        report = finish(boundary, session, client, lease)

        assert_dirty(report, lease, checked=True)
        assert_read_only_replacement(replacement, batch)


@pytest.mark.parametrize("boundary", ["close", "recover"])
def test_same_episode_cleanup_still_restores_settings_and_owned_actors(
    tmp_path: Path, batch: Mock, boundary: str
) -> None:
    """The fail-closed replacement policy does not weaken normal managed cleanup."""
    client = ReplacingClient()
    original = client.world.settings.copy()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = running_session(client, lease)

        report = finish(boundary, session, client, lease)

        assert (report["ok"], report["world_replaced"], report["settings_restored"]) == (
            True,
            False,
            True,
        )
        assert (client.world.settings, client.world.destroyed) == (original, [11])
        assert lease.recovery_state == {}
        batch.assert_called_once()
