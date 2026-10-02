"""Interprocess ownership must survive blocked work and require crash recovery."""

from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit.simulator_lease import (
    LeaseBusyError,
    RecoveryRequiredError,
    SimulatorLease,
    simulator_identity,
)

if TYPE_CHECKING:
    from multiprocessing.connection import Connection


def _hold_lease(root: str, control: Connection) -> None:
    """Simulate a blocked owner in a separate process."""
    with SimulatorLease("127.0.0.1", 3000, state_root=Path(root)) as lease:
        lease.mark_dirty({"world_id": "before-crash", "actor_ids": [42]})
        control.send("ready")
        control.recv()
        lease.mark_clean()


def test_aliases_share_one_identity() -> None:
    """Loopback spelling cannot bypass local ownership."""
    assert simulator_identity("localhost", 3000) == simulator_identity("127.0.0.1", 3000)
    assert simulator_identity("::1", 3000) == simulator_identity("127.0.0.1", 3000)
    assert simulator_identity("127.0.0.1", 3001) != simulator_identity("localhost", 3000)


def test_ipv4_mapped_address_cannot_bypass_ownership() -> None:
    """IPv4 endpoints represented by IPv6 must use the same shared lock."""
    assert simulator_identity("::ffff:127.0.0.1", 3000) == simulator_identity("127.0.0.1", 3000)
    assert simulator_identity("::ffff:192.0.2.1", 3000) == simulator_identity("192.0.2.1", 3000)


def test_two_processes_exclude_and_crash_requires_recovery(tmp_path: Path) -> None:
    """A killed owner cannot silently leave actors/settings for a new run."""
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_hold_lease, args=(str(tmp_path), child))
    process.start()
    try:
        assert parent.poll(15)
        assert parent.recv() == "ready"
        with pytest.raises(LeaseBusyError), SimulatorLease("localhost", 3000, state_root=tmp_path):
            pass
        process.kill()
        process.join(10)
        assert not process.is_alive()
        _assert_recovery_required(tmp_path)
    finally:
        if process.is_alive():
            process.kill()
        process.join(10)
        parent.close()
        child.close()


def _assert_recovery_required(root: Path) -> None:
    with (
        pytest.raises(RecoveryRequiredError) as caught,
        SimulatorLease("localhost", 3000, state_root=root),
    ):
        pass
    assert caught.value.state["world_id"] == "before-crash"
    with SimulatorLease("localhost", 3000, state_root=root, recovering=True) as lease:
        assert lease.recovery_state["actor_ids"] == [42]
        lease.mark_clean()
    with SimulatorLease("localhost", 3000, state_root=root):
        pass


def test_exception_does_not_clear_dirty_state(tmp_path: Path) -> None:
    """Lease scope exit is not evidence that mutation cleanup succeeded."""
    with pytest.raises(RuntimeError):
        _failed_cleanup(tmp_path)
    with (
        pytest.raises(RecoveryRequiredError),
        SimulatorLease("localhost", 3000, state_root=tmp_path),
    ):
        pass


def _failed_cleanup(root: Path) -> None:
    with SimulatorLease("localhost", 3000, state_root=root) as lease:
        lease.mark_dirty({"actor_ids": [7]})
        message = "cleanup failed"
        raise RuntimeError(message)
