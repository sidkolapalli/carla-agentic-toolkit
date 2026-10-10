"""Frame alignment, corridor validity, and numerical target-lane gap checks."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from carla_agentic_toolkit.merge_models import (
        ActorObservation,
        LaneGeometry,
        ManeuverState,
        MergeObservation,
        PlannerSettings,
    )

MINIMUM_REMAINING_M = 10.0
MINIMUM_LANE_WIDTH_M = 2.7


def _actor_finite(actor: ActorObservation) -> bool:
    return all(
        math.isfinite(value)
        for value in (
            actor.longitudinal_m,
            actor.lateral_m,
            actor.speed_mps,
            actor.yaw_error_degrees,
            actor.length_m,
            actor.width_m,
            *actor.position_m,
            *actor.velocity_mps,
        )
    )


def observation_error(value: MergeObservation, state: ManeuverState) -> str | None:
    """Reject stale, misaligned, or unsafe numerical observations before planning."""
    if value.frame <= state.last_frame:
        return "stale_observation"
    return _actor_observation_error(value) or _scene_error(value)


def _actor_observation_error(value: MergeObservation) -> str | None:
    actors = (value.policy, value.ego, *value.neighbors)
    if any(actor.frame != value.frame for actor in actors):
        return "misaligned_observation"
    if not all(_actor_finite(actor) for actor in actors):
        return "nonfinite_observation"
    return None


def _scene_error(value: MergeObservation) -> str | None:
    if value.collision:
        return "collision"
    if not _lane_valid(value.lane):
        return "corridor_invalid"
    if abs(value.policy.lateral_m) > value.lane.width_m * 2:
        return "corridor_departure"
    return None


def _lane_valid(lane: LaneGeometry) -> bool:
    return all(
        (
            lane.adjacent,
            lane.same_direction,
            lane.lane_change_allowed,
            math.isfinite(lane.remaining_m),
            lane.remaining_m > MINIMUM_REMAINING_M,
            math.isfinite(lane.width_m),
            lane.width_m >= MINIMUM_LANE_WIDTH_M,
            math.isfinite(lane.target_offset_m),
        )
    )


def gap_evidence(
    value: MergeObservation, settings: PlannerSettings
) -> tuple[dict[str, object], ...]:
    """Measure bumper gaps/TTC under constant-speed longitudinal extrapolation."""
    target_actors = tuple(
        actor for actor in value.neighbors if actor.lane_id == value.lane.target_lane_id
    )
    return tuple(_actor_gap(value.policy, actor, settings) for actor in target_actors)


def _actor_gap(
    policy: ActorObservation, neighbor: ActorObservation, settings: PlannerSettings
) -> dict[str, object]:
    policy_along, policy_radius = _longitudinal_box(policy)
    neighbor_along, neighbor_radius = _longitudinal_box(neighbor)
    ahead = neighbor_along >= policy_along
    gap = abs(neighbor_along - policy_along) - policy_radius - neighbor_radius
    closing_speed = (
        policy.speed_mps - neighbor.speed_mps if ahead else neighbor.speed_mps - policy.speed_mps
    )
    follower_speed = policy.speed_mps if ahead else neighbor.speed_mps
    ttc = max(gap, 0.0) / closing_speed if closing_speed > 0 else None
    required = max(settings.minimum_gap_m, follower_speed * settings.following_headway_seconds)
    return {
        "actor_id": neighbor.actor_id,
        "position": "front" if ahead else "rear",
        "gap_m": gap,
        "closing_speed_mps": closing_speed,
        "ttc_seconds": ttc,
        "required_gap_m": required,
        "safe": _gap_safe(gap, required, ttc, settings),
    }


def _longitudinal_box(actor: ActorObservation) -> tuple[float, float]:
    if actor.box is None:
        return actor.longitudinal_m, actor.length_m / 2
    return actor.box.longitudinal_m, actor.box.longitudinal_radius_m


def _gap_safe(gap: float, required: float, ttc: float | None, settings: PlannerSettings) -> bool:
    return gap >= required and (ttc is None or ttc >= settings.minimum_ttc_seconds)


def merge_safe(value: MergeObservation, settings: PlannerSettings) -> bool:
    """Require enough corridor and valid front/rear target-lane gaps."""
    needed = settings.target_speed_mps * settings.maneuver_timeout_seconds
    return value.lane.remaining_m >= needed and all(
        item["safe"] for item in gap_evidence(value, settings)
    )
