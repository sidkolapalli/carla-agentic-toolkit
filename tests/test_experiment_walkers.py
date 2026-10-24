"""Regression coverage for partial walker/controller spawn rollback."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import experiment_walkers
from carla_agentic_toolkit.experiment_walkers import WalkerSpawnContext, spawn_walkers

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaBlueprint, CarlaWorld

WALKER_ID = 101
CONTROLLER_ID = 102


@dataclass
class FakeActor:
    """Actor stub with controllable setup and cleanup failures."""

    id: int
    destroyed: list[int]
    failure_stage: str = ""
    destroy_result: bool = True

    def destroy(self) -> bool:
        """Record cleanup order and optionally fail to destroy."""
        self.destroyed.append(self.id)
        self._fail("destroy")
        return self.destroy_result

    def start(self) -> None:
        """Start the controller unless configured to fail."""
        self._fail("start")

    def stop(self) -> None:
        """Acknowledge controller removal before native deletion."""
        self._fail("stop")

    def set_max_speed(self, _speed: float) -> None:
        """Configure the controller speed unless configured to fail."""
        self._fail("speed")

    def go_to_location(self, _location: object) -> None:
        """Configure a navigation destination unless configured to fail."""
        self._fail("destination")

    def _fail(self, stage: str) -> None:
        if stage == self.failure_stage:
            raise RuntimeError(stage)


@dataclass
class FakeWorld:
    """World stub exercising each partial-spawn boundary."""

    failure_stage: str
    destroyed: list[int] = field(default_factory=list)
    destroy_result: bool = True
    walker_cleanup_error: bool = False
    id: int = 17
    frame: int = 0
    actors: dict[int, FakeActor] = field(default_factory=dict)

    def set_pedestrians_seed(self, _seed: int) -> None:
        """Accept the supplied native navigation seed."""

    def get_settings(self) -> SimpleNamespace:
        """Observe asynchronous mode without claiming a tick owner."""
        return SimpleNamespace(synchronous_mode=False)

    def get_snapshot(self) -> SimpleNamespace:
        """Return the publication used by the startup barrier."""
        return SimpleNamespace(frame=self.frame, find=self.actors.get)

    def wait_for_tick(self, _seconds: float) -> SimpleNamespace:
        """Publish created actors before controller Start."""
        self.frame += 1
        return self.get_snapshot()

    def try_spawn_actor(self, _blueprint: object, _transform: object) -> FakeActor | None:
        """Return a pedestrian or simulate a failed initial spawn."""
        if self.failure_stage == "walker":
            return None
        actor = FakeActor(
            WALKER_ID,
            self.destroyed,
            failure_stage="destroy" if self.walker_cleanup_error else "",
            destroy_result=self.destroy_result,
        )
        self.actors[actor.id] = actor
        return actor

    def spawn_actor(self, _blueprint: object, _transform: object, _walker: object) -> FakeActor:
        """Return a controller or fail before one is created."""
        if self.failure_stage == "controller":
            raise RuntimeError(self.failure_stage)
        actor = FakeActor(
            CONTROLLER_ID,
            self.destroyed,
            failure_stage=self.failure_stage,
            destroy_result=self.destroy_result,
        )
        self.actors[actor.id] = actor
        return actor


def _spawn(world: FakeWorld, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(experiment_walkers, "random_walker_transform", lambda _world: object())
    monkeypatch.setattr(experiment_walkers, "random_walker_location", lambda _world: object())
    context = WalkerSpawnContext(
        world=cast("CarlaWorld", world),
        walker_blueprints=[cast("CarlaBlueprint", object())],
        controller_blueprint=cast("CarlaBlueprint", object()),
        speed=1.0,
        seed=0,
        count=1,
    )
    return spawn_walkers(context)


@pytest.mark.parametrize(
    ("stage", "destroyed"),
    [
        ("walker", []),
        ("controller", [WALKER_ID]),
        ("start", [CONTROLLER_ID, WALKER_ID]),
        ("speed", [CONTROLLER_ID, WALKER_ID]),
        ("destination", [CONTROLLER_ID, WALKER_ID]),
    ],
)
def test_partial_spawn_is_rolled_back_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch, stage: str, destroyed: list[int]
) -> None:
    """Every failed spawn phase cleans only created actors and reports failure."""
    world = FakeWorld(stage)
    outcome = _spawn(world, monkeypatch)

    assert world.destroyed == destroyed
    assert outcome["walker_ids"] == []
    assert outcome["controller_ids"] == []
    assert outcome["failed_spawns"]


@pytest.mark.parametrize("cleanup_raises", [False, True])
def test_failed_rollback_preserves_actor_ids_for_later_owned_cleanup(
    monkeypatch: pytest.MonkeyPatch, *, cleanup_raises: bool
) -> None:
    """Destroy failures must leave the surviving actor available for journaling."""
    world = FakeWorld("controller", destroy_result=False, walker_cleanup_error=cleanup_raises)
    outcome = _spawn(world, monkeypatch)

    assert world.destroyed == [WALKER_ID]
    assert outcome["walker_ids"] == [WALKER_ID]
    assert outcome["controller_ids"] == []
    assert outcome["failed_spawns"] == ["controller"]


def test_successful_spawn_preserves_both_actor_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fully initialized pair remains alive and available for ownership."""
    world = FakeWorld("")
    outcome = _spawn(world, monkeypatch)

    assert world.destroyed == []
    assert outcome == {
        "walker_ids": [WALKER_ID],
        "controller_ids": [CONTROLLER_ID],
        "failed_spawns": [],
    }
