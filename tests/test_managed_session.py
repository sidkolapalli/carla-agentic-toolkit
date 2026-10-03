"""Managed sessions own exactly one frame progression and preserve cleanup truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.managed_session import ManagedSession, SessionInvariantError
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaClient


@dataclass
class FakeWorld:
    """In-memory world where external ticks and reloads are controllable."""

    id: int = 7
    frame: int = 100
    extra_tick: int = 0
    destroyed: list[int] = field(default_factory=list)
    actors: list[SimpleNamespace] = field(default_factory=list)
    settings: dict[str, object] = field(
        default_factory=lambda: {
            "synchronous_mode": False,
            "fixed_delta_seconds": None,
            "no_rendering_mode": False,
            "substepping": True,
            "max_substeps": 10,
            "max_substep_delta_time": 0.01,
        }
    )

    def get_settings(self) -> SimpleNamespace:
        """Return a copy as CARLA does."""
        return SimpleNamespace(**self.settings)

    def apply_settings(self, settings: SimpleNamespace) -> int:
        """Apply settings without an implicit scheduled tick."""
        self.settings = vars(settings).copy()
        return self.frame

    def get_snapshot(self) -> SimpleNamespace:
        """Return immutable frame evidence."""
        return SimpleNamespace(frame=self.frame)

    def wait_for_tick(self, _seconds: float) -> SimpleNamespace:
        """Deliver a fresh preflight snapshot without a client-requested tick."""
        self.frame += 1
        return self.get_snapshot()

    def get_actors(self) -> object:
        """Return actor queries used by ownership/recovery."""
        return SimpleNamespace(
            filter=lambda pattern: self.actors if pattern == "vehicle.*" else [],
            find=lambda actor_id: next((a for a in self.actors if a.id == actor_id), None),
        )

    def get_map(self) -> object:
        """Return the retained map handle."""
        return SimpleNamespace(name="Town10HD_Opt")

    def tick(self) -> int:
        """Advance with an optional competing external tick."""
        self.frame += 1 + self.extra_tick
        return self.frame


def _session(world: FakeWorld, lease: SimulatorLease) -> ManagedSession:
    client = cast("CarlaClient", SimpleNamespace(get_world=lambda: world))
    return ManagedSession(ExperimentSpec(), client, lease, "test-run")


def test_each_step_advances_exactly_once_and_restores_settings(tmp_path: Path) -> None:
    """A successful session restores all settings it changes, holding its lease."""
    world = FakeWorld()
    before = world.settings.copy()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(world, lease)
        session.open()
        assert world.settings["synchronous_mode"] is True
        initial_frame = world.frame
        assert session.step().frame == initial_frame + 1
        assert session.step().frame == initial_frame + 2
        _assert_restored(session, world, lease, before)


def _assert_restored(
    session: ManagedSession,
    world: FakeWorld,
    lease: SimulatorLease,
    before: dict[str, object],
) -> None:
    assert session.close()["ok"] is True
    assert world.settings == before
    assert not lease.recovery_state


@pytest.mark.parametrize("during", [False, True])
def test_external_tick_invalidates_session(tmp_path: Path, *, during: bool) -> None:
    """Competing ticks are never silently counted as one experiment step."""
    world = FakeWorld()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(world, lease)
        session.open()
        if during:
            world.extra_tick = 1
        else:
            world.frame += 1
        with pytest.raises(SessionInvariantError, match="frame"):
            session.step()
        session.close()


def test_reload_invalidates_handles_without_destroying_reused_actor_id(tmp_path: Path) -> None:
    """An old generation may never control or clean an ID in a replacement world."""
    world = FakeWorld()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(world, lease)
        session.open()
        actor = SimpleNamespace(
            id=11,
            type_id="vehicle.test",
            attributes={
                "role_name": "managed:test-run:ego",
            },
            destroy=lambda: world.destroyed.append(11) or True,
        )
        world.actors.append(actor)
        session.own(cast("CarlaActor", actor), controller="ego", protected=True)
        world.id = 8
        with pytest.raises(SessionInvariantError, match="world"):
            session.step()
        report = session.close()
        assert report["world_replaced"] is True
        assert not world.destroyed


def test_protection_is_separate_from_creation_and_controller(tmp_path: Path) -> None:
    """Ownership doesn't allow background reassignment or protected deletion."""
    world = FakeWorld()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(world, lease)
        session.open()
        actor = SimpleNamespace(
            id=11,
            type_id="vehicle.test",
            attributes={
                "role_name": "managed:test-run:ego",
            },
            destroy=lambda: True,
        )
        world.actors.append(actor)
        session.own(cast("CarlaActor", actor), controller="ego", protected=True)
        with pytest.raises(SessionInvariantError, match="controller"):
            session.assign_controller(11, "density")
        with pytest.raises(SessionInvariantError, match="protected"):
            session.destroy_actor(11)
        assert session.close()["ok"] is True


def test_partial_cleanup_keeps_recovery_required(tmp_path: Path) -> None:
    """Failed destroy must remain visible and keep the endpoint quarantined."""
    world = FakeWorld()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(world, lease)
        session.open()
        actor = SimpleNamespace(
            id=11,
            type_id="vehicle.test",
            attributes={
                "role_name": "managed:test-run:ego",
            },
            destroy=lambda: False,
        )
        world.actors.append(actor)
        session.own(cast("CarlaActor", actor), controller="ego", protected=True)
        report = session.close()
        assert report["ok"] is False
        assert report["failures"]
        assert lease.recovery_state


def test_cleanup_uses_created_handle_before_first_snapshot(tmp_path: Path) -> None:
    """A spawn may succeed before its actor appears in the synchronous snapshot."""
    world = FakeWorld()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(world, lease)
        session.open()
        actor = SimpleNamespace(
            id=11,
            type_id="vehicle.test",
            attributes={"role_name": "managed:test-run:ego"},
            destroy=lambda: world.destroyed.append(11) or True,
        )
        session.own(cast("CarlaActor", actor), controller="ego", protected=True)
        assert session.close()["ok"] is True
        assert world.destroyed == [11]
