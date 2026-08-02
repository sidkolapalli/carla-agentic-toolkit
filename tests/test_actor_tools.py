"""Behavior specs for CARLA actor MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from carla_mcp.models import (
    ActorCounts,
    BlueprintAttribute,
    BlueprintInfo,
    HealthReport,
    Location,
    Rotation,
    SpawnRequest,
    SpawnResult,
    Transform,
    WorldSettings,
    WorldState,
)
from carla_mcp.snapshots import RunSnapshots
from carla_mcp.tools.actors import list_blueprints, spawn_actor_batch

BLUEPRINT_ID: Final = "vehicle.tesla.model3"
SPAWNED_ACTOR_ID: Final = 17


@dataclass
class ActorAdapter:
    """Test double for actor-oriented CARLA operations."""

    blueprints: tuple[BlueprintInfo, ...]
    spawn_results: tuple[SpawnResult, ...]

    def health_check(self) -> HealthReport:
        """Return a minimal health report."""
        return HealthReport(
            connected=True,
            client_version="0.10.0",
            server_version="0.10.0",
            current_map="Town10HD_Opt",
            settings=WorldSettings(
                synchronous_mode=False,
                fixed_delta_seconds=None,
                no_rendering_mode=False,
            ),
            actor_counts=ActorCounts(),
        )

    def get_world_state(self) -> WorldState:
        """Return a minimal world state."""
        return WorldState(
            current_map="Town10HD_Opt",
            settings=_world_settings(),
            actor_counts=ActorCounts(),
            frame=1,
        )

    def list_worlds(self) -> tuple[str, ...]:
        """Return available maps."""
        return ("Town10HD_Opt",)

    def load_world(self, map_name: str) -> WorldState:
        """Load a map."""
        return WorldState(
            current_map=map_name,
            settings=_world_settings(),
            actor_counts=ActorCounts(),
            frame=1,
        )

    def set_sync_mode(self, *, enabled: bool, fixed_delta_seconds: float | None) -> WorldState:
        """Configure sync mode."""
        return WorldState(
            current_map="Town10HD_Opt",
            settings=WorldSettings(
                synchronous_mode=enabled,
                fixed_delta_seconds=fixed_delta_seconds,
                no_rendering_mode=False,
            ),
            actor_counts=ActorCounts(),
            frame=1,
        )

    def tick(self) -> int:
        """Advance one frame."""
        return 2

    def list_blueprints(self, filter_pattern: str) -> tuple[BlueprintInfo, ...]:
        """Return matching blueprints."""
        return tuple(bp for bp in self.blueprints if filter_pattern in (bp.blueprint_id, "*"))

    def spawn_actor_batch(self, requests: tuple[SpawnRequest, ...]) -> tuple[SpawnResult, ...]:
        """Return predefined spawn results."""
        return self.spawn_results[: len(requests)]


def test_list_blueprints_returns_sorted_blueprint_summaries_and_snapshot() -> None:
    """Blueprint listing should be deterministic and publish a filtered snapshot."""
    snapshots = RunSnapshots()
    adapter = ActorAdapter(
        blueprints=(
            BlueprintInfo(
                blueprint_id=BLUEPRINT_ID,
                tags=("vehicle", "car"),
                attributes=(
                    BlueprintAttribute(
                        attribute_id="color",
                        is_modifiable=True,
                        recommended_values=("red", "blue"),
                    ),
                ),
            ),
            BlueprintInfo(
                blueprint_id="walker.pedestrian.0001",
                tags=("walker",),
                attributes=(),
            ),
        ),
        spawn_results=(),
    )

    result = list_blueprints(adapter=adapter, snapshots=snapshots, filter_pattern="*")

    assert result.is_error is False
    assert result.structured_content["blueprints"] == [
        adapter.blueprints[0].to_dict(),
        adapter.blueprints[1].to_dict(),
    ]
    assert snapshots.read_snapshot("carla-snapshot://blueprints/*") == result.structured_content


def test_spawn_actor_batch_returns_structured_results_and_snapshot() -> None:
    """Batch spawn should publish structured actor creation results."""
    snapshots = RunSnapshots()
    spawn_request = SpawnRequest(
        blueprint_id=BLUEPRINT_ID,
        transform=Transform(
            location=Location(x=1.0, y=2.0, z=0.5),
            rotation=Rotation(pitch=0.0, yaw=90.0, roll=0.0),
        ),
        attributes={"role_name": "hero"},
    )
    adapter = ActorAdapter(
        blueprints=(),
        spawn_results=(
            SpawnResult(
                request_index=0,
                actor_id=SPAWNED_ACTOR_ID,
                error=None,
            ),
        ),
    )

    result = spawn_actor_batch(adapter=adapter, snapshots=snapshots, requests=(spawn_request,))

    assert result.is_error is False
    assert result.structured_content == {"results": [adapter.spawn_results[0].to_dict()]}
    assert snapshots.read_snapshot("carla-snapshot://actors") == result.structured_content


def _world_settings() -> WorldSettings:
    """Return default test world settings."""
    return WorldSettings(
        synchronous_mode=False,
        fixed_delta_seconds=None,
        no_rendering_mode=False,
    )
