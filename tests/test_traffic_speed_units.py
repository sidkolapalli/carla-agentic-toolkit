"""Traffic Manager speed names preserve native km/h semantics and preset behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import traffic_tuning
from carla_agentic_toolkit.behavior_profiles import behavior_profile
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import TrafficControllerStartRequest, TrafficDensityRequest
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from carla_agentic_toolkit.traffic_controller_step import maintain_traffic_once
from tests.test_traffic_controller_service import ActorCollection, VehicleActor, World
from tests.test_traffic_tuning_api import ACTOR_ID, TM_PORT, Client, TrafficManager

if TYPE_CHECKING:
    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.carla_protocols import CarlaClient


@pytest.mark.parametrize(
    "settings",
    [
        {"desired_speed_kmh": 36.0},
        {"desired_speed": 36.0},
        {"desired_speed": 36, "desired_speed_kmh": 36.0},
    ],
)
def test_native_speed_uses_canonical_kmh_without_conversion(settings: dict[str, object]) -> None:
    """Canonical and deprecated names send the same km/h value exactly once."""
    manager = TrafficManager()
    original = dict(settings)
    result = traffic_tuning.tune_traffic_vehicle(
        Client(manager),
        actor_id=ACTOR_ID,
        traffic_manager_port=TM_PORT,
        settings=settings,
    )
    assert manager.calls == [("speed", ACTOR_ID, 36.0)]
    assert result == {
        "actor_id": ACTOR_ID,
        "traffic_manager_port": TM_PORT,
        "desired_speed_kmh": 36.0,
    }
    assert settings == original


@pytest.mark.parametrize(
    "settings",
    [
        {"desired_speed": 36.0, "desired_speed_kmh": 37.0},
        {"desired_speed": True, "desired_speed_kmh": 1.0},
        {"desired_speed": 0, "desired_speed_kmh": False},
        {"desired_speed": "36", "desired_speed_kmh": 36},
        {"desired_speed": float("nan"), "desired_speed_kmh": 36},
        {"desired_speed": 36, "desired_speed_kmh": float("inf")},
        {"desired_speed_kmh": -1},
        {"desired_speed": -1},
    ],
)
def test_invalid_speed_aliases_fail_before_traffic_manager_access(
    settings: dict[str, object],
) -> None:
    """Parsing every present alias precedes equality checks and native side effects."""
    client = NoTrafficAccess()
    with pytest.raises(CarlaAdapterError, match="desired_speed"):
        traffic_tuning.tune_traffic_vehicle(
            client,
            actor_id=ACTOR_ID,
            traffic_manager_port=TM_PORT,
            settings={"auto_lane_change": False, **settings},
        )
    assert client.calls == []


@dataclass
class NoTrafficAccess:
    """Expose whether malformed input reached a Traffic Manager RPC."""

    calls: list[int] = field(default_factory=list)

    def get_trafficmanager(self, port: int) -> None:
        """Record an unexpected native lookup."""
        self.calls.append(port)


@dataclass
class NativeSpeedManager(TrafficManager):
    """Model CARLA Parameters.cpp's mutually exclusive per-vehicle speed maps."""

    exact_kmh: dict[int, float] = field(default_factory=dict)
    percentages: dict[int, float] = field(default_factory=dict)

    def get_port(self) -> int:
        """Expose the configured Traffic Manager port."""
        return TM_PORT

    def set_global_distance_to_leading_vehicle(self, value: float) -> None:
        """Record a global setter that these unconfigured passes must not invoke."""
        self.calls.append(("global_distance", value))

    def global_percentage_speed_difference(self, value: float) -> None:
        """Record a global setter that these unconfigured passes must not invoke."""
        self.calls.append(("global_percentage", value))

    def set_random_device_seed(self, value: int) -> None:
        """Expose the native seed method required by manager capability validation."""
        self.calls.append(("seed", value))

    def set_synchronous_mode(self, value: object) -> None:
        """Expose the native mode setter without granting synchronous maintenance."""
        self.calls.append(("sync", value))

    def set_desired_speed(self, actor: object, speed: float) -> None:
        """Remove the percentage target when setting an exact speed."""
        actor_id = cast("VehicleActor", actor).id
        self.calls.append(("speed", actor_id, speed))
        self.exact_kmh[actor_id] = speed
        self.percentages.pop(actor_id, None)

    def vehicle_percentage_speed_difference(self, actor: object, percentage: float) -> None:
        """Remove any exact target when setting the profile's percentage."""
        actor_id = cast("VehicleActor", actor).id
        self.calls.append(("percentage", actor_id, percentage))
        self.percentages[actor_id] = percentage
        self.exact_kmh.pop(actor_id, None)


class ProfileActors(ActorCollection):
    """Match the native actor collection's authoritative explicit-ID lookup."""

    def find(self, actor_id: int) -> VehicleActor | None:
        """Resolve the single known vehicle."""
        return next((actor for actor in self.actors if actor.id == actor_id), None)


class ProfileWorld(World):
    """Retain real asynchronous maintenance and explicit actor lookup semantics."""

    def get_actors(self, _actor_ids: list[int] | None = None) -> ProfileActors:
        """Return the known vehicle without creating or adopting deletion rights."""
        return ProfileActors((self.actor,))


@dataclass
class ProfileClient:
    """Expose one authoritative world and the same native Traffic Manager."""

    manager: NativeSpeedManager
    world: ProfileWorld

    def get_world(self) -> ProfileWorld:
        """Return the asynchronous test world."""
        return self.world

    def get_trafficmanager(self, port: int) -> NativeSpeedManager:
        """Return the manager used by tuning and density maintenance."""
        assert port == TM_PORT
        return self.manager


@pytest.mark.parametrize(
    "profile_name", ["normal", "cautious", "aggressive", "impatient", "stalled"]
)
@pytest.mark.parametrize("speed_setting", ["desired_speed", "desired_speed_kmh"])
def test_actual_profile_reapplication_clears_explicit_speed_each_pass(
    profile_name: str,
    speed_setting: str,
) -> None:
    """The documented interaction follows the actual maintenance path twice."""
    expected_percentage = _profile_percentage(profile_name)
    actor = VehicleActor(id=ACTOR_ID)
    world = ProfileWorld(actor, [])
    manager = NativeSpeedManager()
    client = ProfileClient(manager, world)
    request = TrafficControllerStartRequest(density=TrafficDensityRequest(vehicle_count=1))
    for speed in (36.0, 54.0):
        traffic_tuning.tune_traffic_vehicle(
            client,
            actor_id=ACTOR_ID,
            traffic_manager_port=TM_PORT,
            settings={speed_setting: speed},
        )
        result = maintain_traffic_once(
            cast("CarlaClient", client),
            request,
            {ACTOR_ID: profile_name},
            frozenset({ACTOR_ID}),
            configure_manager=False,
        )
        assert (manager.exact_kmh, manager.percentages) == ({}, {ACTOR_ID: expected_percentage})
        assert result.owned_actor_ids == frozenset()
    assert world.events == ["tick", "tick"]


def _profile_percentage(name: str) -> float:
    profile = behavior_profile(name)
    assert profile is not None
    return profile.speed_difference


def test_discoverable_api_names_kmh_deprecation_and_profile_interaction() -> None:
    """Agent-facing descriptions distinguish TM presets from BehaviorAgent and speed units."""
    api = CarlaScriptApi(cast("PythonCarlaAdapter", object()), RunSnapshots())
    methods = cast("dict[str, dict[str, object]]", api.describe_api()["methods"])
    speed_description = str(methods["tune_traffic_vehicle"]["doc"])
    behavior_description = str(methods["set_vehicle_behavior"]["doc"])
    assert ("desired_speed_kmh" in speed_description, "km/h" in speed_description) == (True, True)
    assert "deprecated" in speed_description
    assert "overrid" in speed_description
    assert "BehaviorAgent" in behavior_description
