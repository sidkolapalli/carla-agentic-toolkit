"""Polyline geometry and a bounded local pure-pursuit tracker; no model or CARLA access."""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING

from carla_agentic_toolkit.merge_models import LocalControl

if TYPE_CHECKING:
    from carla_agentic_toolkit.route_models import RouteActor

TRACKER_VERSION = "route-pure-pursuit-v1"
MIN_POINTS = 2
MAX_POINTS = 256
MIN_SEGMENT_M = 0.01
EFFECTIVE_STEER_DEGREES = 35.0


@dataclass(frozen=True, slots=True)
class RoutePoint:
    """One sampled, driving-direction lane centre in world metres."""

    x: float
    y: float
    z: float = 0.0
    yaw_degrees: float = 0.0
    lane_id: int = -1
    width_m: float = 3.5
    road_id: int = 0


@dataclass(frozen=True, slots=True)
class Projection:
    """Closest position on the polyline, with signed left-to-right lateral error."""

    progress_m: float
    lateral_m: float
    distance_m: float
    segment: int


class RoutePath:
    """A bounded, nonlooping polyline sampled from CARLA's lane successors."""

    def __init__(self, points: tuple[RoutePoint, ...]) -> None:
        """Reject missing or degenerate geometry before any actuation."""
        if not MIN_POINTS <= len(points) <= MAX_POINTS:
            message = "A route requires between 2 and 256 points."
            raise ValueError(message)
        self.points = points
        self.distances = [0.0]
        for start, end in pairwise(points):
            distance = math.hypot(end.x - start.x, end.y - start.y)
            if not math.isfinite(distance) or distance < MIN_SEGMENT_M:
                message = "Route segments must have finite, nonzero length."
                raise ValueError(message)
            self.distances.append(self.distances[-1] + distance)
        self.length_m = self.distances[-1]

    def project(self, x: float, y: float) -> Projection:
        """Measure actual travelled position without assuming a straight road."""
        return min(
            (self._project_segment(x, y, index) for index in range(len(self.points) - 1)),
            key=lambda point: point.distance_m,
        )

    def _project_segment(self, x: float, y: float, index: int) -> Projection:
        start, end = self.points[index : index + 2]
        dx, dy = end.x - start.x, end.y - start.y
        length = self.distances[index + 1] - self.distances[index]
        along = min(max(((x - start.x) * dx + (y - start.y) * dy) / length, 0.0), length)
        px, py = start.x + dx * along / length, start.y + dy * along / length
        lateral = (-(x - px) * dy + (y - py) * dx) / length
        return Projection(self.distances[index] + along, lateral, math.hypot(x - px, y - py), index)

    def sample(self, progress_m: float, lateral_m: float = 0.0) -> RoutePoint:
        """Interpolate a route target and optionally shift it sideways in metres."""
        progress = min(max(progress_m, 0.0), self.length_m)
        index = next(
            (i for i in range(len(self.points) - 1) if self.distances[i + 1] >= progress),
            len(self.points) - 2,
        )
        start, end = self.points[index : index + 2]
        fraction = (progress - self.distances[index]) / (
            self.distances[index + 1] - self.distances[index]
        )
        yaw = math.atan2(end.y - start.y, end.x - start.x)
        return RoutePoint(
            start.x + fraction * (end.x - start.x) - math.sin(yaw) * lateral_m,
            start.y + fraction * (end.y - start.y) + math.cos(yaw) * lateral_m,
            start.z + fraction * (end.z - start.z),
            math.degrees(yaw),
            start.lane_id,
            start.width_m,
            start.road_id,
        )


def tracking_control(
    path: RoutePath, actor: RouteActor, *, target_speed_mps: float, lateral_m: float = 0.0
) -> LocalControl:
    """Steer locally with a 2.85m wheelbase; Jev selects only a target-speed candidate."""
    progress = path.project(actor.x, actor.y).progress_m
    target = path.sample(progress + 5.0 + actor.speed_mps * 0.5, lateral_m)
    dx, dy = target.x - actor.x, target.y - actor.y
    yaw = math.radians(actor.yaw_degrees)
    lateral = -math.sin(yaw) * dx + math.cos(yaw) * dy
    angle = math.atan2(2.0 * 2.85 * lateral, max(dx * dx + dy * dy, 0.01))
    error = target_speed_mps - actor.speed_mps
    return LocalControl(
        min(max(error * 0.4, 0.0), 0.65),
        min(max(-error * 0.5, 0.0), 1.0),
        min(max(angle / math.radians(EFFECTIVE_STEER_DEGREES), -0.7), 0.7),
    )
