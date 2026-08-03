"""Per-vehicle Traffic Manager tuning behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest

from carla_mcp import traffic_tuning
from carla_mcp.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_mcp.models import Location, TrafficVehiclePathRequest
from carla_mcp.script_api import CarlaScriptApi
from carla_mcp.snapshots import RunSnapshots

if TYPE_CHECKING:
    from carla_mcp.adapter import PythonCarlaAdapter

ACTOR_ID = 71
TM_PORT = 8000


@dataclass
class TrafficTuningAdapter:
    """Facade-level per-vehicle Traffic Manager fake."""

    calls: list[tuple[object, ...]] = field(default_factory=list)

    def tune_traffic_vehicle(
        self,
        *,
        actor_id: int,
        traffic_manager_port: int,
        settings: dict[str, object],
    ) -> dict[str, object]:
        """Record one settings update."""
        self.calls.append(("tune", actor_id, traffic_manager_port, settings))
        return {"actor_id": actor_id, "traffic_manager_port": traffic_manager_port, **settings}

    def set_traffic_vehicle_path(
        self,
        *,
        request: TrafficVehiclePathRequest,
    ) -> dict[str, object]:
        """Record one path or route update."""
        self.calls.append(("path", request))
        return {
            "actor_id": request.actor_id,
            "traffic_manager_port": request.traffic_manager_port,
            "path_count": len(request.path),
            "route": list(request.route),
            "empty_buffer": request.empty_buffer,
        }


def test_facade_exposes_vehicle_tuning_and_routes() -> None:
    """Scripts should tune a vehicle and upload a route with JSON inputs."""
    api = CarlaScriptApi(
        cast("PythonCarlaAdapter", TrafficTuningAdapter()),
        RunSnapshots(),
    )

    tuned = api.tune_traffic_vehicle(
        ACTOR_ID,
        {"desired_speed": 12.0, "ignore_lights_percentage": 10.0},
        traffic_manager_port=TM_PORT,
    )
    routed = api.set_traffic_vehicle_path(
        ACTOR_ID,
        {"route": ["Left", "Straight"], "traffic_manager_port": TM_PORT},
    )

    assert tuned == {
        "actor_id": ACTOR_ID,
        "traffic_manager_port": TM_PORT,
        "desired_speed": 12.0,
        "ignore_lights_percentage": 10.0,
    }
    assert routed == {
        "actor_id": ACTOR_ID,
        "traffic_manager_port": TM_PORT,
        "path_count": 0,
        "route": ["Left", "Straight"],
        "empty_buffer": True,
    }
    assert {"tune_traffic_vehicle", "set_traffic_vehicle_path"} <= set(
        cast("dict[str, object]", api.describe_api()["methods"])
    )


@dataclass
class Actor:
    """CARLA actor fake."""

    id: int = ACTOR_ID


@dataclass
class Actors:
    """Actor collection fake."""

    actor: Actor = field(default_factory=Actor)

    def find(self, actor_id: int) -> Actor | None:
        """Find one actor by ID."""
        return self.actor if actor_id == self.actor.id else None


@dataclass
class World:
    """World fake exposing actors."""

    actors: Actors = field(default_factory=Actors)

    def get_actors(self) -> Actors:
        """Return actors."""
        return self.actors


@dataclass
class TrafficManager:
    """Dynamic Traffic Manager fake."""

    calls: list[tuple[object, ...]] = field(default_factory=list)

    def auto_lane_change(self, actor: Actor, enabled: object) -> None:
        """Record automatic lane-change setting."""
        self.calls.append(("auto", actor.id, enabled))

    def force_lane_change(self, actor: Actor, left: object) -> None:
        """Record forced lane direction."""
        self.calls.append(("force", actor.id, left))

    def set_desired_speed(self, actor: Actor, speed: float) -> None:
        """Record desired speed."""
        self.calls.append(("speed", actor.id, speed))

    def distance_to_leading_vehicle(self, actor: Actor, distance: float) -> None:
        """Record following distance."""
        self.calls.append(("distance", actor.id, distance))

    def ignore_lights_percentage(self, actor: Actor, percentage: float) -> None:
        """Record light-ignore percentage."""
        self.calls.append(("lights", actor.id, percentage))

    def set_path(self, actor: Actor, path: list[object], empty_buffer: object) -> None:
        """Record a location path."""
        self.calls.append(("path", actor.id, path, empty_buffer))

    def set_route(self, actor: Actor, route: list[str], empty_buffer: object) -> None:
        """Record a road-option route."""
        self.calls.append(("route", actor.id, route, empty_buffer))


@dataclass
class PartialTrafficManager:
    """Manager exposing only the first of two requested methods."""

    calls: list[tuple[object, ...]] = field(default_factory=list)

    def set_desired_speed(self, actor: Actor, speed: float) -> None:
        """Record a desired-speed side effect."""
        self.calls.append((actor.id, speed))


@dataclass
class Client:
    """Client fake returning one Traffic Manager."""

    manager: object
    world: World = field(default_factory=World)

    def get_trafficmanager(self, port: int) -> object:
        """Return the manager on the expected port."""
        assert port == TM_PORT
        return self.manager

    def get_world(self) -> World:
        """Return the fake world."""
        return self.world


@dataclass
class CarlaLocation:
    """CARLA location factory fake."""

    x: float
    y: float
    z: float


def test_runtime_applies_supported_per_vehicle_settings() -> None:
    """Settings should map to official CARLA 0.9.16 Traffic Manager methods."""
    manager = TrafficManager()
    client = Client(manager)

    result = traffic_tuning.tune_traffic_vehicle(
        cast("object", client),
        actor_id=ACTOR_ID,
        traffic_manager_port=TM_PORT,
        settings={
            "auto_lane_change": False,
            "force_lane_change": "left",
            "desired_speed": 12.0,
            "distance_to_leading_vehicle": 3.0,
            "ignore_lights_percentage": 25.0,
        },
    )

    assert {
        "actor_id": result["actor_id"],
        "auto": ("auto", ACTOR_ID, False) in manager.calls,
        "force": ("force", ACTOR_ID, True) in manager.calls,
        "speed": ("speed", ACTOR_ID, 12.0) in manager.calls,
        "distance": ("distance", ACTOR_ID, 3.0) in manager.calls,
        "lights": ("lights", ACTOR_ID, 25.0) in manager.calls,
    } == {
        "actor_id": ACTOR_ID,
        "auto": True,
        "force": True,
        "speed": True,
        "distance": True,
        "lights": True,
    }


def test_runtime_uploads_path_and_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Paths and routes should use official set_path/set_route contracts."""
    manager = TrafficManager()
    client = Client(manager)
    monkeypatch.setattr(
        traffic_tuning,
        "import_module",
        lambda _name: type("Carla", (), {"Location": CarlaLocation}),
    )

    path_result = traffic_tuning.set_traffic_vehicle_path(
        cast("object", client),
        TrafficVehiclePathRequest(
            actor_id=ACTOR_ID,
            traffic_manager_port=TM_PORT,
            path=(Location(1.0, 2.0, 3.0),),
            route=(),
            empty_buffer=True,
        ),
    )
    route_result = traffic_tuning.set_traffic_vehicle_path(
        cast("object", client),
        TrafficVehiclePathRequest(
            actor_id=ACTOR_ID,
            traffic_manager_port=TM_PORT,
            path=(),
            route=("Left", "LaneFollow"),
            empty_buffer=False,
        ),
    )

    assert path_result["path_count"] == 1
    assert route_result["route"] == ["Left", "LaneFollow"]
    assert manager.calls[-1] == ("route", ACTOR_ID, ["Left", "LaneFollow"], False)


def test_invalid_tuning_and_missing_method_are_structured() -> None:
    """Bad values and absent runtime methods should fail before unsafe calls."""
    client = Client(TrafficManager())
    with pytest.raises(CarlaAdapterError, match=r"0\.\.100"):
        traffic_tuning.tune_traffic_vehicle(
            cast("object", client),
            actor_id=ACTOR_ID,
            traffic_manager_port=TM_PORT,
            settings={"ignore_walkers_percentage": 101.0},
        )

    partial = PartialTrafficManager()
    with pytest.raises(UnsupportedFeatureError, match="ignore_lights_percentage"):
        traffic_tuning.tune_traffic_vehicle(
            cast("object", Client(partial)),
            actor_id=ACTOR_ID,
            traffic_manager_port=TM_PORT,
            settings={"desired_speed": 12.0, "ignore_lights_percentage": 10.0},
        )
    assert partial.calls == []
