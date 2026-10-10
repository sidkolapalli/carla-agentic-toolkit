"""Optional background evidence uses only the owner snapshot and sensing range."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from carla_agentic_toolkit.errors import CarlaAdapterError
from tests.test_merge_experiment import Actor, _prepare, _snapshot
from tests.test_route_backend import prepared

if TYPE_CHECKING:
    from collections.abc import Callable

NEAR_DISTANCE = 8.0
OWNER_FRAME = 100


def _background(x: float) -> Actor:
    return Actor(
        91, "vehicle.test.background", "managed:test:background", SimpleNamespace(x=x, y=0.0, z=0.4)
    )


def _frozen(handle: Actor) -> SimpleNamespace:
    return SimpleNamespace(
        get_transform=lambda: SimpleNamespace(
            location=handle.location, rotation=SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0)
        ),
        get_velocity=lambda: SimpleNamespace(x=1.0, y=0.0, z=0.0),
    )


def _case(monkeypatch: pytest.MonkeyPatch, fixture: str) -> tuple[Any, Any, SimpleNamespace]:
    if fixture == "merge":
        session, experiment = _prepare(monkeypatch)
        return session, experiment, _snapshot(session, OWNER_FRAME)
    session, experiment = prepared(monkeypatch)
    handles = {handle.id: handle for handle in experiment.actors.handles.values()}
    snapshot = SimpleNamespace(
        frame=OWNER_FRAME,
        timestamp=SimpleNamespace(elapsed_seconds=5.0),
        find=lambda identity: _frozen(handles[identity]),
    )
    return session, experiment, snapshot


def _assert_measured_background(value: object, observed: list[object], fixture: str) -> None:
    if observed:
        actor = cast("SimpleNamespace", observed[0])
        _assert_background_frame(cast("SimpleNamespace", value), actor, fixture)
        assert actor.speed_mps == 1.0


def _assert_background_frame(
    observation: SimpleNamespace, actor: SimpleNamespace, fixture: str
) -> None:
    assert observation.frame == OWNER_FRAME
    if fixture == "merge":
        assert actor.frame == observation.frame


def _observed_background(value: object, fixture: str, identity: int) -> list[object]:
    observation = cast("SimpleNamespace", value)
    evidence = (
        observation.neighbors
        if fixture == "merge"
        else tuple(item.actor for item in observation.traffic)
    )
    return [actor for actor in evidence if actor.actor_id == identity]


@pytest.mark.parametrize("fixture", ["merge", "route"])
@pytest.mark.parametrize("distance", [NEAR_DISTANCE, 200.0])
def test_background_observations_are_frozen_and_range_filtered(
    monkeypatch: pytest.MonkeyPatch, fixture: str, distance: float
) -> None:
    """A background actor's live getters raise; only in-range frozen values are exposed."""
    session, experiment, snapshot = _case(monkeypatch, fixture)
    background = _background(distance)
    session.background_actors = lambda: (background,)
    original_find: Callable[[int], object] = snapshot.find
    snapshot.find = lambda identity: (
        _frozen(background) if identity == background.id else original_find(identity)
    )
    value = experiment.observe(snapshot)
    observed = _observed_background(value, fixture, background.id)
    assert bool(observed) is (distance == NEAR_DISTANCE)
    _assert_measured_background(value, observed, fixture)


@pytest.mark.parametrize("fixture", ["merge", "route"])
def test_missing_owned_background_is_not_replaced_with_live_reads(
    monkeypatch: pytest.MonkeyPatch, fixture: str
) -> None:
    """Ownership alone does not fabricate frame-aligned kinematics."""
    session, experiment, snapshot = _case(monkeypatch, fixture)
    background = _background(8.0)
    session.background_actors = lambda: (background,)
    original_find = snapshot.find
    snapshot.find = lambda identity: None if identity == background.id else original_find(identity)
    with pytest.raises(CarlaAdapterError, match="snapshot"):
        experiment.observe(snapshot)
