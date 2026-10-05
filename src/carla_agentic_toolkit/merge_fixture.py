"""Deterministic, verified straight corridor for the Town10 merge fixture."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from typing import Any, cast

from carla_agentic_toolkit.errors import UnsupportedFeatureError

FIXTURE_VERSION = "town10-merge-v1"
FIXTURE_VEHICLES = {
    FIXTURE_VERSION: "vehicle.tesla.model3",
    "town10-merge-ue5-v1": "vehicle.lincoln.mkz",
}
CORRIDOR_LENGTH_M = 50.0
CORRIDOR_SAMPLE_M = 2.0
EGO_INITIAL_LEAD_M = 24.0
MINIMUM_LANE_WIDTH_M = 2.7
MAXIMUM_HEADING_CHANGE_DEGREES = 2.0
MAXIMUM_CORRIDOR_DEVIATION_M = 0.5


@dataclass(frozen=True, slots=True)
class Pose:
    """Versioned initial/world route coordinates in metres and degrees."""

    x: float
    y: float
    z: float
    yaw: float


@dataclass(frozen=True, slots=True)
class MergeCorridor:
    """Two adjacent same-direction lanes proven reachable over a finite corridor."""

    policy_start: Pose
    ego_start: Pose
    target_start: Pose
    road_id: int
    source_lane_id: int
    target_lane_id: int
    width_m: float
    source_marking: str
    target_marking: str
    length_m: float = CORRIDOR_LENGTH_M
    fixture_version: str = FIXTURE_VERSION

    def project(self, x: float, y: float) -> tuple[float, float]:
        """Project a point into this verified straight corridor's local coordinates."""
        yaw = math.radians(self.policy_start.yaw)
        dx, dy = x - self.policy_start.x, y - self.policy_start.y
        return dx * math.cos(yaw) + dy * math.sin(yaw), -dx * math.sin(yaw) + dy * math.cos(yaw)

    @property
    def target_offset_m(self) -> float:
        """Return the signed adjacent-lane centre displacement."""
        return self.project(self.target_start.x, self.target_start.y)[1]

    def to_dict(self) -> dict[str, object]:
        """Record exact fixture poses and bounded reachability assumptions."""
        return {
            **asdict(self),
            "reachability": "same-road, same-lane, unique nonjunction successors at 2m",
        }


def select_corridor(world_map: object, *, fixture_version: str = FIXTURE_VERSION) -> MergeCorridor:
    """Select the first deterministic corridor satisfying the reviewed fixture."""
    if fixture_version not in FIXTURE_VEHICLES:
        msg = f"Unsupported merge fixture: {fixture_version}."
        raise UnsupportedFeatureError(msg)
    runtime_map = cast("Any", world_map)
    map_name = str(runtime_map.name).rsplit("/", maxsplit=1)[-1]
    if map_name not in {"Town10HD", "Town10HD_Opt"}:
        msg = f"{fixture_version} requires Town10HD or Town10HD_Opt, received {map_name}."
        raise UnsupportedFeatureError(msg)
    starts = sorted(runtime_map.generate_waypoints(CORRIDOR_SAMPLE_M), key=_waypoint_key)
    for start in starts[:4096]:
        corridor = _candidate_corridor(start)
        if corridor is not None:
            return replace(corridor, fixture_version=fixture_version)
    msg = "No legal, straight, same-direction 50m merge corridor was found in this Town10 map."
    raise UnsupportedFeatureError(msg)


def _waypoint_key(waypoint: object) -> tuple[int, int, int, float]:
    value = cast("Any", waypoint)
    return int(value.road_id), int(value.section_id), int(value.lane_id), float(value.s)


def _candidate_corridor(start: object) -> MergeCorridor | None:
    source = cast("Any", start)
    target = source.get_right_lane()
    if not _lane_pair_valid(source, target):
        return None
    source_route, target_route = _verified_route(source), _verified_route(target)
    if not source_route or not target_route:
        return None
    return MergeCorridor(
        policy_start=source_route[0],
        ego_start=target_route[int(EGO_INITIAL_LEAD_M / CORRIDOR_SAMPLE_M)],
        target_start=target_route[0],
        road_id=int(source.road_id),
        source_lane_id=int(source.lane_id),
        target_lane_id=int(target.lane_id),
        width_m=float(target.lane_width),
        source_marking=str(source.right_lane_marking.type),
        target_marking=str(target.left_lane_marking.type),
    )


def _lane_pair_valid(source: object, target: object | None) -> bool:
    if target is None:
        return False
    left, right = cast("Any", source), cast("Any", target)
    permission = str(left.lane_change).split(".")[-1] in {"Right", "Both"}
    return all(
        (
            _driving(left),
            _driving(right),
            permission,
            left.road_id == right.road_id,
            left.lane_id * right.lane_id > 0,
            float(right.lane_width) >= MINIMUM_LANE_WIDTH_M,
            abs(
                yaw_difference(
                    float(left.transform.rotation.yaw), float(right.transform.rotation.yaw)
                )
            )
            <= MAXIMUM_HEADING_CHANGE_DEGREES,
        )
    )


def _driving(waypoint: object) -> bool:
    value = cast("Any", waypoint)
    return str(value.lane_type).split(".")[-1] == "Driving" and not value.is_junction


def _verified_route(start: object) -> tuple[Pose, ...]:
    current = cast("Any", start)
    points: list[Pose] = []
    for _index in range(int(CORRIDOR_LENGTH_M / CORRIDOR_SAMPLE_M) + 1):
        if not _route_point_valid(current, start):
            return ()
        points.append(_pose(current))
        successors = _matching_successors(current)
        if len(successors) != 1:
            return ()
        current = successors[0]
    return tuple(points)


def _matching_successors(waypoint: object) -> list[object]:
    value = cast("Any", waypoint)
    return [
        item
        for item in value.next(CORRIDOR_SAMPLE_M)
        if (item.road_id, item.lane_id) == (value.road_id, value.lane_id)
    ]


def _route_point_valid(current: object, start: object) -> bool:
    if current is None:
        return False
    pose, initial = _pose(current), _pose(start)
    yaw = math.radians(initial.yaw)
    lateral = -(pose.x - initial.x) * math.sin(yaw) + (pose.y - initial.y) * math.cos(yaw)
    return all(
        (
            _driving(current),
            abs(yaw_difference(pose.yaw, initial.yaw)) <= MAXIMUM_HEADING_CHANGE_DEGREES,
            abs(lateral) <= MAXIMUM_CORRIDOR_DEVIATION_M,
        )
    )


def _pose(waypoint: object) -> Pose:
    transform = cast("Any", waypoint).transform
    location = transform.location
    return Pose(
        float(location.x), float(location.y), float(location.z), float(transform.rotation.yaw)
    )


def yaw_difference(yaw: float, reference: float) -> float:
    """Return the shortest signed angle in degrees."""
    return (yaw - reference + 180.0) % 360.0 - 180.0
