"""Behavior specs for CARLA actor inspection and cleanup tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from carla_mcp.models import (
    ActorSnapshot,
    DestroyResult,
    Location,
    Rotation,
    Transform,
)
from carla_mcp.snapshots import RunSnapshots
from carla_mcp.tools.actor_management import destroy_actors, list_actors

ACTOR_ID: Final = 101


@dataclass
class ActorManagementAdapter:
    """Test double for actor management operations."""

    actors: tuple[ActorSnapshot, ...]
    destroy_results: tuple[DestroyResult, ...]
    last_filter: str | None = None

    def list_actors(self, filter_pattern: str) -> tuple[ActorSnapshot, ...]:
        """Return actor snapshots matching a filter."""
        self.last_filter = filter_pattern
        return self.actors

    def destroy_actors(self, actor_ids: tuple[int, ...]) -> tuple[DestroyResult, ...]:
        """Return predefined destroy results."""
        return self.destroy_results[: len(actor_ids)]


def test_list_actors_publishes_actor_inventory_snapshot() -> None:
    """Actor inspection should return stable snapshots and publish a snapshot."""
    snapshots = RunSnapshots()
    actor = _actor_snapshot()
    adapter = ActorManagementAdapter(actors=(actor,), destroy_results=())

    result = list_actors(adapter=adapter, snapshots=snapshots, filter_pattern="vehicle.*")

    assert result.is_error is False
    assert result.structured_content == {"actors": [actor.to_dict()]}
    assert adapter.last_filter == "vehicle.*"
    assert snapshots.read_snapshot("carla-snapshot://actors/current") == result.structured_content


def test_destroy_actors_publishes_cleanup_results() -> None:
    """Actor cleanup should report per-actor destroy outcomes."""
    snapshots = RunSnapshots()
    destroy_result = DestroyResult(actor_id=ACTOR_ID, destroyed=True, error=None)
    adapter = ActorManagementAdapter(actors=(), destroy_results=(destroy_result,))

    result = destroy_actors(adapter=adapter, snapshots=snapshots, actor_ids=(ACTOR_ID,))

    assert result.is_error is False
    assert result.structured_content == {"results": [destroy_result.to_dict()]}
    assert snapshots.read_snapshot("carla-snapshot://actors/destroyed") == result.structured_content


def _actor_snapshot() -> ActorSnapshot:
    """Create a representative actor snapshot."""
    return ActorSnapshot(
        actor_id=ACTOR_ID,
        type_id="vehicle.audi.a2",
        role_name="autopilot",
        transform=Transform(
            location=Location(x=1.0, y=2.0, z=0.0),
            rotation=Rotation(pitch=0.0, yaw=90.0, roll=0.0),
        ),
        speed_mps=8.0,
        traffic_light_state="Green",
    )
