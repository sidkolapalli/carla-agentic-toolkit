"""Smooth bounded spectator-follow behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest

from carla_mcp import experiment_scene
from carla_mcp.errors import CarlaAdapterError
from carla_mcp.models import Location, Rotation, Transform

if TYPE_CHECKING:
    from carla_mcp.carla_protocols import CarlaWorld

ACTOR_ID = 42


@dataclass
class Actor:
    """Moving actor fake."""

    id: int = ACTOR_ID
    transform: Transform = field(
        default_factory=lambda: Transform(
            location=Location(10.0, 20.0, 1.0),
            rotation=Rotation(0.0, 90.0, 0.0),
        )
    )

    def get_transform(self) -> Transform:
        """Return a transform facing positive Y."""
        return self.transform


@dataclass
class Actors:
    """Actor collection fake."""

    actor: Actor = field(default_factory=Actor)

    def find(self, actor_id: int) -> Actor | None:
        """Find the test actor."""
        return self.actor if actor_id == self.actor.id else None


@dataclass
class Spectator:
    """Spectator fake recording camera transforms."""

    transforms: list[Transform] = field(default_factory=list)
    initial: Transform = field(
        default_factory=lambda: Transform(
            location=Location(1.0, 2.0, 3.0),
            rotation=Rotation(0.0, 0.0, 0.0),
        )
    )

    def get_transform(self) -> Transform:
        """Return the initial operator camera."""
        return self.initial

    def set_transform(self, transform: Transform) -> None:
        """Record one camera update."""
        self.transforms.append(transform)


@dataclass
class World:
    """Asynchronous world fake."""

    actors: Actors = field(default_factory=Actors)
    spectator: Spectator = field(default_factory=Spectator)
    ticks: int = 0

    def get_actors(self) -> Actors:
        """Return actors."""
        return self.actors

    def get_spectator(self) -> Spectator:
        """Return the spectator."""
        return self.spectator

    def wait_for_tick(self, _timeout: float) -> object:
        """Record one simulator-frame wait."""
        self.ticks += 1
        return object()


def test_watch_actor_tracks_yaw_each_tick_and_restores_camera(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A chase camera should use actor heading, not jump along world axes."""
    world = World()
    times = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(experiment_scene.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(experiment_scene, "carla_transform", lambda value: value)

    result = experiment_scene.watch_actor(
        cast("CarlaWorld", world),
        actor_id=ACTOR_ID,
        seconds=1.0,
        distance=8.0,
        height=4.0,
    )

    chase = world.spectator.transforms[0]
    assert {
        "samples": result["samples"],
        "ticks": world.ticks,
        "chase_location": chase.location.to_dict(),
        "restored": world.spectator.transforms[-1],
    } == {
        "samples": 1,
        "ticks": 1,
        "chase_location": {"x": 10.0, "y": 12.0, "z": 5.0},
        "restored": world.spectator.initial,
    }


def test_watch_actor_rejects_unbounded_duration() -> None:
    """Promotional camera loops must remain bounded."""
    with pytest.raises(CarlaAdapterError, match="seconds"):
        experiment_scene.watch_actor(
            cast("CarlaWorld", World()),
            actor_id=ACTOR_ID,
            seconds=31.0,
            distance=8.0,
            height=4.0,
        )
