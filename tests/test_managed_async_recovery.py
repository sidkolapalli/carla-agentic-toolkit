"""Managed recovery publishes fresh state without ticking asynchronous worlds."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import experiment_replay
from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import RecoveryRequiredError, SimulatorLease
from tests.test_managed_session import FakeWorld

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaClient

ACTOR_ID = 11
RUN_ID = "async-recovery"


@dataclass
class RecoveryWorld(FakeWorld):
    """Separate the frame delivered by the native call from later publication."""

    events: list[str] = field(default_factory=list)
    wait_timeouts: list[float] = field(default_factory=list)
    refresh_armed: bool = False
    fail_sync_apply: bool = False
    behavior: str = "fresh"
    publication_ahead: int = 0
    on_refresh: Callable[[], None] = field(default=lambda: None, repr=False)

    def apply_settings(self, settings: SimpleNamespace) -> int:
        """Expose settings writes and the journal-before-first-mutation window."""
        self.events.append("settings")
        if self.fail_sync_apply:
            self.fail_sync_apply = False
            message = "simulated worker death before sync settings"
            raise RuntimeError(message)
        return super().apply_settings(settings)

    def get_actors(self) -> object:
        """Record actor-cache reads so failed freshness cannot authorize discovery."""
        self.events.append("actors")
        return super().get_actors()

    def tick(self) -> int:
        """Record client-requested ticks independently from native frame delivery."""
        if not self.refresh_armed:
            return super().tick()
        return self._refresh("tick")

    def wait_for_tick(self, _seconds: float) -> SimpleNamespace:
        """Deliver an immutable frame while allowing publication to advance further."""
        if not self.refresh_armed:
            return super().wait_for_tick(_seconds)
        self.wait_timeouts.append(_seconds)
        return SimpleNamespace(frame=self._refresh("wait"))

    def _refresh(self, operation: str) -> int:
        self.events.append(operation)
        if self.behavior == "timeout":
            message = "native refresh timed out"
            raise RuntimeError(message)
        before = self.frame
        delivered = before if self.behavior == "stale" else before + 1
        self.frame = (
            before if self.behavior == "unpublished" else delivered + self.publication_ahead
        )
        self.on_refresh()
        return delivered


@dataclass
class RecoveryClient:
    """Allow a native refresh to replace the current episode explicitly."""

    world: FakeWorld

    def get_world(self) -> FakeWorld:
        """Return the currently active episode, not a Mock identity."""
        return self.world

    def reload_world(self, reset_settings: bool = True) -> FakeWorld:  # noqa: FBT001, FBT002
        """Retain the fixture proxy while binding a different reload episode."""
        return self.world.reload_world(reset_settings)


def _destroy_batch(
    client: CarlaClient, commands: list[dict[str, object]], *, do_tick: bool
) -> dict[str, object]:
    assert do_tick is False
    world = cast("RecoveryWorld", cast("RecoveryClient", client).world)
    return {
        "responses": [_destroy_response(world, int(str(item["actor_id"]))) for item in commands]
    }


def _destroy_response(world: RecoveryWorld, actor_id: int) -> dict[str, object]:
    world.events.append("destroy")
    if not any(actor.id == actor_id for actor in world.actors):
        return {"actor_id": 0, "error": "unable to destroy actor: not found"}
    world.actors = [actor for actor in world.actors if actor.id != actor_id]
    world.destroyed.append(actor_id)
    return {"actor_id": actor_id, "error": ""}


def _fail_mark_clean() -> None:
    message = "simulated worker death before clean marker"
    raise RuntimeError(message)


def _abandoned_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, window: str
) -> tuple[RecoveryWorld, RecoveryClient, dict[str, object]]:
    world = RecoveryWorld()
    client = RecoveryClient(world)
    original = world.settings.copy()
    monkeypatch.setattr(experiment_replay, "apply_batch", _destroy_batch)
    with SimulatorLease("localhost", 3000, state_root=tmp_path / "state") as lease:
        session = ManagedSession(ExperimentSpec(), cast("CarlaClient", client), lease, RUN_ID)
        _interrupt_session(session, world, lease, monkeypatch, window)
        assert lease.recovery_state
    world.refresh_armed = True
    world.events.clear()
    return world, client, original


def _interrupt_session(
    session: ManagedSession,
    world: RecoveryWorld,
    lease: SimulatorLease,
    monkeypatch: pytest.MonkeyPatch,
    window: str,
) -> None:
    if window == "before_sync":
        world.fail_sync_apply = True
        with pytest.raises(RuntimeError, match="before sync"):
            session.open()
        return
    session.open()
    actor = SimpleNamespace(
        id=ACTOR_ID,
        type_id="vehicle.test",
        attributes={"role_name": f"managed:{RUN_ID}:ego"},
    )
    world.actors.append(actor)
    session.own(cast("CarlaActor", actor), controller="ego")
    with monkeypatch.context() as patch:
        patch.setattr(lease, "mark_clean", _fail_mark_clean)
        with pytest.raises(RuntimeError, match="before clean marker"):
            session.close()


def _recover(tmp_path: Path, client: RecoveryClient) -> dict[str, object]:
    with SimulatorLease("localhost", 3000, state_root=tmp_path / "state", recovering=True) as lease:
        return ManagedSession.recover(cast("CarlaClient", client), lease, ExperimentSpec())


def _assert_refresh_mode(world: RecoveryWorld, *, synchronous: bool) -> None:
    operation = {False: "wait", True: "tick"}[synchronous]
    other = {False: "tick", True: "wait"}[synchronous]
    assert world.events.count(operation) == 1
    assert world.events.count(other) == 0
    assert world.wait_timeouts == ([] if synchronous else [5.0])


def _assert_success(report: dict[str, object], frame: int) -> None:
    assert report["ok"] is True
    assert report["settings_restored"] is True
    assert report["recovery_frame"] == frame


def _assert_clean_lease(tmp_path: Path) -> None:
    with SimulatorLease("localhost", 3000, state_root=tmp_path / "state") as lease:
        assert lease.recovery_state == {}


def _assert_dirty_lease(tmp_path: Path) -> None:
    with (
        pytest.raises(RecoveryRequiredError),
        SimulatorLease("localhost", 3000, state_root=tmp_path / "state"),
    ):
        pass


@pytest.mark.parametrize("window", ["before_sync", "before_clean"])
@pytest.mark.parametrize("publication_ahead", [0, 2])
def test_asynchronous_recovery_reacquires_dirty_journal_and_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, window: str, publication_ahead: int
) -> None:
    """Both crash windows need native delivery, never an asynchronous tick cue."""
    world, client, original = _abandoned_session(tmp_path, monkeypatch, window)
    world.publication_ahead = publication_ahead
    expected = world.frame + 1
    report = _recover(tmp_path, client)
    _assert_success(report, expected)
    _assert_refresh_mode(world, synchronous=False)
    _assert_restored_world(world, original)
    _assert_clean_lease(tmp_path)


def _assert_restored_world(world: RecoveryWorld, original: dict[str, object]) -> None:
    assert world.settings == original
    assert world.actors == []
    assert world.events[0] in {"wait", "tick"}


def test_synchronous_recovery_accepts_a_later_published_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cleanup freshness permits later publication without changing strict runtime stepping."""
    world, client, original = _abandoned_session(tmp_path, monkeypatch, "before_clean")
    world.settings["synchronous_mode"] = True
    world.publication_ahead = 2
    expected = world.frame + 1
    report = _recover(tmp_path, client)
    _assert_success(report, expected)
    _assert_refresh_mode(world, synchronous=True)
    _assert_restored_world(world, original)
    _assert_clean_lease(tmp_path)


@pytest.mark.parametrize("synchronous", [False, True])
@pytest.mark.parametrize("behavior", ["stale", "unpublished", "timeout", "replaced"])
def test_failed_recovery_refresh_cannot_destroy_or_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, behavior: str, *, synchronous: bool
) -> None:
    """Unverified native publication retains quarantine before actor or settings mutations."""
    world, client, _original = _abandoned_session(tmp_path, monkeypatch, "before_clean")
    world.settings["synchronous_mode"] = synchronous
    world.behavior = behavior
    replacement = FakeWorld(id=world.id + 1, actors=[SimpleNamespace(id=ACTOR_ID)])
    if behavior == "replaced":
        world.on_refresh = partial(setattr, client, "world", replacement)
    before = world.settings.copy()
    report = _recover(tmp_path, client)
    _assert_failed_report(report)
    _assert_no_mutations(world)
    _assert_failure_preserved_worlds(world, replacement, before)
    _assert_refresh_mode(world, synchronous=synchronous)
    _assert_dirty_lease(tmp_path)


def _assert_failed_report(report: dict[str, object]) -> None:
    assert report["ok"] is False
    assert report["settings_restored"] is False
    assert report["failures"]


def _assert_no_mutations(world: RecoveryWorld) -> None:
    assert "destroy" not in world.events
    assert "settings" not in world.events
    assert "actors" not in world.events


def _assert_failure_preserved_worlds(
    world: RecoveryWorld, replacement: FakeWorld, before: dict[str, object]
) -> None:
    assert world.settings == before
    assert [actor.id for actor in replacement.actors] == [ACTOR_ID]
    assert replacement.settings["synchronous_mode"] is False
