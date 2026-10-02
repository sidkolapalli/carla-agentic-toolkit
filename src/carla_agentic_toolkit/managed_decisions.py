"""Revalidate immutable provider context against current numerical applicability."""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.managed_policy import DecisionContext, PolicyDecision

if TYPE_CHECKING:
    from carla_agentic_toolkit.managed_policy import Candidate, PolicyRequest

MAX_DECISION_METADATA_BYTES = 65_536


def decision_metadata(decision: PolicyDecision) -> dict[str, object]:
    """Reject malformed provider envelopes before recording or applying their contents."""
    if not isinstance(decision, PolicyDecision) or not isinstance(
        decision.context, DecisionContext
    ):
        message = "Provider returned an invalid decision envelope."
        raise TypeError(message)
    return _metadata_object(decision.metadata_json)


def _metadata_object(encoded: str) -> dict[str, object]:
    if not isinstance(encoded, str) or len(encoded.encode()) > MAX_DECISION_METADATA_BYTES:
        message = "Provider decision metadata exceeds its bounded string contract."
        raise ValueError(message)
    value = json.loads(encoded)
    if not isinstance(value, dict):
        message = "Provider decision metadata must be an object."
        raise TypeError(message)
    json.dumps(value, allow_nan=False)
    return cast("dict[str, object]", value)


SCENE_IDENTITY_FIELDS = (
    "run_id",
    "world_generation",
    "actor_id",
    "revision",
    "candidate_set_id",
    "maneuver_generation",
    "phase",
)


def decision_rejection(
    decision: PolicyDecision,
    requested: PolicyRequest,
    current: PolicyRequest,
    *,
    now: float,
) -> str | None:
    """Return a specific rejection before any reply reaches local actuation."""
    if decision.context != requested.context:
        return "request_context_changed"
    if _scene_changed(requested, current):
        return "scene_context_changed"
    if not math.isfinite(now) or now > requested.context.deadline_monotonic:
        return "deadline_expired"
    return _candidate_rejection(decision, requested, current)


def _scene_changed(requested: PolicyRequest, current: PolicyRequest) -> bool:
    return any(
        getattr(requested.context, field) != getattr(current.context, field)
        for field in SCENE_IDENTITY_FIELDS
    )


def _candidate_rejection(
    decision: PolicyDecision,
    requested: PolicyRequest,
    current: PolicyRequest,
) -> str | None:
    original = _find_candidate(requested, decision.choice_id)
    available = _find_candidate(current, decision.choice_id)
    if original is None or available is None:
        return "unknown_choice"
    if current.context.observation_frame < requested.context.observation_frame:
        return "observation_frame_reversed"
    if current.context.observation_frame > min(original.expires_frame, available.expires_frame):
        return "candidate_expired"
    return None


def _find_candidate(request: PolicyRequest, choice: str) -> Candidate | None:
    return next(
        (candidate for candidate in request.candidates if candidate.candidate_id == choice), None
    )
