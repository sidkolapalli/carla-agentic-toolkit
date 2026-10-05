"""Versioned route speed choices, no-key baseline, and bounded command retention."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from carla_agentic_toolkit.managed_decisions import decision_rejection
from carla_agentic_toolkit.managed_policy import (
    Candidate,
    DecisionContext,
    PolicyDecision,
    PolicyRequest,
)

if TYPE_CHECKING:
    from carla_agentic_toolkit.route_models import RouteObservation

PLANNER_VERSION = "route-tactics-v1"
YIELD_HORIZON_SECONDS = 3.0
CAUTION_HORIZON_SECONDS = 4.0
MAX_REQUESTS_RETAINED = 32
SPEED_FACTORS = {"cruise": 1.0, "caution": 0.45, "yield": 0.0}


def build_request(
    value: RouteObservation,
    target_speed_mps: float,
    expiry_frames: int,
    *,
    revision: int,
    deadline_monotonic: float,
) -> PolicyRequest:
    """Offer reviewed tactical speeds; local code owns the route and collision guard."""
    candidates = tuple(
        Candidate(choice, 0, target_speed_mps * factor, 4.0, value.frame + expiry_frames)
        for choice, factor in SPEED_FACTORS.items()
    )
    identities = [
        {k: v for k, v in asdict(item).items() if k != "expires_frame"} for item in candidates
    ]
    candidate_id = hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()[:24]
    context = DecisionContext(
        value.run_id,
        value.world_generation,
        value.policy.actor_id,
        revision,
        value.frame,
        candidate_id,
        0,
        value.phase,
        deadline_monotonic,
    )
    evidence = {**value.to_dict(), "planner_version": PLANNER_VERSION}
    # Sensor payloads belong in the trace, not in a bounded semantic question.
    evidence.pop("sensors")
    return PolicyRequest(context, candidates, json.dumps(evidence, allow_nan=False, sort_keys=True))


def _risk_level(risk: dict[str, object], speed: float) -> int:
    overlap, gap = risk["predicted_overlap_seconds"], risk["ahead_gap_m"]
    if isinstance(overlap, (int, float)) and overlap < YIELD_HORIZON_SECONDS:
        return 2
    if isinstance(gap, (int, float)) and gap < 3.0 + speed * 1.5:
        return 2
    return int(_caution(overlap, gap, speed))


def _caution(overlap: object, gap: object, speed: float) -> bool:
    return (isinstance(overlap, (int, float)) and overlap <= CAUTION_HORIZON_SECONDS) or (
        isinstance(gap, (int, float)) and gap < 8.0 + speed * 3.0
    )


def rules_decision(request: PolicyRequest) -> PolicyDecision:
    """React to the same public measurements as Jev using documented fixed thresholds."""
    evidence = json.loads(request.evidence_json)
    levels = [_risk_level(item, evidence["speed_mps"]) for item in evidence["traffic"]]
    choice = ("cruise", "caution", "yield")[max(levels, default=0)]
    return PolicyDecision(request.context, choice, "rules", "Measured route traffic thresholds.")


@dataclass(frozen=True, slots=True)
class SelectedAction:
    """Provenance survives every frame between two decision boundaries."""

    choice: str = "yield"
    source: str = "fallback"
    reason: str | None = "no_current_decision"
    observation_frame: int | None = None


class RouteSelection:
    """Accept exact identities once, then hold within the candidate's simulation horizon."""

    def __init__(self) -> None:
        """Begin stopped, with a bounded request registry."""
        self.requests: dict[DecisionContext, PolicyRequest] = {}
        self.held: tuple[PolicyDecision, Candidate] | None = None

    def remember(self, request: PolicyRequest) -> None:
        """Keep outstanding identities, including original requests during paced inference."""
        self.requests[request.context] = request
        while len(self.requests) > MAX_REQUESTS_RETAINED:
            del self.requests[next(iter(self.requests))]

    def choose(
        self, value: RouteObservation, decision: PolicyDecision | None, *, now: float
    ) -> SelectedAction:
        """Replace a previous command with a visible stop on an invalid or fallback reply."""
        if decision is not None:
            rejected = self._accept(value, decision, now)
            if rejected is not None:
                self.held = None
                return SelectedAction(reason=rejected)
        return self._held_action(value)

    def _accept(self, value: RouteObservation, decision: PolicyDecision, now: float) -> str | None:
        original = self.requests.get(decision.context)
        if original is None:
            return "unknown_request_identity"
        current = build_request(
            value,
            original.candidates[0].target_speed_mps,
            1,
            revision=decision.context.revision,
            deadline_monotonic=decision.context.deadline_monotonic,
        )
        reason = decision_rejection(decision, original, current, now=now)
        if reason is None:
            candidate = next(
                item for item in original.candidates if item.candidate_id == decision.choice_id
            )
            self.held = decision, candidate
        return reason

    def _held_action(self, value: RouteObservation) -> SelectedAction:
        if self.held is None:
            return SelectedAction()
        decision, candidate = self.held
        context = decision.context
        identity = (value.run_id, value.world_generation, value.policy.actor_id, value.phase)
        original = (context.run_id, context.world_generation, context.actor_id, context.phase)
        if (
            identity != original
            or not context.observation_frame <= value.frame <= candidate.expires_frame
        ):
            self.held = None
            return SelectedAction(reason="held_decision_expired_or_scene_changed")
        reason = decision.reason if decision.source == "fallback" else None
        return SelectedAction(
            decision.choice_id, decision.source, reason, context.observation_frame
        )
