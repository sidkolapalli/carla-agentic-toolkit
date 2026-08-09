"""Behavior specs for CARLA traffic script facade operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Final

from carla_agentic_toolkit.models import (
    ActorCounts,
    AutopilotRequest,
    HealthReport,
    TrafficManagerRequest,
    TrafficManagerSettings,
    TrafficPopulationRequest,
    TrafficPopulationResult,
    WorldSettings,
    WorldState,
)
from carla_agentic_toolkit.snapshots import RunSnapshots
from carla_agentic_toolkit.traffic_runtime import _spawn_traffic_actor, _SpawnTrafficContext
from tests.api_helpers import build_api

TRAFFIC_MANAGER_PORT: Final = 8000
TRAFFIC_VEHICLE_COUNT: Final = 3
SPAWNED_ACTOR_IDS: Final = (101, 102, 103)


@dataclass
class TrafficAdapter:
    """Test double for Traffic Manager-oriented CARLA operations."""

    population: TrafficPopulationResult
    configured_settings: TrafficManagerSettings | None = None
    autopilot_calls: list[tuple[tuple[int, ...], bool, int]] = field(default_factory=list)

    def health_check(self) -> HealthReport:
        """Return a minimal health report."""
        return HealthReport(
            connected=True,
            client_version="0.9.16",
            server_version="0.9.16",
            current_map="Town10HD_Opt",
            settings=_world_settings(),
            actor_counts=ActorCounts(vehicles=3),
        )

    def get_world_state(self) -> WorldState:
        """Return a minimal world state."""
        return WorldState(
            current_map="Town10HD_Opt",
            settings=_world_settings(),
            actor_counts=ActorCounts(vehicles=3, traffic=22),
            frame=10,
        )

    def populate_traffic(
        self,
        *,
        request: TrafficPopulationRequest,
    ) -> TrafficPopulationResult:
        """Return a predefined population result."""
        self.configured_settings = TrafficManagerSettings(
            traffic_manager_port=request.traffic_manager_port,
            global_distance_to_leading_vehicle=request.global_distance_to_leading_vehicle,
            global_percentage_speed_difference=request.global_percentage_speed_difference,
            seed=request.seed,
            safe_filter=request.safe_filter,
        )
        assert request.vehicle_count == self.population.requested_vehicle_count
        return self.population

    def set_autopilot(
        self,
        *,
        request: AutopilotRequest,
    ) -> TrafficPopulationResult:
        """Record autopilot calls and return actor IDs as the changed set."""
        self.autopilot_calls.append(
            (request.actor_ids, request.enabled, request.traffic_manager_port)
        )
        return TrafficPopulationResult(
            requested_vehicle_count=len(request.actor_ids),
            spawned_vehicle_count=len(request.actor_ids),
            actor_ids=request.actor_ids,
            failed_spawns=(),
            settings=TrafficManagerSettings(
                traffic_manager_port=request.traffic_manager_port,
                global_distance_to_leading_vehicle=None,
                global_percentage_speed_difference=None,
                seed=None,
                safe_filter=None,
            ),
            world_state=self.get_world_state(),
        )

    def configure_traffic_manager(
        self,
        *,
        request: TrafficManagerRequest,
    ) -> TrafficManagerSettings:
        """Return the applied traffic-manager settings."""
        self.configured_settings = TrafficManagerSettings(
            traffic_manager_port=request.traffic_manager_port,
            global_distance_to_leading_vehicle=request.global_distance_to_leading_vehicle,
            global_percentage_speed_difference=request.global_percentage_speed_difference,
            seed=request.seed,
            safe_filter=None,
            synchronous_mode=request.synchronous_mode,
        )
        return self.configured_settings


def test_populate_traffic_spawns_autopilot_vehicles_and_publishes_snapshot() -> None:
    """Populating traffic should return actor IDs and publish the traffic snapshot."""
    snapshots = RunSnapshots()
    adapter = TrafficAdapter(
        population=TrafficPopulationResult(
            requested_vehicle_count=TRAFFIC_VEHICLE_COUNT,
            spawned_vehicle_count=TRAFFIC_VEHICLE_COUNT,
            actor_ids=SPAWNED_ACTOR_IDS,
            failed_spawns=(),
            settings=TrafficManagerSettings(
                traffic_manager_port=TRAFFIC_MANAGER_PORT,
                global_distance_to_leading_vehicle=2.5,
                global_percentage_speed_difference=10.0,
                seed=42,
                safe_filter=True,
            ),
            world_state=WorldState(
                current_map="Town10HD_Opt",
                settings=_world_settings(),
                actor_counts=ActorCounts(vehicles=3, traffic=22),
                frame=10,
            ),
        )
    )

    request = TrafficPopulationRequest(
        vehicle_count=TRAFFIC_VEHICLE_COUNT,
        traffic_manager_port=TRAFFIC_MANAGER_PORT,
        seed=42,
        safe_filter=True,
        global_distance_to_leading_vehicle=2.5,
        global_percentage_speed_difference=10.0,
    )
    result = build_api(adapter, snapshots).populate_traffic(asdict(request))

    assert result["actor_ids"] == list(SPAWNED_ACTOR_IDS)
    assert result["spawned_vehicle_count"] == TRAFFIC_VEHICLE_COUNT
    assert snapshots.read_snapshot("carla-snapshot://traffic/population") == result


def test_set_autopilot_returns_changed_actor_ids_and_publishes_snapshot() -> None:
    """Autopilot toggling should target explicit actors through Traffic Manager."""
    snapshots = RunSnapshots()
    adapter = TrafficAdapter(population=_empty_population())

    request = AutopilotRequest(
        actor_ids=SPAWNED_ACTOR_IDS,
        enabled=True,
        traffic_manager_port=TRAFFIC_MANAGER_PORT,
    )
    payload = {**asdict(request), "actor_ids": list(request.actor_ids)}
    result = build_api(adapter, snapshots).set_autopilot(payload)

    assert adapter.autopilot_calls == [(SPAWNED_ACTOR_IDS, True, TRAFFIC_MANAGER_PORT)]
    assert result["actor_ids"] == list(SPAWNED_ACTOR_IDS)
    assert snapshots.read_snapshot("carla-snapshot://traffic/autopilot") == result


def test_configure_traffic_manager_returns_settings_and_publishes_snapshot() -> None:
    """Traffic Manager configuration should expose the applied behavior settings."""
    snapshots = RunSnapshots()
    adapter = TrafficAdapter(population=_empty_population())

    request = TrafficManagerRequest(
        traffic_manager_port=TRAFFIC_MANAGER_PORT,
        global_distance_to_leading_vehicle=3.0,
        global_percentage_speed_difference=5.0,
        seed=11,
        synchronous_mode=False,
    )
    result = build_api(adapter, snapshots).configure_traffic_manager(asdict(request))

    assert adapter.configured_settings is not None
    assert result == adapter.configured_settings.to_dict()
    assert snapshots.read_snapshot("carla-snapshot://traffic/manager") == result


def _empty_population() -> TrafficPopulationResult:
    """Create an empty population result for tests that do not populate actors."""
    return TrafficPopulationResult(
        requested_vehicle_count=0,
        spawned_vehicle_count=0,
        actor_ids=(),
        failed_spawns=(),
        settings=TrafficManagerSettings(
            traffic_manager_port=TRAFFIC_MANAGER_PORT,
            global_distance_to_leading_vehicle=None,
            global_percentage_speed_difference=None,
            seed=None,
            safe_filter=None,
        ),
        world_state=WorldState(
            current_map="Town10HD_Opt",
            settings=_world_settings(),
            actor_counts=ActorCounts(),
            frame=1,
        ),
    )


def _world_settings() -> WorldSettings:
    """Return default test world settings."""
    return WorldSettings(
        synchronous_mode=False,
        fixed_delta_seconds=None,
        no_rendering_mode=False,
    )


def test_spawn_traffic_actor_cleans_up_on_autopilot_failure() -> None:
    """When autopilot fails after spawn, the actor must be destroyed."""
    destroyed_ids: list[int] = []
    autopilot_error = "autopilot unavailable"

    class BrokenAutopilotActor:
        id = 999

        def set_autopilot(self, _enabled: object, _port: int) -> None:
            raise RuntimeError(autopilot_error)

        def destroy(self) -> bool:
            destroyed_ids.append(self.id)
            return True

    class FakeWorld:
        @staticmethod
        def try_spawn_actor(_blueprint: object, _point: object) -> BrokenAutopilotActor:
            return BrokenAutopilotActor()

    class FakeTM:
        @staticmethod
        def get_port() -> int:
            return 8000

    context = _SpawnTrafficContext(
        world=FakeWorld(),  # type: ignore[arg-type]
        traffic_manager_instance=FakeTM(),  # type: ignore[arg-type]
        seed=0,
    )

    result = _spawn_traffic_actor(
        context=context,
        blueprint=_FakeBlueprint(),  # type: ignore[arg-type]
        spawn_point=object(),
        index=0,
    )

    assert result.error is not None
    assert autopilot_error in str(result.error)
    actor_id = 999
    assert actor_id in destroyed_ids, "actor should be destroyed when autopilot fails"


class _FakeBlueprint:
    id = "vehicle.audi.a2"

    @staticmethod
    def has_attribute(_name: str) -> bool:
        return False
