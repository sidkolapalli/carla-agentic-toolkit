"""Managed fixtures must not construct or contact a Traffic Manager."""

from __future__ import annotations

import pytest

from tests.test_merge_experiment import Actor, Session, World, _prepare
from tests.test_route_backend import prepared


def _reject_tm_access(*_args: object, **_kwargs: object) -> None:
    """Turn an unintended TM or autopilot call into an immediate failure."""
    pytest.fail("Managed fixtures must not call autopilot or reach a Traffic Manager.")


@pytest.mark.parametrize("fixture", ["merge", "lead_brake", "cut_in", "pedestrian_crossing"])
def test_managed_fixture_does_not_reach_traffic_manager(
    monkeypatch: pytest.MonkeyPatch, fixture: str
) -> None:
    """Even disabling autopilot would construct a native TM before checking the flag."""
    monkeypatch.setattr(Actor, "set_autopilot", _reject_tm_access)
    monkeypatch.setattr(World, "get_trafficmanager", _reject_tm_access, raising=False)
    monkeypatch.setattr(Session, "get_trafficmanager", _reject_tm_access, raising=False)
    session, _experiment = (
        _prepare(monkeypatch) if fixture == "merge" else prepared(monkeypatch, fixture)
    )
    assert session.world.actors
    assert {item[0] for item in session.owned} == {actor.id for actor in session.world.actors}
