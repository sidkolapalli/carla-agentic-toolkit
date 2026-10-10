"""A killed worker leaves durable reload provenance for a freshly acquired lease."""

from __future__ import annotations

import multiprocessing
import signal
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.managed_reload_helpers import (
    _assert_quarantined,
    _case,
    _reload_evidence,
    _session,
)

if TYPE_CHECKING:
    from multiprocessing.synchronize import Event
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient


def _worker(root: Path, phase: str, ready: Event, hold: Event) -> None:
    _old, new, client = _case()

    def interruptible_boundary() -> None:
        ready.set()
        hold.wait()

    if phase == "pending":
        client.on_reload = interruptible_boundary
    else:
        new.on_map = interruptible_boundary
    with SimulatorLease("localhost", 3000, state_root=root) as lease:
        _session(client, lease).open()


@pytest.mark.parametrize("phase", ["pending", "acknowledged"])
def test_sigkill_recovery_uses_only_durable_reload_acknowledgement(
    tmp_path: Path, phase: str
) -> None:
    """A process-death boundary cannot be repaired by guessing the current world identity."""
    context = multiprocessing.get_context("fork")
    ready, hold = context.Event(), context.Event()
    child = context.Process(target=_worker, args=(tmp_path, phase, ready, hold))
    child.start()
    try:
        assert ready.wait(5.0)
    finally:
        child.kill()
        child.join(5.0)
    assert child.exitcode == -signal.SIGKILL
    _recover_killed_worker(tmp_path, phase)


def _recover_killed_worker(root: Path, phase: str) -> None:
    old, new, client = _case()
    original = old.settings.copy()
    new.settings.update(synchronous_mode=True, fixed_delta_seconds=0.05)
    client.world = new
    with SimulatorLease("localhost", 3000, state_root=root, recovering=True) as lease:
        assert _reload_evidence(lease.recovery_state)["phase"] == phase
        report = ManagedSession.recover(cast("CarlaClient", client), lease, ExperimentSpec())
        assert report["ok"] is (phase == "acknowledged")
        _assert_recovery_effects(new.events, new.settings, original, phase)
    if phase == "pending":
        _assert_quarantined(root)


def _assert_recovery_effects(
    events: list[tuple[str, int]],
    settings: dict[str, object],
    original: dict[str, object],
    phase: str,
) -> None:
    if phase == "acknowledged":
        assert settings == original
    else:
        assert ("apply", 8) not in events
        assert ("tick", 8) not in events
