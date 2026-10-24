"""Fresh-lease recovery preserves acknowledged reload identities and unknown-outcome quarantine."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient


from tests.managed_reload_helpers import (
    RELOADED_WORLD_ID,
    _assert_journal_call_counts,
    _assert_no_reload_setup,
    _assert_quarantined,
    _assert_reload_cleanup,
    _case,
    _interrupt_reload,
    _session,
)


@pytest.mark.parametrize("window", ["lost_reply", "acknowledged"])
def test_fresh_lease_recovery_distinguishes_unknown_and_acknowledged_reload(
    tmp_path: Path, window: str
) -> None:
    """Crash recovery can write only a durably acknowledged replacement episode."""
    old, new, client = _case()
    original = old.settings.copy()
    if window == "lost_reply":
        client.reload_error = "reload reply lost"
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        _interrupt_reload(session, window)
        saved = deepcopy(lease.recovery_state)
    new.events.clear()
    with SimulatorLease("localhost", 3000, state_root=tmp_path, recovering=True) as lease:
        assert lease.recovery_state == saved
        report = ManagedSession.recover(cast("CarlaClient", client), lease, ExperimentSpec())
        _assert_reload_cleanup(report, new, lease, original, window)
    if window == "lost_reply":
        _assert_quarantined(tmp_path)


def test_unknown_reload_cannot_clear_even_if_replacement_matches_baseline(tmp_path: Path) -> None:
    """Read-only #139 equality does not establish the outcome of a lost mutation reply."""
    old, new, client = _case()
    original = old.settings.copy()
    client.reload_error = "reload reply lost"
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        with pytest.raises(RuntimeError, match="reload reply lost"):
            session.open()
        new.settings = original.copy()
        report = session.close()
        assert report["ok"] is False
        failures = cast("list[str]", report["failures"])
        assert any("reload" in error.lower() for error in failures)
    _assert_quarantined(tmp_path)


@pytest.mark.parametrize("phase", ["prepared", "pending", "acknowledged"])
def test_reload_journal_failure_is_sticky_and_blocks_later_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Neither pre-call nor returned-ID durability failure can manufacture a clean result."""
    old, new, client = _case()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        write = lease.mark_dirty

        def fail_write(state: dict[str, object]) -> None:
            value = state.get("reload")
            if isinstance(value, dict) and value.get("phase") == phase:
                message = "reload journal unavailable"
                raise OSError(message)
            write(state)

        monkeypatch.setattr(lease, "mark_dirty", fail_write)
        with pytest.raises(OSError, match="reload journal unavailable"):
            session.open()
        _assert_no_reload_setup(new)
        _assert_journal_call_counts(old, client, phase)
        if phase == "acknowledged":
            new.settings = cast("dict[str, object]", lease.recovery_state["settings"]).copy()
        assert session.close()["ok"] is False


def test_acknowledged_id_survives_failed_post_reload_map_rpc(tmp_path: Path) -> None:
    """Known returned identity is sufficient for same-episode settings cleanup."""
    old, new, client = _case()
    original = old.settings.copy()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)

        def unavailable_map() -> None:
            message = "map read unavailable"
            raise RuntimeError(message)

        new.on_map = unavailable_map
        with pytest.raises(RuntimeError, match="map read unavailable"):
            session.open()
        assert lease.recovery_state["world_id"] == RELOADED_WORLD_ID
        assert ("reset", 8) not in new.events
    new.on_map = None
    with SimulatorLease("localhost", 3000, state_root=tmp_path, recovering=True) as lease:
        report = ManagedSession.recover(cast("CarlaClient", client), lease, ExperimentSpec())
        assert report["ok"] is True
        assert new.settings == original


def test_prepared_settings_window_recovers_original_episode_without_reload(tmp_path: Path) -> None:
    """A crash before the native reload call is not misreported as an unknown reply."""
    old, _new, client = _case()
    original = old.settings.copy()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        lease.mark_dirty(
            {
                "kind": "managed",
                "run_id": "prepared-crash",
                "world_id": 7,
                "settings": original,
                "actors": [],
                "spawn_journal_version": 1,
                "spawn_intents": [],
                "reload": {
                    "phase": "prepared",
                    "previous_world_id": 7,
                    "previous_map_name": "Town10HD_Opt",
                },
            }
        )
        old.settings.update(synchronous_mode=True, fixed_delta_seconds=0.05)
    with SimulatorLease("localhost", 3000, state_root=tmp_path, recovering=True) as lease:
        assert ManagedSession.recover(cast("CarlaClient", client), lease, ExperimentSpec())["ok"]
        assert old.settings == original
        assert client.calls == []
        assert not lease.recovery_state


@pytest.mark.parametrize("phase", [{}, ["acknowledged"]])
def test_malformed_reload_phase_is_quarantined_without_reload(
    tmp_path: Path, phase: object
) -> None:
    """Malformed durable phase data is not guessed or repaired into authority."""
    old, _new, client = _case()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        lease.mark_dirty(
            {
                "kind": "managed",
                "run_id": "invalid-phase",
                "world_id": 7,
                "settings": old.settings.copy(),
                "actors": [],
                "spawn_journal_version": 1,
                "spawn_intents": [],
                "reload": {"phase": phase, "previous_world_id": 7},
            }
        )
        report = ManagedSession.recover(cast("CarlaClient", client), lease, ExperimentSpec())
        assert report["ok"] is False
        assert lease.recovery_state
        assert client.calls == []
