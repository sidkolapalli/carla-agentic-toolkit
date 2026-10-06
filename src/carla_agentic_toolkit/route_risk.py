"""Transparent 2D traffic estimates and a per-frame emergency guard, not a safety proof."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from carla_agentic_toolkit.route_models import TrafficEvidence

EMERGENCY_HORIZON_SECONDS = 1.1

if TYPE_CHECKING:
    from carla_agentic_toolkit.route_models import RouteActor, RouteObservation


def _axes(actor: RouteActor) -> tuple[tuple[float, float], tuple[float, float]]:
    yaw = math.radians(actor.yaw_degrees)
    return (math.cos(yaw), math.sin(yaw)), (-math.sin(yaw), math.cos(yaw))


def _radius(actor: RouteActor, axis: tuple[float, float], padding: float) -> float:
    forward, side = _axes(actor)
    return abs(forward[0] * axis[0] + forward[1] * axis[1]) * (actor.length_m / 2 + padding) + abs(
        side[0] * axis[0] + side[1] * axis[1]
    ) * (actor.width_m / 2 + padding)


def box_separation(
    first: RouteActor, second: RouteActor, *, seconds: float = 0.0, padding: float = 0.0
) -> float:
    """Maximum separating-axis gap: a clearance lower bound; <=0 means box overlap."""
    dx = second.x - first.x + (second.vx - first.vx) * seconds
    dy = second.y - first.y + (second.vy - first.vy) * seconds
    return max(
        abs(dx * axis[0] + dy * axis[1])
        - _radius(first, axis, padding)
        - _radius(second, axis, padding)
        for axis in (*_axes(first), *_axes(second))
    )


def _overlap_time(first: RouteActor, second: RouteActor) -> float | None:
    return next(
        (
            step / 10
            for step in range(41)
            if box_separation(first, second, seconds=step / 10, padding=0.2) <= 0
        ),
        None,
    )


def _ahead_gap(first: RouteActor, second: RouteActor) -> float | None:
    lateral_overlap = (
        abs(second.lateral_m - first.lateral_m) < (first.width_m + second.width_m) / 2 + 0.25
    )
    if not lateral_overlap or second.progress_m < first.progress_m:
        return None
    return max(0.0, second.progress_m - first.progress_m - (first.length_m + second.length_m) / 2)


def traffic_evidence(
    policy: RouteActor, neighbors: tuple[RouteActor, ...]
) -> tuple[TrafficEvidence, ...]:
    """Evaluate visible vehicles and walkers using their frozen current kinematics only."""
    return tuple(
        TrafficEvidence(
            actor,
            max(0.0, box_separation(policy, actor)),
            _overlap_time(policy, actor),
            _ahead_gap(policy, actor),
        )
        for actor in neighbors
    )


def _imminent(risk: TrafficEvidence, speed_mps: float) -> bool:
    if (
        risk.predicted_overlap_seconds is not None
        and risk.predicted_overlap_seconds <= EMERGENCY_HORIZON_SECONDS
    ):
        return True
    stopping_m = 1.5 + speed_mps * speed_mps / (2 * 5.0)
    return risk.ahead_gap_m is not None and risk.ahead_gap_m < stopping_m


def emergency_reason(value: RouteObservation) -> str | None:
    """Re-evaluate after every tick, independently of whether inference has returned."""
    if value.traffic_light in {"Red", "Yellow"}:
        return "traffic_light_stop"
    if any(_imminent(risk, value.policy.speed_mps) for risk in value.traffic):
        return "imminent_obstacle"
    return None
