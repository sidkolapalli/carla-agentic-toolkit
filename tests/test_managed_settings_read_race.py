"""A settings lookup cannot carry managed write authority into a new episode."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.managed_world import SessionInvariantError
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.managed_reload_helpers import ReloadWorld, _assert_quarantined, _case, _session

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from tests.managed_reload_helpers import ReloadClient


@pytest.mark.parametrize("phase", ["startup", "close", "recover"])
def test_settings_read_replacement_refuses_managed_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Replace only during the lookup immediately preceding the settings write."""
    old, new, client = _case()
    replacement = ReloadWorld(id=9, events=old.events)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        if phase != "startup":
            session.open()
        current = old if phase == "startup" else new
        get_settings = current.get_settings
        reads = 0

        def replace_on_write_lookup() -> object:
            nonlocal reads
            settings = get_settings()
            reads += 1
            if reads == (1 if phase == "close" else 2):
                current.events.append(("replace", current.id))
                client.world = replacement
            return settings

        monkeypatch.setattr(current, "get_settings", replace_on_write_lookup)
        _finish_session(session, client, lease, phase)
        _assert_no_late_write(current)
        assert replacement.settings["synchronous_mode"] is False
        assert lease.recovery_state
    _assert_quarantined(tmp_path)


def _finish_session(
    session: ManagedSession, client: ReloadClient, lease: SimulatorLease, phase: str
) -> None:
    if phase == "startup":
        with pytest.raises(SessionInvariantError, match="replaced"):
            session.open()
        assert client.calls == []
        return
    report = (
        ManagedSession.recover(cast("CarlaClient", client), lease, ExperimentSpec())
        if phase == "recover"
        else session.close()
    )
    _assert_restore_refused(report)


def _assert_restore_refused(report: dict[str, object]) -> None:
    assert report["ok"] is False
    assert report["settings_restored"] is False
    _assert_identity_unobserved(report)


def _assert_identity_unobserved(report: dict[str, object]) -> None:
    assert report["world_replaced"] is None
    assert report["world_identity_checked"] is False


def _assert_no_late_write(world: ReloadWorld) -> None:
    marker = world.events.index(("replace", world.id))
    assert not any(
        name in {"apply", "tick", "reset", "reload"} for name, _ in world.events[marker + 1 :]
    )
