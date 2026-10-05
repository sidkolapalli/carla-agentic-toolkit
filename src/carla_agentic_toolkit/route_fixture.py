"""A reviewed Town10 UE5 route with a real junction turn and owned scripted traffic."""

from __future__ import annotations

import math
from importlib import import_module
from typing import Any, cast

from carla_agentic_toolkit.errors import UnsupportedFeatureError
from carla_agentic_toolkit.merge_fixture import select_corridor, yaw_difference
from carla_agentic_toolkit.route_geometry import RoutePath, RoutePoint

FIXTURE_VERSION = "town10-route-ue5-v1"
VEHICLE_BLUEPRINT = "vehicle.lincoln.mkz"
WALKER_BLUEPRINT = "walker.pedestrian.0015"
GOAL_DISTANCE_M = 135.0
TURN_ROAD_ID = 24
MIN_LANE_WIDTH_M = 2.7
MAX_SEGMENT_M = 3.0
MAX_SEGMENT_TURN_DEGREES = 30.0
MIN_ROUTE_TURN_DEGREES = 70.0
MAX_ROUTE_TURN_DEGREES = 110.0


def select_route(world_map: object) -> RoutePath:
    """Follow legal driving successors through reviewed road 24 at the first branch."""
    corridor = select_corridor(world_map, fixture_version="town10-merge-ue5-v1")
    carla = cast("Any", import_module("carla"))
    start = corridor.policy_start
    waypoint = cast("Any", world_map).get_waypoint(carla.Location(x=start.x, y=start.y, z=start.z))
    points = [_point(waypoint)]
    for _ in range(81):
        waypoint = _successor(waypoint)
        point = _point(waypoint)
        _verify_segment(points[-1], point)
        points.append(point)
    path = RoutePath(tuple(points))
    _verify_turn(path)
    return path


def _successor(waypoint: object) -> object:
    options = list(cast("Any", waypoint).next(2.0))
    if len(options) == 1:
        return options[0]
    reviewed = [item for item in options if _reviewed_branch(item)]
    if len(reviewed) != 1:
        message = "Route fixture encountered an unreviewed junction or missing successor."
        raise UnsupportedFeatureError(message)
    return reviewed[0]


def _point(waypoint: object) -> RoutePoint:
    value = cast("Any", waypoint)
    if (
        str(value.lane_type).split(".")[-1] != "Driving"
        or float(value.lane_width) < MIN_LANE_WIDTH_M
    ):
        message = "Route fixture requires driving lanes at least 2.7m wide."
        raise UnsupportedFeatureError(message)
    pose = value.transform
    return RoutePoint(
        float(pose.location.x),
        float(pose.location.y),
        float(pose.location.z),
        float(pose.rotation.yaw),
        int(value.lane_id),
        float(value.lane_width),
        int(value.road_id),
    )


def _verify_segment(first: RoutePoint, second: RoutePoint) -> None:
    distance = math.hypot(second.x - first.x, second.y - first.y)
    if (
        not 1.0 <= distance <= MAX_SEGMENT_M
        or abs(yaw_difference(second.yaw_degrees, first.yaw_degrees)) > MAX_SEGMENT_TURN_DEGREES
    ):
        message = "Route fixture has a discontinuous or excessively sharp successor."
        raise UnsupportedFeatureError(message)


def _verify_turn(path: RoutePath) -> None:
    turn = abs(yaw_difference(path.sample(GOAL_DISTANCE_M).yaw_degrees, path.points[0].yaw_degrees))
    if (
        path.length_m < GOAL_DISTANCE_M + 20.0
        or not MIN_ROUTE_TURN_DEGREES <= turn <= MAX_ROUTE_TURN_DEGREES
    ):
        message = "Route fixture does not match the reviewed 135m Town10 route and turn."
        raise UnsupportedFeatureError(message)


def _reviewed_branch(waypoint: object) -> bool:
    value = cast("Any", waypoint)
    return int(value.road_id) == TURN_ROAD_ID and int(value.lane_id) == 1
