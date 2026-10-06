"""Ownership, frame identity, actuation guards and trace provenance for route driving."""

from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import route_actors, route_experiment
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.managed_policy import PolicyDecision
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.route_geometry import RoutePath, RoutePoint
from carla_agentic_toolkit.route_risk import traffic_evidence
from tests.test_merge_experiment import Session
from tests.test_route_driving import actor

if TYPE_CHECKING:
    from carla_agentic_toolkit.route_models import RouteObservation


def prepared(
    monkeypatch: pytest.MonkeyPatch, scenario: str = "lead_brake"
) -> tuple[Session, route_experiment.RouteExperiment]:
    """Use real route/session contracts with CARLA-free actor handles that cannot tick."""
    module = SimpleNamespace(
        Location=SimpleNamespace,
        Rotation=SimpleNamespace,
        Transform=lambda *args, **kwargs: SimpleNamespace(
            location=args[0] if args else None, **kwargs
        ),
        VehicleControl=SimpleNamespace,
        WalkerControl=SimpleNamespace,
        Vector3D=SimpleNamespace,
    )
    monkeypatch.setattr(route_actors, "import_module", lambda _name: module)
    path = RoutePath((RoutePoint(0.0, 0.0), RoutePoint(165.0, 0.0)))
    monkeypatch.setattr(route_experiment, "select_route", lambda _map: path)
    spec = ExperimentSpec.model_validate({"fixture": "town10-route-ue5-v1", "scenario": scenario})
    session = Session(spec=spec)
    experiment = route_experiment.RouteExperiment(session)
    experiment.prepare()
    monkeypatch.setattr(experiment.actors, "traffic_light", lambda _role="policy": "None")
    return session, experiment


def observed(experiment: route_experiment.RouteExperiment, frame: int = 100) -> RouteObservation:
    """Provide one coherent snapshot without permitting actor live-position reads."""
    handles = {int(handle.id): handle for handle in experiment.actors.handles.values()}

    def frozen(identity: int) -> SimpleNamespace:
        handle = handles[identity]
        return SimpleNamespace(
            get_transform=lambda: SimpleNamespace(
                location=handle.location, rotation=SimpleNamespace(yaw=0.0)
            ),
            get_velocity=lambda: SimpleNamespace(x=0.0, y=0.0),
        )

    return experiment.observe(
        SimpleNamespace(
            frame=frame,
            timestamp=SimpleNamespace(elapsed_seconds=frame * 0.05),
            find=frozen,
        )
    )


@pytest.mark.parametrize("scenario", ["lead_brake", "cut_in", "pedestrian_crossing"])
def test_all_actors_are_journaled_and_every_vehicle_disables_autopilot(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    """Hazards do not create an ownership or Traffic Manager escape hatch."""
    session, _experiment = prepared(monkeypatch, scenario)
    assert {item[0] for item in session.owned} == {item.id for item in session.world.actors}
    assert all(
        item.autopilot == [False]
        for item in session.world.actors
        if item.type_id.startswith("vehicle.")
    )
    assert all(item[2] for item in session.owned)


def test_range_filter_excludes_distant_actors_from_model_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owned background actors outside sensing range are not leaked to the selector."""
    _session, experiment = prepared(monkeypatch)
    experiment.actors.handles["lead"].location.x = 200.0
    value = observed(experiment)
    request = experiment.policy_request(value, revision=1, deadline_monotonic=time.monotonic() + 5)
    assert request is not None
    assert {item.actor.actor_id for item in value.traffic} == {
        experiment.actors.handles["traffic"].id
    }


def test_duplicate_snapshot_is_rejected_and_cleanup_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backend cannot replay a frame or leave collision subscriptions running."""
    _session, experiment = prepared(monkeypatch)
    observed(experiment)
    with pytest.raises(CarlaAdapterError, match="increasing"):
        observed(experiment)
    experiment.close()
    assert experiment.close()["already_closed"] is True


def test_emergency_override_keeps_accepted_jev_choice_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trace cannot attribute local emergency braking to Jev's semantic selection."""
    _session, experiment = prepared(monkeypatch)
    value = observed(experiment)
    policy = replace(value.policy, speed_mps=6.0, vx=6.0)
    risk = traffic_evidence(
        policy, (actor(actor_id=20, x=8.0, progress_m=8.0, vx=0.0, speed_mps=0.0),)
    )
    value = replace(value, policy=policy, traffic=risk)
    request = experiment.policy_request(value, revision=1, deadline_monotonic=time.monotonic() + 5)
    assert request is not None
    decision = PolicyDecision(request.context, "cruise", "jev", "selected")
    result = experiment.advance(value, decision)
    control = experiment.actors.handles["policy"].controls[-1]
    assert (result["requested_choice"], result["executed_choice"], control.brake) == (
        "cruise",
        "local_stop",
        1.0,
    )
    assert result["intervention"] == {"fallback": False, "reason": "imminent_obstacle"}


@pytest.mark.parametrize("failure", ["collision", "off_route"])
def test_terminal_failures_brake_all_owned_vehicles(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """An unsafe run ends explicitly; route completion cannot mask a collision."""
    _session, experiment = prepared(monkeypatch)
    value = observed(experiment)
    value = replace(value, collision=failure == "collision", tracking_error_m=3.0)
    result = experiment.advance(value)
    controls = cast("dict[str, dict[str, float]]", result["controls"])
    assert result["outcome"] == {"completed": False, "status": failure}
    assert all(control["brake"] == 1.0 for control in controls.values())


def test_arrival_requires_actual_position_and_a_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A car racing through the route end is not reported as arrived and parked."""
    _session, experiment = prepared(monkeypatch)
    value = observed(experiment)
    value = replace(value, policy=replace(value.policy, progress_m=134.5, speed_mps=5.0))
    assert experiment.advance(value)["terminal"] is False
    value = replace(value, policy=replace(value.policy, speed_mps=0.0))
    result = experiment.advance(value)
    assert result["outcome"] == {"completed": True, "status": "route_completed"}
    assert result["executed_choice"] == "local_stop"


def test_background_vehicle_stops_before_exhausting_its_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long red-light wait must not let traffic drive beyond the validated geometry."""
    _session, experiment = prepared(monkeypatch)
    value = observed(experiment)
    experiment.values["lead"] = replace(
        experiment.values["lead"], x=160.0, progress_m=160.0, speed_mps=6.0, vx=6.0
    )
    result = experiment.advance(value)
    controls = cast("dict[str, dict[str, float]]", result["controls"])
    assert (controls["lead"]["throttle"], controls["lead"]["brake"]) == (0.0, 1.0)


def test_ue5_walker_spawns_with_capsule_ground_clearance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Walker capsules need more initial vertical clearance than a vehicle chassis."""
    _session, experiment = prepared(monkeypatch, "pedestrian_crossing")
    walker = experiment.actors.handles["pedestrian"]
    assert walker.location.z >= 1.0
