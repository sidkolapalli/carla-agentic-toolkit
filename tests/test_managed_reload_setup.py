"""Managed setup refuses stale publication, missing native capabilities and changed episodes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from carla_agentic_toolkit import managed_session
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from pathlib import Path


from tests.managed_reload_helpers import (
    ReloadWorld,
    _case,
    _session,
)


@pytest.mark.parametrize("fault", ["reset", "stale", "replacement", "wrong_map"])
def test_setup_refuses_unverified_reset_or_replacement(tmp_path: Path, fault: str) -> None:
    """Setup errors cannot be followed by light inventory or scheduled mutations."""
    _old, new, client = _case()
    if fault == "reset":
        new.reset_error = "reset unavailable"
    elif fault == "stale":
        new.extra_tick = -1
    elif fault == "replacement":
        new.on_tick = lambda: setattr(client, "world", ReloadWorld(id=9))
    else:
        new.map_name = "TownOther"
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        with pytest.raises(RuntimeError):
            session.open()
        assert ("actors", 8) not in new.events
        with pytest.raises(RuntimeError):
            session.step()
        session.close()


@pytest.mark.parametrize("capability", ["reset", "state"])
def test_missing_native_light_capability_refuses_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability: str
) -> None:
    """Unsupported native APIs cannot produce fabricated reset metadata."""
    _old, new, client = _case()
    if capability == "reset":
        monkeypatch.setattr(new, "reset_all_traffic_lights", None)
    else:
        monkeypatch.setattr(new.light, "get_state", None)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        with pytest.raises(RuntimeError, match=r"traffic.light"):
            session.open()
        with pytest.raises(RuntimeError):
            session.step()
        session.close()


@pytest.mark.parametrize("raw_id", [True, "8", None, 7])
def test_invalid_or_unchanged_returned_episode_cannot_authorize_setup(
    tmp_path: Path, raw_id: object
) -> None:
    """Only an exact native new integer identity is an acknowledged replacement."""
    _old, new, client = _case()
    cast("Any", new).id = raw_id
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        with pytest.raises(RuntimeError, match=r"identity|episode"):
            session.open()
        assert ("reset", raw_id) not in new.events
        assert session.close()["ok"] is False


@pytest.mark.parametrize("phase", ["prepared", "pending"])
def test_journal_write_episode_change_refuses_next_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """The durable intent callback cannot authorize a subsequently replaced episode."""
    old, _new, client = _case()
    replacement = ReloadWorld(id=9)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        write = lease.mark_dirty

        def replace_after_write(state: dict[str, object]) -> None:
            write(state)
            value = state.get("reload")
            if isinstance(value, dict) and value.get("phase") == phase:
                client.world = replacement

        monkeypatch.setattr(lease, "mark_dirty", replace_after_write)
        with pytest.raises(RuntimeError, match="replaced"):
            session.open()
        assert client.calls == []
        if phase == "prepared":
            assert ("apply", 7) not in old.events
        assert replacement.events == []
        session.close()


@pytest.mark.parametrize("state", [None, 0])
def test_unavailable_native_state_is_not_reported_as_a_reset_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: object
) -> None:
    """A callable getter is insufficient if its result cannot name a native light state."""
    _old, new, client = _case()
    monkeypatch.setattr(new.light, "get_state", lambda: state)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        with pytest.raises(RuntimeError, match=r"traffic.light"):
            session.open()
        session.close()


def test_light_metadata_frame_drift_refuses_runtime_start(tmp_path: Path) -> None:
    """Getters delivered after a competing tick cannot be labelled with the reset frame."""
    _old, new, client = _case()
    new.light.on_state = lambda: setattr(new, "frame", new.frame + 1)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        with pytest.raises(RuntimeError, match=r"frame|publication"):
            session.open()
        with pytest.raises(RuntimeError):
            session.step()
        session.close()


def test_reset_snapshot_episode_change_refuses_native_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A snapshot read revealing replacement cannot authorize the next mutating RPC."""
    _old, new, client = _case()
    replacement = ReloadWorld(id=9)

    def settled(*_args: object) -> dict[str, object]:
        return {"settled_frame": new.frame}

    def replacing_snapshot() -> SimpleNamespace:
        client.world = replacement
        return SimpleNamespace(frame=new.frame)

    monkeypatch.setattr(managed_session, "settle_setup_frames", settled)
    monkeypatch.setattr(new, "get_snapshot", replacing_snapshot)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        with pytest.raises(RuntimeError, match="replaced"):
            session.open()
        assert ("reset", 8) not in new.events
        assert ("tick", 8) not in new.events
        assert replacement.events == []
        session.close()
