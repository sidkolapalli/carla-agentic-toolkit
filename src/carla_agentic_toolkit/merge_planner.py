"""Bounded candidate generation, policy requests, and deterministic local actuation."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict

from carla_agentic_toolkit.managed_policy import (
    Candidate,
    DecisionContext,
    PolicyDecision,
    PolicyRequest,
)
from carla_agentic_toolkit.merge_maneuver import lateral_target, transition
from carla_agentic_toolkit.merge_models import (
    COMMITTED_PHASES,
    TERMINAL_PHASES,
    ActorObservation,
    LaneGeometry,
    LocalControl,
    ManeuverState,
    MergeObservation,
    PlannerSettings,
)
from carla_agentic_toolkit.merge_safety import gap_evidence, merge_safe, observation_error

PLANNER_VERSION = "bounded-merge-planner-v1"
TRACKER_VERSION = "bounded-lateral-speed-tracker-v1"

__all__ = [
    "COMMITTED_PHASES",
    "TERMINAL_PHASES",
    "ActorObservation",
    "LaneGeometry",
    "LocalControl",
    "ManeuverState",
    "MergeObservation",
    "PlannerSettings",
    "RulesPolicy",
    "build_policy_request",
    "fallback_choice",
    "gap_evidence",
    "lateral_target",
    "rules_decision",
    "tracking_control",
    "transition",
    "valid_candidates",
]


def fallback_choice(phase: str) -> str:
    """Defer before commitment and continue an already committed maneuver."""
    return "continue" if phase in COMMITTED_PHASES else "defer"


def valid_candidates(
    value: MergeObservation, state: ManeuverState, settings: PlannerSettings
) -> tuple[Candidate, ...]:
    """Offer only bounded actions valid for the current phase and geometry."""
    if state.phase in TERMINAL_PHASES or observation_error(value, state) is not None:
        return ()
    if state.phase in COMMITTED_PHASES:
        return (_candidate("continue", value, settings), _candidate("abort", value, settings))
    return _precommit_candidates(value, state, settings)


def _precommit_candidates(
    value: MergeObservation, state: ManeuverState, settings: PlannerSettings
) -> tuple[Candidate, ...]:
    candidates = (_candidate("defer", value, settings),)
    if state.phase == "preparing" and merge_safe(value, settings):
        return (*candidates, _candidate("merge", value, settings))
    return candidates


def _candidate(choice: str, value: MergeObservation, settings: PlannerSettings) -> Candidate:
    lane_id = value.lane.source_lane_id if choice == "defer" else value.lane.target_lane_id
    return Candidate(
        candidate_id=choice,
        target_lane_id=lane_id,
        target_speed_mps=0.0 if choice == "abort" else settings.target_speed_mps,
        horizon_seconds=settings.crossing_seconds,
        expires_frame=value.frame + settings.expiry_frames,
    )


def tracking_control(
    actor: ActorObservation,
    *,
    target_speed_mps: float,
    target_lateral_m: float,
    heading_gain: float = 0.9,
) -> LocalControl:
    """Track a numerical speed/lateral target with bounded proportional actuation."""
    speed_error = target_speed_mps - actor.speed_mps
    heading = math.radians(actor.yaw_error_degrees)
    steer = (target_lateral_m - actor.lateral_m) * 0.18 - heading * heading_gain
    return LocalControl(
        throttle=min(max(speed_error * 0.35, 0.0), 0.65),
        brake=min(max(-speed_error * 0.5, 0.0), 1.0),
        steer=min(max(steer, -0.7), 0.7),
    )


def build_policy_request(
    value: MergeObservation,
    state: ManeuverState,
    settings: PlannerSettings,
    *,
    revision: int,
    deadline_monotonic: float,
) -> PolicyRequest | None:
    """Freeze identity and evidence only for a nonempty candidate set."""
    candidates = valid_candidates(value, state, settings)
    if not candidates:
        return None
    candidate_set_id = hashlib.sha256(
        json.dumps([_candidate_identity(item) for item in candidates], sort_keys=True).encode()
    ).hexdigest()[:24]
    context = DecisionContext(
        run_id=value.run_id,
        world_generation=value.world_generation,
        actor_id=value.policy.actor_id,
        revision=revision,
        observation_frame=value.frame,
        candidate_set_id=candidate_set_id,
        maneuver_generation=state.maneuver_generation,
        phase=state.phase,
        deadline_monotonic=deadline_monotonic,
    )
    evidence = {
        **value.to_dict(),
        "gaps": gap_evidence(value, settings),
        "planner_version": PLANNER_VERSION,
    }
    return PolicyRequest(context, candidates, json.dumps(evidence, sort_keys=True, allow_nan=False))


def _candidate_identity(candidate: Candidate) -> dict[str, object]:
    """Keep identity stable across frames only while numerical actions remain valid."""
    return {key: value for key, value in asdict(candidate).items() if key != "expires_frame"}


def rules_decision(request: PolicyRequest) -> PolicyDecision:
    """Select a legal merge when offered, otherwise the phase's deterministic fallback."""
    choices = {candidate.candidate_id for candidate in request.candidates}
    choice = "merge" if "merge" in choices else fallback_choice(request.context.phase)
    return PolicyDecision(
        request.context, choice, "rules", "Reviewed numerical gap and lane rules."
    )


class RulesPolicy:
    """No-key asynchronous interface shared with optional provider adapters."""

    async def choose(self, request: PolicyRequest, fallback_id: str) -> PolicyDecision:
        """Return the deterministic decision without networking or tick ownership."""
        del fallback_id
        return rules_decision(request)
