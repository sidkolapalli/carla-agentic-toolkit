"""Behavior specs for CARLA traffic MCP tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from carla_mcp.models import (
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
from carla_mcp.session import CarlaSession
from carla_mcp.tools.traffic import configure_traffic_manager, populate_traffic, set_autopilot

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


def test_populate_traffic_spawns_autopilot_vehicles_and_publishes_resource() -> None:
    """Populating traffic should return actor IDs and publish the traffic resource."""
    session = CarlaSession()
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

    result = populate_traffic(
        adapter=adapter,
        session=session,
        request=TrafficPopulationRequest(
            vehicle_count=TRAFFIC_VEHICLE_COUNT,
            traffic_manager_port=TRAFFIC_MANAGER_PORT,
            seed=42,
            safe_filter=True,
            global_distance_to_leading_vehicle=2.5,
            global_percentage_speed_difference=10.0,
        ),
    )

    assert result.is_error is False
    assert result.structured_content["actor_ids"] == list(SPAWNED_ACTOR_IDS)
    assert result.structured_content["spawned_vehicle_count"] == TRAFFIC_VEHICLE_COUNT
    assert session.read_resource("carla://traffic/population") == result.structured_content


def test_set_autopilot_returns_changed_actor_ids_and_publishes_resource() -> None:
    """Autopilot toggling should target explicit actors through Traffic Manager."""
    session = CarlaSession()
    adapter = TrafficAdapter(population=_empty_population())

    result = set_autopilot(
        adapter=adapter,
        session=session,
        request=AutopilotRequest(
            actor_ids=SPAWNED_ACTOR_IDS,
            enabled=True,
            traffic_manager_port=TRAFFIC_MANAGER_PORT,
        ),
    )

    assert result.is_error is False
    assert adapter.autopilot_calls == [(SPAWNED_ACTOR_IDS, True, TRAFFIC_MANAGER_PORT)]
    assert result.structured_content["actor_ids"] == list(SPAWNED_ACTOR_IDS)
    assert session.read_resource("carla://traffic/autopilot") == result.structured_content


def test_configure_traffic_manager_returns_settings_and_publishes_resource() -> None:
    """Traffic Manager configuration should expose the applied behavior settings."""
    session = CarlaSession()
    adapter = TrafficAdapter(population=_empty_population())

    result = configure_traffic_manager(
        adapter=adapter,
        session=session,
        request=TrafficManagerRequest(
            traffic_manager_port=TRAFFIC_MANAGER_PORT,
            global_distance_to_leading_vehicle=3.0,
            global_percentage_speed_difference=5.0,
            seed=11,
            synchronous_mode=False,
        ),
    )

    assert result.is_error is False
    assert adapter.configured_settings is not None
    assert result.structured_content == adapter.configured_settings.to_dict()
    assert session.read_resource("carla://traffic/manager") == result.structured_content


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
