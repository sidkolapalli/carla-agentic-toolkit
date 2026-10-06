"""Trusted script cleanup must retain quarantine on malformed evidence or native stalls."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import script_recovery
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.carla_protocols import CarlaWorld
    from carla_agentic_toolkit.ownership import RunOwnership

PROMPT_SECONDS = 2.0


def journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: object) -> Path:
    """Place a journal in a private production-shaped temporary root."""
    monkeypatch.setattr(script_recovery.tempfile, "gettempdir", lambda: str(tmp_path))
    root = tmp_path / "carla-agentic-toolkit-script-test"
    root.mkdir(mode=0o700)
    path = root / "owned-actors.json"
    path.write_text(json.dumps(value))
    return path


def test_empty_journal_cleanup_is_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline finite scripts do not import CARLA or launch native cleanup."""
    path = journal(tmp_path, monkeypatch, [])
    report = script_recovery.cleanup_script_ownership("localhost", 3000, path, -1, 1)
    assert report == {"attempted_actor_ids": [], "destroyed_actor_ids": [], "failures": []}


@pytest.mark.parametrize("value", [[17], {"schema_version": 1, "actor_ids": [17]}])
def test_nonempty_journal_without_episode_is_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """Legacy numeric IDs cannot authorize destroying actors in a replacement episode."""
    path = journal(tmp_path, monkeypatch, value)
    report = script_recovery.cleanup_script_ownership("localhost", 3000, path, -1, 1)
    assert report["failures"]
    assert path.exists()


@pytest.mark.parametrize("damage", ["absent", "symlink", "large", "public_root"])
def test_untrusted_journal_does_not_release_dirty_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    """Missing, redirected, unbounded, or publicly writable evidence fails closed."""
    path = journal(tmp_path, monkeypatch, [])
    if damage == "absent":
        path.unlink()
    elif damage == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "other")
    elif damage == "large":
        path.write_bytes(b" " * (script_recovery.MAX_JOURNAL_BYTES + 1))
    else:
        path.parent.chmod(0o777)
    state = tmp_path / "state"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty({"kind": "script", "ownership_path": str(path)})
    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)
    with SimulatorLease("localhost", 3000, state_root=state, recovering=True) as lease:
        assert (result["ok"] is False, bool(lease.recovery_state)) == (True, True)


def test_native_cleanup_has_hard_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A native RPC stall is killed/reaped before the inherited lease is released."""
    path = journal(tmp_path, monkeypatch, {"schema_version": 1, "world_id": 7, "actor_ids": [17]})
    children: list[subprocess.Popen[bytes]] = []

    def blocked(_job: Path, lease_descriptor: int) -> subprocess.Popen[bytes]:
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
            pass_fds=(lease_descriptor,),
        )
        children.append(child)
        return child

    monkeypatch.setattr(script_recovery, "_spawn_cleanup", blocked)
    with SimulatorLease("localhost", 3000, state_root=tmp_path / "state") as lease:
        started = time.monotonic()
        report = script_recovery.cleanup_script_ownership(
            "localhost", 3000, path, lease.descriptor, 0.1
        )
        assert time.monotonic() - started < PROMPT_SECONDS
        assert report["failures"]
        assert children[0].poll() is not None


@pytest.mark.parametrize("same_episode", [True, False])
def test_cleanup_never_confuses_empty_cache_with_absent_actor(
    monkeypatch: pytest.MonkeyPatch, *, same_episode: bool
) -> None:
    """Refresh before destruction, and never destroy recycled IDs in another world."""
    from carla_agentic_toolkit import ownership  # noqa: PLC0415

    events: list[str] = []
    state = {"frame": 10}

    def tick() -> int:
        state["frame"] += 1
        events.append("snapshot")
        return state["frame"]

    world = SimpleNamespace(
        id=7 if same_episode else 8,
        get_snapshot=lambda: SimpleNamespace(frame=state["frame"]),
        get_settings=lambda: SimpleNamespace(synchronous_mode=True),
        tick=tick,
    )
    client = SimpleNamespace(get_world=lambda: world)
    adapter = SimpleNamespace(_client=lambda: client)
    record = SimpleNamespace(world_id=lambda: 7, clear=lambda: events.append("clear"))

    def clean(_adapter: object, _record: object) -> dict[str, object]:
        events.append("destroy")
        return ownership.cleanup_report()

    monkeypatch.setattr(ownership, "cleanup_owned_actors", clean)
    report = script_recovery._cleanup_connected(  # noqa: SLF001 - isolate native RPC boundary.
        cast("PythonCarlaAdapter", adapter), cast("RunOwnership", record)
    )
    assert report["failures"] == []
    assert events == (["snapshot", "destroy"] if same_episode else ["clear"])


def test_unpublished_cleanup_frame_is_not_absence_evidence() -> None:
    """A successful tick RPC alone cannot prove that actor streaming state arrived."""
    world = SimpleNamespace(
        get_snapshot=lambda: SimpleNamespace(frame=10),
        get_settings=lambda: SimpleNamespace(synchronous_mode=True),
        tick=lambda: 11,
    )
    with pytest.raises(RuntimeError, match="fresh actor snapshot"):
        script_recovery._fresh_cleanup_snapshot(cast("CarlaWorld", world))  # noqa: SLF001


def test_verified_empty_recovery_releases_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A private intact empty journal provides positive offline recovery evidence."""
    path = journal(tmp_path, monkeypatch, [])
    state = tmp_path / "state"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty({"kind": "script", "ownership_path": str(path)})
    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)
    assert result["ok"] is True
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        assert not lease.recovery_state


def test_hard_linked_ownership_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second writable name must not redirect supposedly private ownership evidence."""
    path = journal(tmp_path, monkeypatch, [])
    (tmp_path / "alias").hardlink_to(path)
    report = script_recovery.cleanup_script_ownership("localhost", 3000, path, -1, 1)
    assert report["failures"]
