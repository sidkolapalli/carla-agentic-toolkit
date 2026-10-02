"""Behavior specs for the in-process CARLA traffic controller service."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final, cast

from carla_agentic_toolkit.models import (
    TrafficControllerStartRequest,
    TrafficDensityRequest,
    TrafficPopulationRequest,
)
from carla_agentic_toolkit.traffic_controller_service import (
    InProcessTrafficControllerService,
    TrafficControllerStep,
    maintain_traffic_once,
)

if TYPE_CHECKING:
    import pytest

    from carla_agentic_toolkit.carla_protocols import CarlaClient

ACTOR_ID: Final = 342
SPAWNED_ACTOR_ID: Final = 343


@dataclass
class VehicleActor:
    """Minimal vehicle actor test double."""

    id: int = ACTOR_ID
    destroyed: bool = False
    autopilot_calls: list[tuple[bool, int]] = field(default_factory=list)

    def set_autopilot(self, enabled: object, traffic_manager_port: int) -> None:
        """Record autopilot changes."""
        assert isinstance(enabled, bool)
        self.autopilot_calls.append((enabled, traffic_manager_port))

    def destroy(self) -> bool:
        """Record actor destruction."""
        self.destroyed = True
        return True

    def get_velocity(self) -> object:
        """Return a zero velocity vector."""
        return Vector()


@dataclass
class Vector:
    """Minimal CARLA vector test double."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class ActorCollection:
    """Minimal actor collection test double."""

    actors: tuple[VehicleActor, ...]

    def filter(self, pattern: str) -> tuple[VehicleActor, ...]:
        """Return vehicle actors."""
        assert pattern == "vehicle.*"
        return self.actors


@dataclass
class World:
    """Minimal world test double."""

    actor: VehicleActor
    events: list[str]

    def get_actors(self, _actor_ids: list[int] | None = None) -> ActorCollection:
        """Return current actors."""
        return ActorCollection((self.actor,) if not self.actor.destroyed else ())

    def get_settings(self) -> SimpleNamespace:
        """Use the asynchronous mode supported by background maintenance."""
        return SimpleNamespace(synchronous_mode=False)

    def wait_for_tick(self, seconds: float = 1.0) -> None:
        """Record world tick waits."""
        assert seconds == 1.0
        self.events.append("tick")


@dataclass
class Client:
    """Minimal CARLA client test double."""

    world: World

    def get_world(self) -> World:
        """Return the test world."""
        return self.world


@dataclass
class TrafficManager:
    """Minimal Traffic Manager test double."""

    port: int = 8000

    def get_port(self) -> int:
        """Return the Traffic Manager port."""
        return self.port


@dataclass
class TrafficManagerFactory:
    """Traffic Manager factory test double."""

    events: list[str]

    def __call__(self, _client: object, _port: int) -> TrafficManager:
        """Record Traffic Manager creation."""
        self.events.append("traffic_manager")
        return TrafficManager()


@dataclass
class ConfigureManager:
    """Traffic Manager configuration test double."""

    events: list[str]

    def __call__(self, _manager: object, _request: object) -> None:
        """Record Traffic Manager configuration."""
        self.events.append("configure")


@dataclass
class PopulateTraffic:
    """Traffic population test double."""

    world: World
    events: list[str]

    def __call__(
        self,
        world: object,
        traffic_manager_instance: object,
        request: TrafficPopulationRequest,
    ) -> tuple[list[int], list[object]]:
        """Record traffic population requests."""
        assert world is self.world
        assert isinstance(traffic_manager_instance, TrafficManager)
        assert request.vehicle_count == 1
        self.events.append("populate")
        return [], []


def test_reset_existing_vehicles_happens_before_traffic_manager_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reset should be a one-shot cleanup before Traffic Manager owns new actors."""
    events: list[str] = []
    actor = VehicleActor()
    client = Client(world=World(actor=actor, events=events))

    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_step.traffic_manager",
        TrafficManagerFactory(events),
    )
    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_step.configure_traffic_manager",
        ConfigureManager(events),
    )
    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_density.populate_traffic_actors",
        PopulateTraffic(world=client.world, events=events),
    )

    result = maintain_traffic_once(
        cast("CarlaClient", client),
        TrafficControllerStartRequest(
            density=TrafficDensityRequest(vehicle_count=1, reset_existing=True),
        ),
        {},
    )

    assert {
        "autopilot_calls": actor.autopilot_calls,
        "destroyed": actor.destroyed,
        "events": events,
        "target_count": result.request.density.vehicle_count,
        "reset_existing": result.request.density.reset_existing,
    } == {
        "autopilot_calls": [(False, 8000)],
        "destroyed": True,
        "events": ["tick", "tick", "traffic_manager", "configure", "populate", "tick"],
        "target_count": 1,
        "reset_existing": False,
    }


def test_existing_vehicles_are_registered_with_traffic_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing actors should start driving even when no new spawn is needed."""
    events: list[str] = []
    actor = VehicleActor()
    client = Client(world=World(actor=actor, events=events))

    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_step.traffic_manager",
        lambda _client, _port: TrafficManager(port=9000),
    )
    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_step.configure_traffic_manager",
        lambda _manager, _request: events.append("configure"),
    )
    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_density.populate_traffic_actors",
        lambda **_kwargs: events.append("populate"),
    )

    result = maintain_traffic_once(
        cast("CarlaClient", client),
        TrafficControllerStartRequest(density=TrafficDensityRequest(vehicle_count=1)),
        {},
    )

    assert {
        "autopilot_calls": actor.autopilot_calls,
        "destroyed": actor.destroyed,
        "events": events,
        "vehicle_count": result.vehicle_count,
        "registered_actor_ids": result.registered_actor_ids,
        "spawned_actor_ids": result.spawned_actor_ids,
    } == {
        "autopilot_calls": [(True, 9000)],
        "destroyed": False,
        "events": ["configure", "tick"],
        "vehicle_count": 1,
        "registered_actor_ids": frozenset({ACTOR_ID}),
        "spawned_actor_ids": (),
    }


def test_spawned_vehicles_are_distinct_from_adopted_vehicles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only actual controller spawns should enter failure-cleanup ownership."""
    events: list[str] = []
    actor = VehicleActor(destroyed=True)
    client = Client(world=World(actor=actor, events=events))
    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_step.traffic_manager",
        lambda _client, _port: TrafficManager(),
    )
    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_step.configure_traffic_manager",
        lambda _manager, _request: None,
    )
    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_density.populate_traffic_actors",
        lambda **_kwargs: ([SPAWNED_ACTOR_ID], []),
    )

    result = maintain_traffic_once(
        cast("CarlaClient", client),
        TrafficControllerStartRequest(density=TrafficDensityRequest(vehicle_count=1)),
        {},
    )

    assert result.registered_actor_ids == frozenset({SPAWNED_ACTOR_ID})
    assert result.spawned_actor_ids == (SPAWNED_ACTOR_ID,)


def test_controller_reports_only_actual_spawns_to_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adopted pre-existing vehicles must not be marked as script-owned."""
    owned: list[int] = []
    reported = threading.Event()
    request = TrafficControllerStartRequest(density=TrafficDensityRequest(vehicle_count=2))

    def step(*_args: object, **_kwargs: object) -> TrafficControllerStep:
        reported.wait(0.01)
        return TrafficControllerStep(
            request=request,
            vehicle_count=2,
            moving_vehicle_count=0,
            registered_actor_ids=frozenset({ACTOR_ID, SPAWNED_ACTOR_ID}),
            spawned_actor_ids=(SPAWNED_ACTOR_ID,),
        )

    def record(actor_ids: tuple[int, ...]) -> None:
        owned.extend(actor_ids)
        reported.set()

    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_service._client", lambda _request: object()
    )
    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_service.maintain_traffic_once", step
    )
    service = InProcessTrafficControllerService(on_spawn=record)
    service.start(request)
    assert reported.wait(1.0)
    service.stop()

    assert SPAWNED_ACTOR_ID in owned
    assert ACTOR_ID not in owned


def test_known_vehicles_are_not_registered_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Controller steps should not reset Traffic Manager ownership every tick."""
    events: list[str] = []
    actor = VehicleActor()
    client = Client(world=World(actor=actor, events=events))

    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_step.traffic_manager",
        lambda _client, _port: TrafficManager(port=9000),
    )
    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_step.configure_traffic_manager",
        lambda _manager, _request: events.append("configure"),
    )

    result = maintain_traffic_once(
        cast("CarlaClient", client),
        TrafficControllerStartRequest(density=TrafficDensityRequest(vehicle_count=1)),
        {},
        frozenset({ACTOR_ID}),
    )

    assert actor.autopilot_calls == []
    assert result.registered_actor_ids == frozenset({ACTOR_ID})


def test_configured_manager_can_be_left_undisturbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated controller steps should not reset Traffic Manager settings."""
    events: list[str] = []
    actor = VehicleActor()
    client = Client(world=World(actor=actor, events=events))

    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_step.traffic_manager",
        lambda _client, _port: TrafficManager(port=9000),
    )
    monkeypatch.setattr(
        "carla_agentic_toolkit.traffic_controller_step.configure_traffic_manager",
        lambda _manager, _request: events.append("configure"),
    )

    result = maintain_traffic_once(
        cast("CarlaClient", client),
        TrafficControllerStartRequest(density=TrafficDensityRequest(vehicle_count=1)),
        {},
        frozenset({ACTOR_ID}),
        configure_manager=False,
    )

    assert {
        "events": events,
        "autopilot_calls": actor.autopilot_calls,
        "registered_actor_ids": result.registered_actor_ids,
    } == {
        "events": ["tick"],
        "autopilot_calls": [],
        "registered_actor_ids": frozenset({ACTOR_ID}),
    }
