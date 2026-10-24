"""Controlled vehicle roles are labels, not recovery ownership evidence."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from carla_agentic_toolkit import route_actors
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.route_geometry import RoutePath, RoutePoint
from tests.test_merge_experiment import Session, _prepare, _snapshot
from tests.test_route_backend import prepared

TARGET_START_M = 24.0
TARGET_SPEED_MPS = 5.0
CUSTOM_SPEED_MPS = 4.0


@pytest.mark.parametrize("fixture", ["town10-merge-v1", "town10-merge-ue5-v1"])
def test_merge_controlled_vehicle_is_the_only_hero(
    monkeypatch: pytest.MonkeyPatch, fixture: str
) -> None:
    """The policy car, not another vehicle or an attached sensor, carries hero."""
    session, experiment = _prepare(monkeypatch, ExperimentSpec.model_validate({"fixture": fixture}))
    heroes = [actor for actor in session.world.actors if actor.role == "hero"]
    metadata = cast("dict[str, Any]", experiment.fixture_metadata())
    assert [actor.id for actor in heroes] == [metadata["actor_ids"]["policy"]]
    assert len({actor.role for actor in session.world.actors}) == len(session.world.actors)


@pytest.mark.parametrize("scenario", ["lead_brake", "cut_in", "pedestrian_crossing"])
def test_route_controlled_vehicle_is_the_only_hero(
    monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """All route hazards and sensors retain distinct non-hero roles."""
    session, experiment = prepared(monkeypatch, scenario)
    assert [actor.id for actor in session.world.actors if actor.role == "hero"] == [
        experiment.actors.handles["policy"].id
    ]


def test_custom_role_applies_to_merge_and_route_policy_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured role does not leak onto attached or background actors."""
    spec = ExperimentSpec.model_validate({"controlled_vehicle_role": "test_vehicle"})
    session, _experiment = _prepare(monkeypatch, spec)
    assert [actor.role for actor in session.world.actors].count("test_vehicle") == 1
    module = SimpleNamespace(
        Location=SimpleNamespace,
        Rotation=SimpleNamespace,
        Transform=lambda *args: SimpleNamespace(location=args[0] if args else None),
    )
    monkeypatch.setattr(route_actors, "import_module", lambda _name: module)
    route_session = Session(spec=spec)
    actors = route_actors.RouteActors(
        route_session, RoutePath((RoutePoint(0, 0), RoutePoint(50, 0)))
    )
    actors.spawn("policy", RoutePoint(0, 0))
    actors.spawn("lead", RoutePoint(24, 0))
    assert [actor.role for actor in route_session.world.actors].count("test_vehicle") == 1
    assert not any(actor.role == "hero" for actor in route_session.world.actors)


@pytest.mark.parametrize("role", ["", "two words", "line\nfeed", "\x00", "x" * 65, True, 7])
def test_invalid_controlled_role_is_rejected_before_fixture_creation(role: object) -> None:
    """Bounded nonempty data excludes whitespace, control characters, and coercion."""
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({"controlled_vehicle_role": role})


def test_new_merge_observation_controls_and_corridor_use_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """New trace fields unambiguously name the other-lane car and lane anchor."""
    session, experiment = _prepare(monkeypatch)
    value = experiment.observe(_snapshot(session))
    metadata = cast("dict[str, Any]", experiment.fixture_metadata())
    report = cast("dict[str, Any]", experiment.advance(value))
    assert set(metadata["actor_ids"]) == {"policy", "target"}
    assert "target" in value.to_dict()
    assert "ego" not in value.to_dict()
    assert set(report["controls"]) == {"policy", "target"}
    _assert_target_metadata(metadata)


def _assert_target_metadata(metadata: dict[str, Any]) -> None:
    assert metadata["target_start"]["x"] == TARGET_START_M
    assert metadata["target_lane_start"]["x"] == 0.0
    assert "ego_start" not in metadata
    assert metadata["settings"]["target_vehicle_speed_mps"] == TARGET_SPEED_MPS


@pytest.mark.parametrize("values", [{}, {"ego_speed_mps": 4.0}, {"target_vehicle_speed_mps": 4.0}])
def test_speed_settings_dump_canonical_target_name(values: dict[str, object]) -> None:
    """The old speed input remains accepted without being emitted into new specs."""
    spec = ExperimentSpec.model_validate(values)
    assert spec.model_dump()["target_vehicle_speed_mps"] == values.get(
        "ego_speed_mps", 4.0 if values else 5.0
    )
    assert "ego_speed_mps" not in spec.model_dump()
    assert spec.ego_speed_mps == spec.target_vehicle_speed_mps


def test_matching_speed_aliases_are_accepted() -> None:
    """Matching historical and canonical data is accepted and emitted once."""
    spec = ExperimentSpec.model_validate({"ego_speed_mps": 4.0, "target_vehicle_speed_mps": 4.0})
    assert spec.model_dump()["target_vehicle_speed_mps"] == CUSTOM_SPEED_MPS


@pytest.mark.parametrize("old", [True, "4.0", 5.0, None])
def test_conflicting_or_invalid_speed_aliases_are_rejected(old: object) -> None:
    """Neither coercion nor a conflicting legacy value can be silently ignored."""
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({"ego_speed_mps": old, "target_vehicle_speed_mps": 4.0})
