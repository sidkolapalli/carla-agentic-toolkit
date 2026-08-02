"""World inspection tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_mcp.adapter import CarlaAdapterError, WorldAdapter
from carla_mcp.models import ToolResult

if TYPE_CHECKING:
    from carla_mcp.snapshots import RunSnapshots


def get_world_state(adapter: WorldAdapter, snapshots: RunSnapshots) -> ToolResult:
    """Read current CARLA world state and publish it as a snapshot."""
    try:
        state = adapter.get_world_state()
    except CarlaAdapterError as exc:
        return _adapter_error("world_state_failed", exc)
    payload = state.to_dict()
    snapshots.register_snapshot("carla-snapshot://world/current", payload)
    return ToolResult.ok(payload)


def list_worlds(adapter: WorldAdapter, snapshots: RunSnapshots) -> ToolResult:
    """List available CARLA maps and publish them as a snapshot."""
    try:
        worlds = sorted(adapter.list_worlds())
    except CarlaAdapterError as exc:
        return _adapter_error("list_worlds_failed", exc)
    payload = {"worlds": worlds}
    snapshots.register_snapshot("carla-snapshot://worlds", payload)
    return ToolResult.ok(payload)


def load_world(adapter: WorldAdapter, snapshots: RunSnapshots, map_name: str) -> ToolResult:
    """Load a CARLA map and publish the resulting current-world state."""
    try:
        state = adapter.load_world(map_name)
    except CarlaAdapterError as exc:
        return _adapter_error("load_world_failed", exc)
    payload = state.to_dict()
    snapshots.register_snapshot("carla-snapshot://world/current", payload)
    return ToolResult.ok(payload)


def set_sync_mode(
    adapter: WorldAdapter,
    snapshots: RunSnapshots,
    *,
    enabled: bool,
    fixed_delta_seconds: float | None,
) -> ToolResult:
    """Configure CARLA synchronous stepping and publish updated world state."""
    try:
        state = adapter.set_sync_mode(
            enabled=enabled,
            fixed_delta_seconds=fixed_delta_seconds,
        )
    except CarlaAdapterError as exc:
        return _adapter_error("set_sync_mode_failed", exc)
    payload = state.to_dict()
    snapshots.register_snapshot("carla-snapshot://world/current", payload)
    return ToolResult.ok(payload)


def tick(adapter: WorldAdapter, snapshots: RunSnapshots) -> ToolResult:
    """Advance CARLA by one frame and publish the last-tick snapshot."""
    try:
        frame = adapter.tick()
    except CarlaAdapterError as exc:
        return _adapter_error("tick_failed", exc)
    payload = {"frame": frame}
    snapshots.register_snapshot("carla-snapshot://session/last-tick", payload)
    return ToolResult.ok(payload)


def _adapter_error(error_type: str, exc: CarlaAdapterError) -> ToolResult:
    """Convert adapter failures into MCP-style tool errors."""
    return ToolResult.error(
        {
            "error_type": error_type,
            "message": str(exc),
            "retryable": True,
        }
    )
