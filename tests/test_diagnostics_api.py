"""Behavior specs for CARLA diagnostic script facade operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

import pytest

from carla_agentic_toolkit.adapter import CarlaAdapterError
from carla_agentic_toolkit.models import ActorCounts, HealthReport, WorldSettings, WorldState
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api

DEFAULT_MAPS: Final = ("Town10HD_Opt", "Town01")
EXPECTED_FRAME: Final = 42
EXPECTED_VEHICLES: Final = 2
DEFAULT_CARLA_PORT: Final = 2000
UNREACHABLE_SERVER_MESSAGE: Final = "CARLA server is not reachable on 127.0.0.1:2000"


@dataclass
class FakeAdapter:
    """Test double that behaves like a small slice of CARLA."""

    health: HealthReport
    world: WorldState
    maps: tuple[str, ...] = DEFAULT_MAPS
    fail_health: bool = False
    host: str = "127.0.0.1"
    port: int = DEFAULT_CARLA_PORT

    def health_check(self) -> HealthReport:
        """Return a health report or simulate a CARLA connection failure."""
        if self.fail_health:
            raise CarlaAdapterError(UNREACHABLE_SERVER_MESSAGE)
        return self.health

    def get_world_state(self) -> WorldState:
        """Return current world state."""
        return self.world

    def list_worlds(self) -> tuple[str, ...]:
        """Return available CARLA maps."""
        return self.maps

    def load_world(self, map_name: str, *, reset_settings: bool = True) -> WorldState:
        """Load a CARLA world by map name."""
        del reset_settings
        return replace(self.world, current_map=map_name)

    def set_sync_mode(self, *, enabled: bool, fixed_delta_seconds: float | None) -> WorldState:
        """Configure synchronous world stepping."""
        return replace(
            self.world,
            settings=WorldSettings(
                synchronous_mode=enabled,
                fixed_delta_seconds=fixed_delta_seconds,
                no_rendering_mode=self.world.settings.no_rendering_mode,
            ),
        )

    def tick(self) -> int:
        """Advance the world by one frame."""
        return EXPECTED_FRAME + 1


def build_world_state() -> WorldState:
    """Create a realistic small world state for behavior tests."""
    return WorldState(
        current_map="Town10HD_Opt",
        settings=WorldSettings(
            synchronous_mode=False,
            fixed_delta_seconds=None,
            no_rendering_mode=False,
        ),
        actor_counts=ActorCounts(vehicles=EXPECTED_VEHICLES, walkers=1, sensors=1, traffic=4),
        frame=EXPECTED_FRAME,
        warnings=("Traffic Manager state was not inspected.",),
    )


def build_health_report(*, connected: bool = True) -> HealthReport:
    """Create a health report matching the documented facade contract."""
    return HealthReport(
        connected=connected,
        client_version="0.10.0",
        server_version="0.10.0",
        current_map="Town10HD_Opt" if connected else None,
        settings=WorldSettings(
            synchronous_mode=False,
            fixed_delta_seconds=None,
            no_rendering_mode=False,
        ),
        actor_counts=ActorCounts(vehicles=EXPECTED_VEHICLES, walkers=1, sensors=1, traffic=4),
        warnings=(),
    )


def test_health_check_reports_connected_carla_session_as_structured_content() -> None:
    """A healthy CARLA server should produce a model-readable health payload."""
    snapshots = RunSnapshots()
    health_report = build_health_report()
    adapter = FakeAdapter(health=health_report, world=build_world_state())

    result = build_api(adapter, snapshots).health_check()

    assert result == health_report.to_dict()
    assert snapshots.read_snapshot("carla-snapshot://session/status") == health_report.to_dict()


def test_health_check_returns_tool_error_when_carla_connection_fails() -> None:
    """A failed CARLA connection should be a recoverable operation error, not a crash."""
    snapshots = RunSnapshots()
    adapter = FakeAdapter(
        health=build_health_report(connected=False),
        world=build_world_state(),
        fail_health=True,
    )

    result = build_api(adapter, snapshots).health_check()

    assert {
        "is_error": result["ok"] is False,
        "error_type": result["error_type"],
        "host": result["host"],
        "port": result["port"],
        "retryable": result["retryable"],
    } == {
        "is_error": True,
        "error_type": "carla_connection_error",
        "host": "127.0.0.1",
        "port": DEFAULT_CARLA_PORT,
        "retryable": True,
    }
    with pytest.raises(KeyError):
        snapshots.read_snapshot("carla-snapshot://session/status")


def test_get_world_state_exposes_current_world_as_snapshot() -> None:
    """A world-state read should update the read-only current-world snapshot."""
    snapshots = RunSnapshots()
    world_state = build_world_state()
    adapter = FakeAdapter(health=build_health_report(), world=world_state)

    result = build_api(adapter, snapshots).get_world_state()

    assert result == world_state.to_dict()
    assert snapshots.read_snapshot("carla-snapshot://world/current") == world_state.to_dict()


def test_list_worlds_sorts_maps_and_exposes_worlds_snapshot() -> None:
    """Available maps should be deterministic for facade results and snapshots."""
    snapshots = RunSnapshots()
    adapter = FakeAdapter(
        health=build_health_report(),
        world=build_world_state(),
        maps=("Town10HD_Opt", "Town01", "Town02"),
    )

    result = build_api(adapter, snapshots).list_worlds()

    assert result["worlds"] == ["Town01", "Town02", "Town10HD_Opt"]
    assert snapshots.read_snapshot("carla-snapshot://worlds")["worlds"] == [
        "Town01",
        "Town02",
        "Town10HD_Opt",
    ]
