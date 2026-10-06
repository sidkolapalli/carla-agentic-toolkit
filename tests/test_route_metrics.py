"""Route summaries must keep semantic selections separate from local interventions."""

from typing import Any

import pytest

from carla_agentic_toolkit.route_metrics import route_metrics


def test_route_summary_excludes_rejected_decisions_and_retains_guard_counts() -> None:
    """A rejected cruise reply cannot count as an accepted Jev choice."""
    data: tuple[dict[str, Any], ...] = (
        {
            "kind": "observation",
            "data": {
                "route_progress_m": 20.0,
                "route_length_m": 135.0,
                "traffic": [{"clearance_m": 4.5}],
            },
        },
        {
            "kind": "observation",
            "data": {
                "route_progress_m": 134.6,
                "route_length_m": 135.0,
                "traffic": [{"clearance_m": 8.0}],
            },
        },
        {
            "kind": "decision_received",
            "data": {"source": "jev", "choice_id": "cruise", "context": {"revision": 1}},
        },
        {"kind": "validation", "data": {"accepted": False, "request_revision": 1}},
        {
            "kind": "decision_received",
            "data": {"source": "jev", "choice_id": "yield", "context": {"revision": 2}},
        },
        {"kind": "validation", "data": {"accepted": True, "request_revision": 2}},
        {"kind": "execution", "data": {"intervention": {"reason": "imminent_obstacle"}}},
    )
    metrics = route_metrics(data)
    assert metrics["route_accepted_choices_by_source"] == {"jev": {"yield": 1}}
    assert metrics["route_local_guard_frames"] == {"imminent_obstacle": 1}
    assert metrics["route_minimum_observed_box_clearance_m"] == pytest.approx(4.5)
    assert metrics["route_progress_m"] == pytest.approx(134.6)


def test_merge_traces_do_not_acquire_unmeasured_route_metrics() -> None:
    """Existing merge reports preserve their original physical definitions."""
    assert route_metrics(({"kind": "observation", "data": {"phase": "following"}},)) == {}
