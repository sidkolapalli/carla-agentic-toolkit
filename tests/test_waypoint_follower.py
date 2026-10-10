"""Greedy waypoint following must report progress without claiming path planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import experiment_navigation
from carla_agentic_toolkit.models import Location
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_script_api import ScriptAdapter

if TYPE_CHECKING:
    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.carla_protocols import CarlaWorld


@dataclass
class Waypoint:
    """Road node with explicit successors; no hidden topology search."""

    location: Location
    successors: list[Waypoint] = field(default_factory=list)
    steps: list[float] = field(default_factory=list)
    road_id: int = 1
    section_id: int = 0
    lane_id: int = -1
    s: float = 0.0
    lane_type: str = "Driving"
    is_junction: bool = False

    @property
    def transform(self) -> SimpleNamespace:
        """Return native-shaped pose for serialization."""
        return SimpleNamespace(
            location=self.location, rotation=SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0)
        )

    def next(self, step: float) -> list[Waypoint]:
        """Record successor queries without mutating the road graph."""
        self.steps.append(step)
        return self.successors


@dataclass
class RoadMap:
    """Return the graph's starting driving waypoint."""

    start: Waypoint

    def get_waypoint(self, _location: Location) -> Waypoint:
        """Project the requested origin to the supplied starting node."""
        return self.start


@dataclass
class World:
    """Map-only native fake; a route query never advances the world."""

    start: Waypoint

    def get_map(self) -> RoadMap:
        """Expose explicit waypoint links."""
        return RoadMap(self.start)


def _route(
    monkeypatch: pytest.MonkeyPatch,
    start: Waypoint,
    end: Location,
    *,
    max_steps: int = 10,
) -> dict[str, object]:
    monkeypatch.setattr(
        experiment_navigation, "import_module", lambda _name: SimpleNamespace(Location=Location)
    )
    return experiment_navigation.route(
        cast("CarlaWorld", World(start)),
        start=start.location,
        end=end,
        step_meters=1.0,
        max_steps=max_steps,
    )


def test_nearest_branch_leads_away_and_reports_unreached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reachable alternative does not turn this greedy traversal into a planner."""
    goal = Waypoint(Location(10.0, 0.0, 0.0))
    final = Waypoint(Location(-4.0, 0.0, 0.0))
    wrong = Waypoint(Location(4.0, 0.0, 0.0), [final])
    alternative = Waypoint(Location(0.0, 4.0, 0.0), [goal])
    start = Waypoint(Location(0.0, 0.0, 0.0), [wrong, alternative], is_junction=True)

    result = _route(monkeypatch, start, goal.location)

    assert result["reached_destination"] is False
    assert result["remaining_distance_m"] == pytest.approx(14.0)
    assert result["waypoint_count"] == len([start, wrong, final])
    assert alternative.steps == []


@pytest.mark.parametrize("max_steps", [1, 2])
def test_step_budget_reports_remaining_distance(
    monkeypatch: pytest.MonkeyPatch, max_steps: int
) -> None:
    """Using the budget cannot masquerade as arrival at the distant end."""
    second = Waypoint(Location(2.0, 0.0, 0.0))
    start = Waypoint(Location(0.0, 0.0, 0.0), [second])
    result = _route(monkeypatch, start, Location(10.0, 0.0, 0.0), max_steps=max_steps)
    assert result["reached_destination"] is False
    assert result["remaining_distance_m"] == pytest.approx(10.0 if max_steps == 1 else 8.0)


def test_reached_destination_reports_actual_final_distance(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arrival means the last waypoint is within the declared step tolerance."""
    last = Waypoint(Location(9.5, 0.0, 0.0))
    start = Waypoint(Location(0.0, 0.0, 0.0), [last])
    result = _route(monkeypatch, start, Location(10.0, 0.0, 0.0))
    assert result["reached_destination"] is True
    assert result["remaining_distance_m"] == pytest.approx(0.5)
    assert last.steps == []


def test_already_reached_does_not_walk_away(monkeypatch: pytest.MonkeyPatch) -> None:
    """An origin already near the end must not follow its successor away."""
    start = Waypoint(Location(9.5, 0.0, 0.0), [Waypoint(Location(-4.0, 0.0, 0.0))])
    result = _route(monkeypatch, start, Location(10.0, 0.0, 0.0))
    assert result["waypoint_count"] == 1
    assert result["reached_destination"] is True
    assert start.steps == []


def test_remaining_distance_includes_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Distance is the native location's Euclidean separation, not only XY."""
    result = _route(monkeypatch, Waypoint(Location(10.0, 0.0, 0.0)), Location(10.0, 0.0, 3.0))
    assert result["reached_destination"] is False
    assert result["remaining_distance_m"] == pytest.approx(3.0)


def test_follow_waypoints_facade_preserves_parameters_and_route_snapshot() -> None:
    """The accurately named facade retains the established adapter/query contract."""
    adapter = ScriptAdapter()
    snapshots = RunSnapshots()
    api = CarlaScriptApi(cast("PythonCarlaAdapter", adapter), snapshots)
    result = api.follow_waypoints(
        {"x": 1.0, "y": 2.0, "z": 0.0},
        {"x": 8.0, "y": 2.0, "z": 0.0},
        step_meters=3.0,
        max_steps=20,
    )
    assert adapter.calls == [
        (
            "generate_route",
            {
                "start": {"x": 1.0, "y": 2.0, "z": 0.0},
                "end": {"x": 8.0, "y": 2.0, "z": 0.0},
                "step_meters": 3.0,
                "max_steps": 20,
            },
        )
    ]
    assert snapshots.read_snapshot("carla-snapshot://route/latest") == result


def test_discovery_describes_greedy_follower_and_deprecated_route_alias() -> None:
    """Discovery must not imply a topology-searching route planner."""
    api = CarlaScriptApi(cast("PythonCarlaAdapter", ScriptAdapter()), RunSnapshots())
    catalog = cast("dict[str, dict[str, str]]", api.describe_api()["methods"])
    assert "greedy" in catalog["follow_waypoints"]["doc"]
    assert "reached_destination" in catalog["follow_waypoints"]["doc"]
    assert "deprecated" in catalog["generate_route"]["doc"].lower()


def test_legacy_generate_route_keeps_adapter_and_snapshot_contract() -> None:
    """Existing callers still get the same traversal instead of a silent API removal."""
    adapter = ScriptAdapter()
    snapshots = RunSnapshots()
    api = CarlaScriptApi(cast("PythonCarlaAdapter", adapter), snapshots)
    result = api.generate_route({"x": 0.0, "y": 0.0, "z": 0.0}, {"x": 10.0, "y": 0.0, "z": 0.0})
    assert adapter.calls[0][0] == "generate_route"
    assert snapshots.read_snapshot("carla-snapshot://route/latest") == result
