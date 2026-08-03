"""Behavior specs for CARLA world-control script facade operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from carla_mcp.models import ActorCounts, HealthReport, WorldSettings, WorldState
from carla_mcp.snapshots import RunSnapshots
from tests.api_helpers import build_api

LOADED_MAP: Final = "Town01"
NEXT_FRAME: Final = 43
SYNC_DELTA_SECONDS: Final = 0.05


@dataclass
class ControlAdapter:
    """Test double for CARLA world-control operations."""

    world: WorldState

    def health_check(self) -> HealthReport:
        """Return a minimal health report."""
        return HealthReport(
            connected=True,
            client_version="0.10.0",
            server_version="0.10.0",
            current_map=self.world.current_map,
            settings=self.world.settings,
            actor_counts=self.world.actor_counts,
        )

    def get_world_state(self) -> WorldState:
        """Return current world state."""
        return self.world

    def list_worlds(self) -> tuple[str, ...]:
        """Return available maps."""
        return ("Town10HD_Opt", LOADED_MAP)

    def load_world(self, map_name: str) -> WorldState:
        """Load a map and return the resulting world state."""
        self.world = replace(self.world, current_map=map_name)
        return self.world

    def set_sync_mode(self, *, enabled: bool, fixed_delta_seconds: float | None) -> WorldState:
        """Apply synchronous stepping settings."""
        self.world = replace(
            self.world,
            settings=WorldSettings(
                synchronous_mode=enabled,
                fixed_delta_seconds=fixed_delta_seconds,
                no_rendering_mode=False,
            ),
        )
        return self.world

    def tick(self) -> int:
        """Advance the world by one frame."""
        self.world = replace(self.world, frame=NEXT_FRAME)
        return NEXT_FRAME


def test_load_world_returns_new_world_state_and_updates_snapshot() -> None:
    """Loading a map should publish the resulting current-world snapshot."""
    snapshots = RunSnapshots()
    adapter = ControlAdapter(world=build_world_state())

    result = build_api(adapter, snapshots).load_world(LOADED_MAP)

    assert result == adapter.world.to_dict()
    assert snapshots.read_snapshot("carla-snapshot://world/current") == adapter.world.to_dict()


def test_set_sync_mode_returns_updated_timing_settings() -> None:
    """Sync configuration should publish the updated world timing state."""
    snapshots = RunSnapshots()
    adapter = ControlAdapter(world=build_world_state())

    result = build_api(adapter, snapshots).set_sync_mode(
        enabled=True,
        fixed_delta_seconds=SYNC_DELTA_SECONDS,
    )

    assert result == adapter.world.to_dict()
    assert snapshots.read_snapshot("carla-snapshot://world/current") == adapter.world.to_dict()


def test_tick_advances_one_frame_and_updates_tick_snapshot() -> None:
    """A tick should return the frame advanced by CARLA."""
    snapshots = RunSnapshots()
    adapter = ControlAdapter(world=build_world_state())

    result = build_api(adapter, snapshots).tick()

    assert result == {"frame": NEXT_FRAME}
    assert snapshots.read_snapshot("carla-snapshot://session/last-tick") == {"frame": NEXT_FRAME}


def build_world_state() -> WorldState:
    """Create a small world state for world-control specs."""
    return WorldState(
        current_map="Town10HD_Opt",
        settings=WorldSettings(
            synchronous_mode=False,
            fixed_delta_seconds=None,
            no_rendering_mode=False,
        ),
        actor_counts=ActorCounts(vehicles=0, walkers=0, sensors=0, traffic=0),
        frame=42,
    )
