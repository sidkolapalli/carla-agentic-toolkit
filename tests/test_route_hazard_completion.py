"""Physical hazard exposure is not proved by issuing or finishing a command."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from carla_agentic_toolkit.route_geometry import RoutePath, RoutePoint
from carla_agentic_toolkit.route_metrics import route_metrics
from tests.test_route_backend import prepared

if TYPE_CHECKING:
    from carla_agentic_toolkit.route_experiment import RouteExperiment
    from carla_agentic_toolkit.route_models import RouteObservation

WALKER_REQUEST_MPS = 2.0
WALKER_DURATION_SECONDS = 6.0
OBSERVED_SPEED_MPS = 0.09765625
ROTATED_LANE_WIDTH_M = 4.0


def _observe(
    experiment: RouteExperiment,
    frame: int,
    seconds: float,
    lateral_m: float,
    *,
    progress_m: float = 12.0,
) -> RouteObservation:
    role = "pedestrian" if experiment.spec.scenario == "pedestrian_crossing" else "lead"
    hazard = experiment.actors.handles[role]
    point = experiment.path.sample(32.0 if role == "pedestrian" else 14.0, lateral_m)
    hazard.location = SimpleNamespace(x=point.x, y=point.y, z=0.95)
    ego = experiment.path.sample(progress_m)
    experiment.actors.handles["policy"].location = SimpleNamespace(x=ego.x, y=ego.y, z=0.5)
    handles = {int(actor.id): actor for actor in experiment.actors.handles.values()}

    def frozen(actor_id: int) -> SimpleNamespace:
        actor = handles[actor_id]
        return SimpleNamespace(
            get_transform=lambda: SimpleNamespace(
                location=actor.location, rotation=SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0)
            ),
            get_velocity=lambda: SimpleNamespace(
                x=0.0, y=-OBSERVED_SPEED_MPS if actor is hazard else 0.0
            ),
        )

    return experiment.observe(
        SimpleNamespace(
            frame=frame, timestamp=SimpleNamespace(elapsed_seconds=seconds), find=frozen
        )
    )


def _advance(
    experiment: RouteExperiment,
    frame: int,
    seconds: float,
    lateral_m: float,
    *,
    progress_m: float = 12.0,
) -> dict[str, Any]:
    return experiment.advance(
        _observe(experiment, frame, seconds, lateral_m, progress_m=progress_m)
    )


def _assert_invalid(report: dict[str, Any], reason: str, deadline: float) -> dict[str, Any]:
    trial = report["hazard_trial"]
    assert trial["valid"] is False
    assert (trial["status"], trial["reason"]) == ("invalid", reason)
    assert trial["deadline_seconds"] == deadline
    return trial


def test_slow_walker_command_completion_does_not_prove_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce the retained six-second command with its measured slow displacement."""
    _session, experiment = prepared(monkeypatch, "pedestrian_crossing")
    first = _advance(experiment, 100, 10.0, 6.0)
    assert experiment.actors.handles["pedestrian"].controls[-1].speed == WALKER_REQUEST_MPS
    last = _advance(experiment, 220, 16.0, 5.41406245)
    assert last["hazard"]["active"] is False
    trial = _assert_invalid(last, "lane_entry_not_observed_by_deadline", 16.0)
    _assert_slow_motion_evidence(trial["measured"])
    assert first["hazard_trial"]["commanded"]["duration_seconds"] == WALKER_DURATION_SECONDS


def _assert_slow_motion_evidence(measured: dict[str, Any]) -> None:
    assert measured["displacement_m"] == pytest.approx(0.58593755)
    assert measured["speed_mps"] == pytest.approx(OBSERVED_SPEED_MPS)
    assert measured["first_lane_entry"] is None
    assert measured["first_centreline_crossing"] is None


def test_route_completion_remains_independent_of_invalid_hazard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stopped ego at its goal is still route-complete, not a successful crossing trial."""
    session, experiment = prepared(monkeypatch, "pedestrian_crossing")
    _advance(experiment, 100, 10.0, 6.0)
    report = _advance(experiment, 221, 16.05, 5.41406245, progress_m=134.5)
    assert report["outcome"] == {"completed": True, "status": "route_completed"}
    assert report["terminal"] is True
    _assert_invalid(report, "lane_entry_not_observed_by_deadline", 16.0)
    assert experiment.close()["sensors"]
    assert all(
        actor.callback is None
        for actor in session.world.actors
        if actor.type_id.startswith("sensor.")
    )


def test_cut_in_target_is_not_physical_entry_and_late_entry_stays_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preserved 2.5-second target and 3.5-second lane entry remain separate evidence."""
    _session, experiment = prepared(monkeypatch, "cut_in")
    _advance(experiment, 100, 10.0, 3.5)
    deadline = _advance(experiment, 150, 12.5, 2.650)
    trial = _assert_invalid(deadline, "lane_entry_not_observed_by_deadline", 12.5)
    assert trial["commanded"]["target_lateral_m"] == 0.0
    assert trial["measured"]["lateral_m"] == pytest.approx(2.650)
    late = _advance(experiment, 170, 13.5, 1.70)
    measured = _assert_invalid(late, "lane_entry_not_observed_by_deadline", 12.5)["measured"]
    assert measured["first_lane_entry"] == {"frame": 170, "simulation_seconds": 13.5}
    assert measured["first_centreline_crossing"] is None


@pytest.mark.parametrize("scenario", ["pedestrian_crossing", "cut_in"])
def test_physical_completion_is_measured_before_deadline(
    monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """Positive exposure needs observed actor-origin geometry, never a steering target."""
    _session, experiment = prepared(monkeypatch, scenario)
    _advance(experiment, 100, 10.0, 6.0 if scenario == "pedestrian_crossing" else 3.5)
    _advance(experiment, 120, 11.0, 1.5)
    report = _advance(experiment, 140, 12.0, -0.1)
    trial = report["hazard_trial"]
    assert (trial["valid"], trial["status"], trial["reason"]) == (True, "achieved", None)
    assert trial["measured"]["first_lane_entry"] == {
        "frame": 120,
        "simulation_seconds": 11.0,
    }
    assert trial["measured"]["first_centreline_crossing"] == {
        "frame": 140,
        "simulation_seconds": 12.0,
    }


def test_lane_entry_alone_does_not_prove_pedestrian_centreline_crossing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pedestrian remaining on the starting side is a different failed exposure."""
    _session, experiment = prepared(monkeypatch, "pedestrian_crossing")
    _advance(experiment, 100, 10.0, 6.0)
    _advance(experiment, 120, 11.0, 1.5)
    report = _advance(experiment, 220, 16.0, 0.2)
    trial = _assert_invalid(report, "centreline_crossing_not_observed_by_deadline", 16.0)
    assert trial["measured"]["first_lane_entry"] is not None
    assert trial["measured"]["first_centreline_crossing"] is None


def test_initially_inside_lane_does_not_manufacture_cut_in_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An already present actor has not been observed crossing the outside boundary."""
    _session, experiment = prepared(monkeypatch, "cut_in")
    _advance(experiment, 100, 10.0, 1.0)
    report = _advance(experiment, 150, 12.5, 0.0)
    trial = _assert_invalid(report, "hazard_not_outside_lane_at_trigger", 12.5)
    assert trial["measured"]["first_lane_entry"] is None


def test_fixed_local_lane_geometry_uses_actual_width_and_tangent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rotated four-metre lane cannot inherit a hard-coded 1.75m boundary."""
    path = RoutePath((RoutePoint(0.0, 0.0, width_m=4.0), RoutePoint(0.0, 165.0, width_m=4.0)))
    _session, experiment = prepared(monkeypatch, "cut_in", path=path)
    _advance(experiment, 100, 10.0, 3.5)
    report = _advance(experiment, 120, 11.0, 1.9)
    trial = report["hazard_trial"]
    assert trial["valid"] is True
    assert trial["geometry"]["lane_width_m"] == ROTATED_LANE_WIDTH_M
    assert trial["geometry"]["yaw_degrees"] == pytest.approx(90.0)
    assert trial["measured"]["lateral_m"] == pytest.approx(1.9)


@pytest.mark.parametrize("termination", ["frame_limit", "cancelled", "failed", "collision"])
def test_terminal_summary_marks_unfinished_hazard_without_losing_cause(
    monkeypatch: pytest.MonkeyPatch, termination: str
) -> None:
    """Early termination never turns an outstanding physical deadline into success."""
    _session, experiment = prepared(monkeypatch, "pedestrian_crossing")
    _advance(experiment, 100, 10.0, 6.0)
    report = experiment.final_summary(termination=termination)
    trial = _assert_invalid(report, "trial_ended_before_deadline", 16.0)
    assert trial["termination"] == termination


def test_never_triggered_trial_is_invalid_when_route_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished run without an exposure trigger has no measured hazard success."""
    _session, experiment = prepared(monkeypatch, "cut_in")
    _advance(experiment, 100, 10.0, 3.5, progress_m=0.0)
    trial = cast(
        "dict[str, Any]", experiment.final_summary(termination="frame_limit")["hazard_trial"]
    )
    assert (trial["valid"], trial["reason"], trial["deadline_seconds"]) == (
        False,
        "hazard_not_triggered",
        None,
    )


def test_diagnostic_hazard_measurements_do_not_enter_provider_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An out-of-range owned walker is observable to the fixture, not leaked to the policy."""
    _session, experiment = prepared(monkeypatch, "pedestrian_crossing")
    experiment.spec = experiment.spec.model_copy(update={"observation_range_m": 5.0})
    value = _observe(experiment, 100, 10.0, 6.0)
    assert not value.traffic
    request = experiment.policy_request(value, revision=1, deadline_monotonic=time.monotonic() + 5)
    assert request is not None
    evidence = json.loads(request.evidence_json)
    assert not {"hazard_trial", "commanded", "measured", "scenario"}.intersection(evidence)
    report = cast("dict[str, Any]", experiment.advance(value))
    assert (
        report["hazard_trial"]["measured"]["actor_id"] == experiment.actors.handles["pedestrian"].id
    )


def test_legacy_route_trace_is_explicitly_unverified_not_valid() -> None:
    """An old completed route and inactive command do not retrospectively prove exposure."""
    events = (
        {
            "kind": "observation",
            "data": {"route_progress_m": 134.6, "route_length_m": 135.0, "traffic": []},
        },
        {
            "kind": "execution",
            "data": {
                "hazard": {"scenario": "pedestrian_crossing", "active": False},
                "intervention": {},
            },
        },
        {"kind": "outcome", "data": {"completed": True, "status": "route_completed"}},
    )
    assert route_metrics(events)["route_hazard_trial"] == {"valid": None, "status": "unverified"}


def test_trace_metric_retains_explicit_invalid_trial_and_late_evidence() -> None:
    """Summaries keep route progress separate from recorded physical validity."""
    trial = {
        "valid": False,
        "status": "invalid",
        "reason": "lane_entry_not_observed",
        "deadline_seconds": 12.5,
        "measured": {"first_lane_entry": {"frame": 170, "simulation_seconds": 13.5}},
    }
    events = (
        {
            "kind": "observation",
            "data": {"route_progress_m": 134.6, "route_length_m": 135.0, "traffic": []},
        },
        {"kind": "fixture_summary", "data": {"hazard_trial": trial}},
    )
    metrics = route_metrics(events)
    assert metrics["route_hazard_trial"] == trial
    assert metrics["route_progress_m"] == pytest.approx(134.6)


@pytest.mark.parametrize("kind", ["diagnostic", "decision_received", "metadata", "observation"])
def test_unreviewed_event_cannot_make_legacy_hazard_valid(kind: str) -> None:
    """Provider and diagnostic JSON is not authoritative physical completion evidence."""
    events = (
        _route_event(),
        {"kind": kind, "data": _untrusted_trial()},
    )
    assert route_metrics(events)["route_hazard_trial"] == {"valid": None, "status": "unverified"}


def test_provider_event_cannot_supersede_recorded_invalid_hazard() -> None:
    """A later provider payload cannot replace the fixture's observed missed deadline."""
    trial = {"valid": False, "status": "invalid", "reason": "lane_entry_not_observed_by_deadline"}
    events = (
        _route_event(),
        {"kind": "fixture_summary", "data": {"hazard_trial": trial}},
        {"kind": "decision_received", "data": _untrusted_trial()},
    )
    assert route_metrics(events)["route_hazard_trial"] == trial


def _route_event() -> dict[str, Any]:
    return {
        "kind": "observation",
        "data": {"route_progress_m": 134.6, "route_length_m": 135.0, "traffic": []},
    }


def _untrusted_trial() -> dict[str, Any]:
    return {
        "hazard_trial": {"valid": True, "status": "achieved"},
        "context": {"revision": 1},
        "source": "jev",
        "choice_id": "yield",
    }
