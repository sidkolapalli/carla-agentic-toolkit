"""Regression coverage from independent lifecycle and crash-evidence review."""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import experiment_replay, managed_engine, managed_session, managed_world
from carla_agentic_toolkit.managed_session import ManagedSession, SessionInvariantError
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import RecoveryRequiredError, SimulatorLease
from tests.test_managed_engine import FakeExperiment
from tests.test_managed_session import FakeWorld, _apply_destroy_batch

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaClient


@pytest.fixture(autouse=True)
def authoritative_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the same realistic server-response fixture for lifecycle cleanup."""
    monkeypatch.setattr(experiment_replay, "apply_batch", _apply_destroy_batch)


def prepare_engine(monkeypatch: pytest.MonkeyPatch) -> FakeWorld:
    """Use real lifecycle code with a deterministic numerical fixture."""
    world = FakeWorld()
    client = SimpleNamespace(
        get_world=lambda: world,
        reload_world=world.reload_world,
        get_client_version=lambda: "0.9.16-test",
        get_server_version=lambda: "0.9.16-test",
    )
    monkeypatch.setattr(managed_engine, "connect_client", lambda _spec: client)
    monkeypatch.setattr(managed_engine, "build_experiment", FakeExperiment)
    return world


def test_status_failure_does_not_skip_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unavailable status file cannot prevent restoring a synchronous world."""
    world = prepare_engine(monkeypatch)
    before = world.settings.copy()

    def publish(status: dict[str, object]) -> None:
        if status["state"] == "stopping":
            message = "status storage unavailable"
            raise OSError(message)

    result = managed_engine.run_experiment(
        ExperimentSpec(max_steps=1),
        "status-failure",
        state_root=tmp_path,
        cancelled=lambda: False,
        publish_status=publish,
    )
    assert world.settings == before
    assert cast("dict[str, object]", result["cleanup"])["ok"] is True
    assert result["state"] == "failed"


def test_trace_overflow_returns_failed_evidence_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full trace stops the experiment without losing cleanup truth or its retained path."""
    world = prepare_engine(monkeypatch)
    before = world.settings.copy()
    run = managed_engine.ExperimentRun(
        ExperimentSpec(max_steps=1),
        "trace-overflow",
        state_root=tmp_path,
        cancelled=lambda: False,
        publish_status=lambda _status: None,
    )

    class OverflowExperiment(FakeExperiment):
        """Exhaust remaining trace budget after the session has changed world settings."""

        def prepare(self) -> None:
            """Force the next real append through its existing overflow guard."""
            run.trace.max_bytes = 1

    monkeypatch.setattr(managed_engine, "build_experiment", OverflowExperiment)
    result = asyncio.run(run.run())
    assert (
        result["state"],
        result["ok"],
        cast("dict[str, object]", result["cleanup"])["ok"],
    ) == ("failed", False, True)
    assert result["trace_path"] == str(run.trace.path)
    assert world.settings == before


def test_frame_violation_keeps_new_actor_handle_for_cleanup(tmp_path: Path) -> None:
    """A successful spawn followed by an external tick still has an owned cleanup handle."""
    world = FakeWorld()
    client = cast(
        "CarlaClient", SimpleNamespace(get_world=lambda: world, reload_world=world.reload_world)
    )
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(ExperimentSpec(), client, lease, "new-spawn")
        session.open()
        actor = SimpleNamespace(
            id=11,
            type_id="vehicle.test",
            attributes={"role_name": "managed:new-spawn:ego"},
            destroy=lambda: world.destroyed.append(11) or True,
        )
        world.frame += 1
        with pytest.raises(SessionInvariantError, match="frame"):
            session.own(cast("CarlaActor", actor), controller="ego")
        assert session.close()["ok"] is True
        assert world.destroyed == [11]


def test_empty_dirty_file_never_counts_as_clean(tmp_path: Path) -> None:
    """Only explicit journal removal is evidence of verified recovery."""
    token = hashlib.sha256(b"loopback:3000").hexdigest()
    (tmp_path / f"{token}.json").write_text("{}")
    with (
        pytest.raises(RecoveryRequiredError),
        SimulatorLease("localhost", 3000, state_root=tmp_path),
    ):
        pass


def test_dangling_dirty_symlink_never_counts_as_clean(tmp_path: Path) -> None:
    """Unreadable recovery evidence fails closed without following arbitrary files."""
    token = hashlib.sha256(b"loopback:3000").hexdigest()
    (tmp_path / f"{token}.json").symlink_to(tmp_path / "absent")
    with (
        pytest.raises(RecoveryRequiredError),
        SimulatorLease("localhost", 3000, state_root=tmp_path),
    ):
        pass


class SetupClock:
    """Advance simulated wall time without real sleep in setup-barrier tests."""

    def __init__(self) -> None:
        """Start at one deterministic instant."""
        self.now = 0.0

    def monotonic(self) -> float:
        """Return the simulated instant."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance setup time only when the barrier yields."""
        self.now += seconds


@pytest.mark.parametrize("deliver", [True, False])
def test_crash_recovery_requires_a_fresh_actor_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, deliver: bool
) -> None:
    """A new client's empty cache cannot prove that crashed-run actors are absent."""
    world = FakeWorld()
    client = cast(
        "CarlaClient", SimpleNamespace(get_world=lambda: world, reload_world=world.reload_world)
    )
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(ExperimentSpec(), client, lease, "fresh-recovery")
        session.open()
        actor = SimpleNamespace(
            id=11,
            type_id="vehicle.test",
            attributes={"role_name": "managed:fresh-recovery:ego"},
            destroy=lambda: world.destroyed.append(11) or True,
        )
        session.own(cast("CarlaActor", actor), controller="ego")

        def tick() -> int:
            if deliver:
                world.frame += 1
                world.actors.append(actor)
            return world.frame if deliver else world.frame + 1

        monkeypatch.setattr(world, "tick", tick)
        result = ManagedSession.recover(client, lease, ExperimentSpec())
        if deliver:
            assert (world.destroyed, result["ok"], bool(lease.recovery_state)) == (
                [11],
                True,
                False,
            )
        else:
            assert (result["ok"], bool(lease.recovery_state)) == (False, True)


@pytest.mark.parametrize("continuous", [False, True])
def test_setup_barrier_distinguishes_late_delivery_from_external_ticks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    continuous: bool,
) -> None:
    """Accept bounded settings-delivery frames, never claim runtime ownership during drift."""
    clock = SetupClock()
    world = FakeWorld()
    delivery_seconds = 0.03
    settled_frame = 101

    def snapshot() -> SimpleNamespace:
        if not continuous and world.frame > settled_frame:
            return SimpleNamespace(frame=world.frame)
        world.frame = 100 + (
            int(clock.now / 0.02) if continuous else int(clock.now >= delivery_seconds)
        )
        return SimpleNamespace(frame=world.frame)

    monkeypatch.setattr(managed_world, "time", clock, raising=False)
    monkeypatch.setattr(managed_session, "require_dedicated_world", lambda *_args: None)
    monkeypatch.setattr(world, "get_snapshot", snapshot)
    client = cast(
        "CarlaClient", SimpleNamespace(get_world=lambda: world, reload_world=world.reload_world)
    )
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(ExperimentSpec(), client, lease, "setup-barrier")
        if continuous:
            with pytest.raises(SessionInvariantError, match="quiet"):
                session.open()
        else:
            session.open()
            _assert_settled_setup(session, settled_frame)
        assert session.close()["ok"] is True


def _assert_settled_setup(session: ManagedSession, settled_frame: int) -> None:
    assert session.setup_frame_barrier["settled_frame"] == settled_frame
    assert session.expected_frame == settled_frame + 1
    assert session.setup_frame_barrier["advanced_frames"] == 1


def test_initial_empty_actor_cache_is_not_dedicated_world_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preflight waits for streaming data and refuses unrelated actors before mutation."""
    world = FakeWorld()
    original = world.settings.copy()

    def fresh(_seconds: float) -> SimpleNamespace:
        world.frame += 1
        world.actors.append(SimpleNamespace(id=99))
        return world.get_snapshot()

    monkeypatch.setattr(world, "wait_for_tick", fresh)
    client = cast(
        "CarlaClient", SimpleNamespace(get_world=lambda: world, reload_world=world.reload_world)
    )
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(ExperimentSpec(), client, lease, "preflight")
        with pytest.raises(SessionInvariantError, match="existing actors"):
            session.open()
        assert world.settings == original
        assert not lease.recovery_state
