"""Only still-applicable provider replies can reach local numerical actuation."""

from __future__ import annotations

from dataclasses import replace

import pytest

from carla_agentic_toolkit.managed_decisions import decision_rejection
from carla_agentic_toolkit.managed_policy import (
    Candidate,
    DecisionContext,
    PolicyDecision,
    PolicyRequest,
)


def _request() -> PolicyRequest:
    context = DecisionContext("run", "world", 1, 2, 100, "candidates", 0, "preparing", 20.0)
    return PolicyRequest(context, (Candidate("merge", -2, 6.0, 3.0, 120),), "{}")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "another"),
        ("world_generation", "reloaded"),
        ("actor_id", 2),
        ("revision", 3),
        ("observation_frame", 99),
        ("candidate_set_id", "changed"),
        ("maneuver_generation", 1),
        ("phase", "committed"),
        ("deadline_monotonic", 30.0),
    ],
)
def test_reply_must_match_its_exact_request(field: str, value: object) -> None:
    """Even a plausible recent answer from a different context must be rejected."""
    request = _request()
    reply = PolicyDecision(replace(request.context, **{field: value}), "merge", "jev", "chosen")
    assert decision_rejection(reply, request, request, now=10.0) == "request_context_changed"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "another"),
        ("world_generation", "reloaded"),
        ("actor_id", 2),
        ("revision", 3),
        ("candidate_set_id", "changed"),
        ("maneuver_generation", 1),
        ("phase", "committed"),
    ],
)
def test_current_scene_must_still_match_identity(field: str, value: object) -> None:
    """World reload, reused IDs, new phases and superseded requests invalidate replies."""
    request = _request()
    current = replace(request, context=replace(request.context, **{field: value}))
    reply = PolicyDecision(request.context, "merge", "jev", "chosen")
    assert decision_rejection(reply, request, current, now=10.0) == "scene_context_changed"


def test_paced_reply_can_be_older_but_must_remain_bounded_and_applicable() -> None:
    """Current numerical candidates must still allow the same action before expiry."""
    request = _request()
    reply = PolicyDecision(request.context, "merge", "jev", "chosen")
    current = replace(request, context=replace(request.context, observation_frame=105))
    assert decision_rejection(reply, request, current, now=10.0) is None
    assert decision_rejection(reply, request, current, now=21.0) == "deadline_expired"
    expired = replace(current, context=replace(current.context, observation_frame=121))
    assert decision_rejection(reply, request, expired, now=10.0) == "candidate_expired"
    assert (
        decision_rejection(replace(reply, choice_id="unknown"), request, current, now=10.0)
        == "unknown_choice"
    )
