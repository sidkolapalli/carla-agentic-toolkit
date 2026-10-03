"""Corridor reachability checks are local to the versioned Town10 merge fixture."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from carla_agentic_toolkit.errors import UnsupportedFeatureError
from carla_agentic_toolkit.merge_fixture import select_corridor

TARGET_LANE_ID = -2


@dataclass
class Waypoint:
    """Straight finite driving-lane test fixture."""

    s: float = 0.0
    lane_id: int = -1
    end: float = 90.0
    heading: float = 0.0
    lane_change: str = "Right"
    is_junction: bool = False
    road_id: int = 1
    section_id: int = 0
    lane_width: float = 3.5
    lane_type: str = "Driving"

    @property
    def transform(self) -> SimpleNamespace:
        """Return a position in the same-direction paired corridor."""
        return SimpleNamespace(
            location=SimpleNamespace(x=self.s, y=(-self.lane_id - 1) * 3.5, z=0.0),
            rotation=SimpleNamespace(yaw=self.heading),
        )

    @property
    def right_lane_marking(self) -> SimpleNamespace:
        """Expose the actual crossing marking."""
        return SimpleNamespace(type="Broken", lane_change=self.lane_change)

    @property
    def left_lane_marking(self) -> SimpleNamespace:
        """Expose the target boundary marking."""
        return SimpleNamespace(type="Broken", lane_change="Left")

    def get_right_lane(self) -> Waypoint | None:
        """Return the one adjacent driving lane."""
        if self.lane_id == TARGET_LANE_ID:
            return None
        return Waypoint(self.s, lane_id=-2, end=self.end)

    def next(self, distance: float) -> list[Waypoint]:
        """End the unique route at its explicit finite boundary."""
        if self.s + distance > self.end:
            return []
        return [Waypoint(self.s + distance, self.lane_id, self.end, self.heading, self.lane_change)]


@dataclass
class Map:
    """Map with one candidate corridor, suitable for deterministic topology tests."""

    start: Waypoint
    name: str = "/Game/Carla/Maps/Town10HD_Opt"

    def generate_waypoints(self, _distance: float) -> list[Waypoint]:
        """Return the start candidate."""
        return [self.start]


def test_verified_corridor_records_initial_poses_and_reachability() -> None:
    """Both actors get explicit poses on a verified same-direction route."""
    corridor = select_corridor(Map(Waypoint()))
    assert {
        "source_lane": corridor.source_lane_id,
        "target_lane": corridor.target_lane_id,
        "source_start": (corridor.policy_start.x, corridor.policy_start.y),
        "ego_start": (corridor.ego_start.x, corridor.ego_start.y),
        "length": corridor.length_m,
    } == {
        "source_lane": -1,
        "target_lane": -2,
        "source_start": (0.0, 0.0),
        "ego_start": (24.0, 3.5),
        "length": 50.0,
    }


@pytest.mark.parametrize(
    "start", [Waypoint(end=20.0), Waypoint(lane_change="None"), Waypoint(is_junction=True)]
)
def test_unreachable_or_illegal_corridors_fail_closed(start: Waypoint) -> None:
    """A short route, forbidden crossing, or junction is not a merge fixture."""
    with pytest.raises(UnsupportedFeatureError, match="corridor"):
        select_corridor(Map(start))


def test_fixture_does_not_claim_arbitrary_map_support() -> None:
    """A route helper cannot turn another map into the reviewed fixture."""
    with pytest.raises(UnsupportedFeatureError, match="Town10"):
        select_corridor(Map(Waypoint(), name="Town01"))
