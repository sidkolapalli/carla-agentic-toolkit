"""Semantic-tag and rich landmark query behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import experiment_navigation
from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.carla_protocols import CarlaWorld

LANDMARK_ID = "speed-30"


@dataclass
class LandmarkAdapter:
    """Facade-level landmark adapter fake."""

    def get_landmarks(
        self,
        *,
        max_count: int,
        landmark_type: str | None,
        landmark_id: str | None,
    ) -> dict[str, object]:
        """Return the selected filter values."""
        return {
            "max_count": max_count,
            "landmark_type": landmark_type,
            "landmark_id": landmark_id,
        }


def test_facade_exposes_landmark_filters() -> None:
    """Scripts should discover and call type-filtered landmark queries."""
    api = CarlaScriptApi(cast("PythonCarlaAdapter", LandmarkAdapter()), RunSnapshots())

    result = api.get_landmarks(10, landmark_type="274")
    methods = cast("dict[str, object]", api.describe_api()["methods"])

    assert result == {"max_count": 10, "landmark_type": "274", "landmark_id": None}
    assert "get_landmarks" in methods


@dataclass
class Vector:
    """CARLA vector fake."""

    x: float
    y: float
    z: float


@dataclass
class Rotation:
    """CARLA rotation fake."""

    pitch: float
    yaw: float
    roll: float


@dataclass
class Transform:
    """CARLA transform fake."""

    location: Vector = field(default_factory=lambda: Vector(1.0, 2.0, 3.0))
    rotation: Rotation = field(default_factory=lambda: Rotation(0.0, 90.0, 0.0))


@dataclass
class Landmark:
    """CARLA landmark fake with official scalar fields."""

    id: str = LANDMARK_ID
    name: str = "Speed"
    type: str = "274"
    sub_type: str = "30"
    road_id: int = 4
    distance: float = 8.5
    s: float = 10.0
    t: float = 1.0
    is_dynamic: bool = False
    orientation: str = "Positive"
    value: float = 30.0
    unit: str = "km/h"
    width: float = 0.5
    height: float = 0.5
    transform: Transform = field(default_factory=Transform)


@dataclass
class LandmarkMap:
    """Map fake exposing official landmark query variants."""

    calls: list[tuple[object, ...]] = field(default_factory=list)

    def get_all_landmarks(self) -> list[Landmark]:
        """Return all landmarks."""
        self.calls.append(("all",))
        return [Landmark(), Landmark(id="second")]

    def get_all_landmarks_of_type(self, landmark_type: str) -> list[Landmark]:
        """Return landmarks by OpenDRIVE type."""
        self.calls.append(("type", landmark_type))
        return [Landmark()]

    def get_all_landmarks_from_id(self, landmark_id: str) -> list[Landmark]:
        """Return landmarks by OpenDRIVE ID."""
        self.calls.append(("id", landmark_id))
        return [Landmark()]


@dataclass
class World:
    """World fake returning one map."""

    world_map: object

    def get_map(self) -> object:
        """Return the map."""
        return self.world_map


def test_runtime_filters_and_serializes_landmarks() -> None:
    """Official query variants should return bounded rich JSON metadata."""
    world_map = LandmarkMap()
    world = cast("CarlaWorld", World(world_map))

    all_result = experiment_navigation.landmarks(world, max_count=1)
    type_result = experiment_navigation.landmarks(world, max_count=10, landmark_type="274")
    id_result = experiment_navigation.landmarks(world, max_count=10, landmark_id=LANDMARK_ID)
    item = cast("list[dict[str, object]]", type_result["landmarks"])[0]

    assert {
        "truncated": all_result["truncated"],
        "type_filter": type_result["landmark_type"],
        "id_filter": id_result["landmark_id"],
        "sub_type": item["sub_type"],
        "value": item["value"],
        "calls": world_map.calls,
    } == {
        "truncated": True,
        "type_filter": "274",
        "id_filter": LANDMARK_ID,
        "sub_type": "30",
        "value": 30.0,
        "calls": [("all",), ("type", "274"), ("id", LANDMARK_ID)],
    }


def test_landmark_queries_validate_bounds_and_capabilities() -> None:
    """Invalid filters and absent methods should fail before returning guessed data."""
    with pytest.raises(CarlaAdapterError, match="mutually exclusive"):
        experiment_navigation.landmarks(
            cast("CarlaWorld", World(LandmarkMap())),
            max_count=10,
            landmark_type="274",
            landmark_id=LANDMARK_ID,
        )
    with pytest.raises(CarlaAdapterError, match=r"1\.\.1000"):
        experiment_navigation.landmarks(cast("CarlaWorld", World(LandmarkMap())), max_count=0)
    with pytest.raises(UnsupportedFeatureError, match="get_all_landmarks"):
        experiment_navigation.landmarks(cast("CarlaWorld", World(object())), max_count=10)
