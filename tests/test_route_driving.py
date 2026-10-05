"""Route tactics must remain distinct from geometry, emergency control, and scenario scripts."""

from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest
from pydantic import ValidationError

from carla_agentic_toolkit.managed_jev_questions import build_query, validate_fallback
from carla_agentic_toolkit.managed_policy import PolicyDecision, PolicyRequest
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.route_geometry import RoutePath, RoutePoint, tracking_control
from carla_agentic_toolkit.route_models import RouteActor, RouteObservation
from carla_agentic_toolkit.route_policy import RouteSelection, build_request, rules_decision
from carla_agentic_toolkit.route_risk import emergency_reason, traffic_evidence
from carla_agentic_toolkit.route_scenarios import HazardState, update_hazard


def actor(**changes: object) -> RouteActor:
    """Start with a moving vehicle on a straight route."""
    return replace(RouteActor(1, "vehicle", 0.0, 0.0, 0.0, 6.0, 6.0, 0.0), **changes)


def observation(**changes: object) -> RouteObservation:
    """Keep scene identity explicit in all tests."""
    return replace(RouteObservation("a" * 32, "world", 100, 5.0, actor(), 135.0), **changes)


def request(value: RouteObservation | None = None) -> PolicyRequest:
    """Produce numerical choices without CARLA or a provider."""
    return build_request(value or observation(), 6.0, 30, revision=2, deadline_monotonic=100.0)


@pytest.mark.parametrize("scenario", ["lead_brake", "cut_in", "pedestrian_crossing"])
def test_explicit_route_scenarios_roundtrip(scenario: str) -> None:
    """The same immutable spec reaches both CLI and MCP without code injection."""
    spec = ExperimentSpec.model_validate({"fixture": "town10-route-ue5-v1", "scenario": scenario})
    assert ExperimentSpec.model_validate_json(spec.model_dump_json()) == spec


@pytest.mark.parametrize(
    "values",
    [
        {"fixture": "town10-route-ue5-v1"},
        {"scenario": "lead_brake"},
        {"fixture": "town10-route-ue5-v1", "scenario": "arbitrary_script"},
    ],
)
def test_scenarios_cannot_be_silently_ignored(values: dict[str, object]) -> None:
    """Ambiguous fixture/scenario combinations fail before ownership."""
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(values)


def test_route_projection_tracks_progress_around_a_corner() -> None:
    """Distance along a curved route is not distance along its first straight."""
    path = RoutePath((RoutePoint(0, 0), RoutePoint(10, 0), RoutePoint(10, 10)))
    point = path.project(11.0, 5.0)
    assert (point.progress_m, point.lateral_m, path.length_m) == pytest.approx((15, -1, 20))
    assert path.sample(15.0).y == pytest.approx(5)


def test_tracker_steers_toward_route_and_brakes_for_stop() -> None:
    """A semantic speed choice never supplies steering or bypasses actuator bounds."""
    path = RoutePath((RoutePoint(0, 0), RoutePoint(30, 0)))
    control = tracking_control(path, actor(y=1.0), target_speed_mps=0.0)
    assert (control.throttle, control.brake) == (0, 1)
    assert -1 <= control.steer < 0


def test_tracker_follows_a_bend_with_ue5_effective_steering() -> None:
    """A bicycle regression catches the understeer seen in the first live UE5 trial."""
    points = tuple(
        RoutePoint(15 * math.sin(i / 40), 15 * (1 - math.cos(i / 40))) for i in range(64)
    )
    path = RoutePath(points)
    vehicle = actor(speed_mps=5.0)
    errors = []
    for _ in range(75):
        control = tracking_control(path, vehicle, target_speed_mps=5.0)
        heading = math.radians(vehicle.yaw_degrees)
        next_heading = heading + 0.05 * 5 / 2.85 * math.tan(control.steer * math.radians(35))
        vehicle = replace(
            vehicle,
            x=vehicle.x + 0.25 * math.cos(heading),
            y=vehicle.y + 0.25 * math.sin(heading),
            yaw_degrees=math.degrees(next_heading),
        )
        errors.append(path.project(vehicle.x, vehicle.y).distance_m)
    assert max(errors) < 1


def test_adjacent_vehicle_is_not_a_frontal_obstacle() -> None:
    """Oriented footprints avoid false collisions from vehicle circumcircles."""
    risk = traffic_evidence(actor(), (actor(actor_id=2, y=3.5, lateral_m=3.5),))[0]
    assert risk.clearance_m == pytest.approx(1.5)
    assert (risk.predicted_overlap_seconds, risk.ahead_gap_m) == (None, None)


def test_crossing_walker_is_predicted_before_entering_the_lane() -> None:
    """Constant-velocity crossing evidence includes a currently off-lane pedestrian."""
    walker = actor(
        actor_id=3,
        kind="walker",
        x=12.0,
        y=4.0,
        vx=0.0,
        vy=-2.0,
        speed_mps=2.0,
        length_m=0.6,
        width_m=0.6,
    )
    risk = traffic_evidence(actor(), (walker,))[0]
    assert risk.predicted_overlap_seconds is not None
    assert risk.predicted_overlap_seconds == pytest.approx(1.5, abs=0.5)


def test_emergency_braking_is_independent_of_jev_choice() -> None:
    """An accepted cruise instruction cannot drive into an imminent stopped vehicle."""
    risks = traffic_evidence(
        actor(), (actor(actor_id=2, x=8.0, progress_m=8.0, vx=0.0, speed_mps=0.0),)
    )
    assert emergency_reason(observation(traffic=risks)) == "imminent_obstacle"


def test_route_question_has_route_semantics_and_no_future_hazard_script() -> None:
    """Jev sees current observations and candidate speeds, not a scenario's future trigger."""
    value = request()
    validate_fallback(value, "yield")
    state, criteria = build_query(value)
    assert set(criteria) == {"cruise", "caution", "yield"}
    assert "merge" not in " ".join(criteria.values()).lower()
    assert set(json.loads(state)["evidence"]).isdisjoint({"scenario", "trigger_progress_m"})


def test_rules_yields_for_crossing_risk_but_cruises_when_clear() -> None:
    """The no-key baseline responds to the same numerical evidence as Jev."""
    walker = actor(
        actor_id=3,
        kind="walker",
        x=12.0,
        y=4.0,
        vx=0.0,
        vy=-2.0,
        speed_mps=2.0,
        length_m=0.6,
        width_m=0.6,
    )
    risks = traffic_evidence(actor(), (walker,))
    assert rules_decision(request(observation(traffic=risks))).choice_id == "yield"
    assert rules_decision(request()).choice_id == "cruise"


def test_selection_holds_between_requests_but_expires_in_simulation_frames() -> None:
    """Tactical commands persist across ticks without becoming unlimited authorizations."""
    selection = RouteSelection()
    original = request()
    selection.remember(original)
    decision = PolicyDecision(original.context, "cruise", "jev", "selected")
    assert selection.choose(observation(), decision, now=90.0).choice == "cruise"
    assert selection.choose(observation(frame=120), None, now=101.0).choice == "cruise"
    assert selection.choose(observation(frame=131), None, now=102.0).choice == "yield"


def test_selection_rejects_unknown_identity_and_world_changes() -> None:
    """A previously accepted decision cannot migrate to another world generation."""
    selection = RouteSelection()
    original = request()
    decision = PolicyDecision(original.context, "cruise", "jev", "selected")
    assert selection.choose(observation(), decision, now=90.0).choice == "yield"
    selection.remember(original)
    selection.choose(observation(), decision, now=90.0)
    assert selection.choose(observation(world_generation="other"), None, now=90.0).choice == "yield"


def test_braking_hazard_is_triggered_once_and_releases_after_three_seconds() -> None:
    """The scripted lead brake is tied to measured route progress and simulation time."""
    state = update_hazard(HazardState(), "lead_brake", progress_m=9.0, seconds=5.0)
    assert state.started_seconds is None
    state = update_hazard(state, "lead_brake", progress_m=12.0, seconds=6.0)
    assert state.active
    state = update_hazard(state, "lead_brake", progress_m=13.0, seconds=9.1)
    assert (state.active, state.started_seconds) == (False, 6.0)
