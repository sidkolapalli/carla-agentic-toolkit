"""Behavior specs for CARLA world-control script facade operations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final, cast

from carla_agentic_toolkit.models import ActorCounts, HealthReport, WorldSettings, WorldState
from carla_agentic_toolkit.snapshots import RunSnapshots
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

    def get_world_settings(self) -> dict[str, object]:
        """Return all settings that scripts may temporarily change."""
        return self.world.settings.to_dict() | {
            "substepping": True,
            "max_substeps": 10,
            "max_substep_delta_time": 0.01,
        }

    def get_synchronous_mode(self) -> bool:
        """Observe mode without querying unrelated world state."""
        return self.world.settings.synchronous_mode

    def list_worlds(self) -> tuple[str, ...]:
        """Return available maps."""
        return ("Town10HD_Opt", LOADED_MAP)

    def load_world(self, map_name: str, *, reset_settings: bool = True) -> WorldState:
        """Load a map and return the resulting world state."""
        settings = (
            replace(self.world.settings, synchronous_mode=False)
            if reset_settings
            else self.world.settings
        )
        self.world = replace(self.world, current_map=map_name, settings=settings)
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

    def restore_world_settings(self, settings: dict[str, object]) -> WorldState:
        """Restore captured settings and return the resulting world state."""
        self.world = replace(
            self.world,
            settings=WorldSettings(
                synchronous_mode=bool(settings["synchronous_mode"]),
                fixed_delta_seconds=cast("float | None", settings["fixed_delta_seconds"]),
                no_rendering_mode=bool(settings["no_rendering_mode"]),
            ),
        )
        return self.world


def test_load_world_returns_new_world_state_and_updates_snapshot() -> None:
    """Loading a map should publish the resulting current-world snapshot."""
    snapshots = RunSnapshots()
    adapter = ControlAdapter(world=build_world_state())

    result = build_api(adapter, snapshots).load_world(LOADED_MAP)

    assert result == adapter.world.to_dict() | {
        "synchronous_mode": False,
        "synchronous_mode_changed": False,
    }
    assert snapshots.read_snapshot("carla-snapshot://world/current") == result


def test_set_sync_mode_returns_updated_timing_settings() -> None:
    """Sync configuration should publish the updated world timing state."""
    snapshots = RunSnapshots()
    adapter = ControlAdapter(world=build_world_state())
    previous_settings = adapter.get_world_settings()

    result = build_api(adapter, snapshots).set_sync_mode(
        enabled=True,
        fixed_delta_seconds=SYNC_DELTA_SECONDS,
    )

    assert result == adapter.world.to_dict() | {"previous_settings": previous_settings}
    assert snapshots.read_snapshot("carla-snapshot://world/current") == result


def test_disabling_sync_without_delta_clears_fixed_timestep() -> None:
    """Disabling sync with the default argument should return variable timestep."""
    adapter = ControlAdapter(world=build_world_state())

    result = build_api(adapter, RunSnapshots()).set_sync_mode(enabled=False)

    assert result["settings"] == adapter.world.settings.to_dict()
    assert adapter.world.settings.fixed_delta_seconds is None


def test_restore_world_settings_restores_previous_snapshot() -> None:
    """A finally block can pass the sync result's complete previous settings back."""
    snapshots = RunSnapshots()
    adapter = ControlAdapter(world=build_world_state())
    api = build_api(adapter, snapshots)
    previous_settings = adapter.get_world_settings()
    api.set_sync_mode(enabled=True, fixed_delta_seconds=SYNC_DELTA_SECONDS)

    result = api.restore_world_settings(previous_settings)

    assert result == adapter.world.to_dict()
    assert adapter.world.settings.synchronous_mode is False
    assert snapshots.read_snapshot("carla-snapshot://world/current") == result


def test_tick_advances_one_frame_and_updates_tick_snapshot() -> None:
    """A tick should return the frame advanced by CARLA."""
    snapshots = RunSnapshots()
    adapter = ControlAdapter(world=build_world_state())
    adapter.set_sync_mode(enabled=True, fixed_delta_seconds=SYNC_DELTA_SECONDS)

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
