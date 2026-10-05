"""Offline studies preserve exposure time, rejected replies and exact frame identity."""

from typing import Any

import pytest

from scripts.route_study import analyze_trial, frame_pairs, intervals, percentile, scenario_fidelity

ATTEMPTS = 3


def _sample(seconds: float, **changes: object) -> dict[str, Any]:
    return {
        "frame": int(seconds * 20) + 100,
        "time_s": seconds,
        "speed_mps": 2.0,
        "progress_m": seconds * 2,
        "tracking_error_m": 0.2,
        "hazard_active": False,
        "hazard_clearance_m": 8.0,
        "all_traffic_clearance_m": 2.0,
        "choice_source": "jev",
        "requested_choice": "cruise",
        "intervention_reason": None,
        **changes,
    }


def _trial() -> dict[str, Any]:
    return {
        "run_id": "study",
        "scenario": "pedestrian_crossing",
        "trigger_s": 0.05,
        "outcome": {"completed": True},
        "cleanup_ok": True,
        "collision_events": 0,
        "samples": [
            _sample(0.0),
            _sample(0.05, hazard_active=True, intervention_reason="traffic_light_stop"),
            _sample(0.1, choice_source="fallback", requested_choice="yield"),
        ],
        "decisions": [
            {
                "time_s": 0.0,
                "choice": "cruise",
                "source": "jev",
                "accepted": True,
                "latency_ms": 100.0,
                "attempts": 1,
                "reason": "selected",
            },
            {
                "time_s": 0.05,
                "choice": "yield",
                "source": "fallback",
                "accepted": True,
                "latency_ms": 200.0,
                "attempts": 1,
                "reason": "invalid_response",
            },
            {
                "time_s": 0.1,
                "choice": "cruise",
                "source": "jev",
                "accepted": False,
                "latency_ms": 300.0,
                "attempts": 1,
                "reason": "rejected",
            },
        ],
    }


def test_final_control_has_no_unobserved_exposure_interval() -> None:
    """Do not invent a final timestep after the terminal observation and cleanup."""
    assert intervals(_trial()["samples"]) == pytest.approx([0.05, 0.05, 0.0])


@pytest.mark.parametrize("times", [(0.1, 0.1), (0.2, 0.1), (0.0, float("nan"))])
def test_invalid_time_axis_is_rejected(times: tuple[float, float]) -> None:
    """Repeated, backward and nonfinite time cannot enter duration or acceleration metrics."""
    samples = [{"time_s": value} for value in times]
    with pytest.raises(ValueError, match="time"):
        intervals(samples)


def test_valid_fallback_is_not_a_model_success() -> None:
    """Source and validation both matter when counting successful Jev selections."""
    result = analyze_trial(_trial())
    assert result["accepted_jev_choices"] == {"cruise": 1}
    assert result["provider_fallbacks"] == {"invalid_response": 1}
    assert result["rejected_decisions"] == 1
    assert result["provider_attempts"] == ATTEMPTS


def test_exposure_excludes_unobserved_terminal_fallback() -> None:
    """Keep elapsed time, signal intervals and provider fallback decisions distinct."""
    result = analyze_trial(_trial())
    assert result["signal_guard_s"] == pytest.approx(0.05)
    assert result["fallback_exposure_s"] == 0.0
    assert result["elapsed_s"] == pytest.approx(0.1)
    assert result["first_post_trigger"]["source"] == "fallback"


def test_hazard_clearance_is_distinct_from_nearest_other_actor() -> None:
    """An adjacent background car cannot stand in for the intended hazard's distance."""
    result = analyze_trial(_trial())
    assert result["hazard_min_clearance_m"] == pytest.approx(8.0)
    assert result["all_traffic_min_clearance_m"] == pytest.approx(2.0)


def test_percentiles_keep_singleton_and_linear_interpolation_explicit() -> None:
    """Quantiles are descriptive per-attempt order statistics, not confidence intervals."""
    assert percentile([123.0], 0.95) == pytest.approx(123.0)
    assert percentile([100.0, 200.0, 300.0], 0.95) == pytest.approx(290.0)
    assert percentile([], 0.5) is None


def test_frame_join_does_not_use_nearest_neighbor_matching() -> None:
    """Missing controls or duplicated observations must fail rather than shift a response."""
    events = [
        {"kind": "observation", "frame": 10, "data": {"frame": 10}},
        {"kind": "execution", "frame": 11, "data": {"frame": 11}},
    ]
    with pytest.raises(ValueError, match="frame"):
        frame_pairs(events)
    events[1]["frame"] = 10
    events[1]["data"] = {"frame": 10}
    assert len(frame_pairs(events)) == 1
    with pytest.raises(ValueError, match="Duplicate"):
        frame_pairs([*events, events[0]])


def test_hazard_response_time_is_relative_to_trigger_not_run_start() -> None:
    """Do not attribute an earlier caution choice to a later scripted hazard."""
    trial = _trial()
    trial["trigger_s"] = 0.025
    result = analyze_trial(trial)
    assert result["first_post_trigger"]["after_trigger_s"] == pytest.approx(0.025)
    assert result["last_pre_trigger"]["choice"] == "cruise"


def test_route_completion_does_not_validate_a_failed_pedestrian_crossing() -> None:
    """A walker can move while remaining outside the driving corridor for the whole event."""
    trial = _trial()
    trial["samples"] = [
        _sample(
            0.0,
            hazard_active=True,
            hazard_x=0.0,
            hazard_y=6.0,
            hazard_lateral_m=6.0,
            hazard_speed_mps=0.1,
        ),
        _sample(6.0, hazard_x=0.0, hazard_y=5.4, hazard_lateral_m=5.4, hazard_speed_mps=0.0),
    ]
    trial["lane_width_m"] = 3.5
    result = scenario_fidelity(trial)
    assert result["active_path_length_m"] == pytest.approx(0.6)
    assert result["active_position_speed_median_mps"] == pytest.approx(0.1)
    assert result["entered_route_lane"] is False
    assert result["crossed_route_centreline"] is False


def test_lane_entry_uses_measured_position_after_the_command_window() -> None:
    """A smooth lane-change target is not proof that the physical car reached it on time."""
    trial = _trial()
    trial["scenario"] = "cut_in"
    trial["trigger_s"] = 0.0
    trial["lane_width_m"] = 3.5
    trial["samples"] = [
        _sample(
            0.0,
            hazard_active=True,
            hazard_x=0.0,
            hazard_y=3.5,
            hazard_lateral_m=3.5,
            hazard_speed_mps=2.0,
        ),
        _sample(2.5, hazard_x=5.0, hazard_y=2.5, hazard_lateral_m=2.5, hazard_speed_mps=2.0),
        _sample(4.0, hazard_x=8.0, hazard_y=1.5, hazard_lateral_m=1.5, hazard_speed_mps=2.0),
    ]
    result = scenario_fidelity(trial)
    assert result["entered_route_lane"] is True
    assert result["first_lane_entry_after_trigger_s"] == pytest.approx(4.0)
