"""Traffic Manager tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from carla_mcp.adapter import CarlaAdapterError
from carla_mcp.models import ToolResult

if TYPE_CHECKING:
    from carla_mcp.models import (
        AutopilotRequest,
        TrafficManagerRequest,
        TrafficManagerSettings,
        TrafficPopulationRequest,
        TrafficPopulationResult,
    )
    from carla_mcp.snapshots import RunSnapshots


class TrafficAdapter(Protocol):
    """Adapter contract for Traffic Manager tools."""

    def populate_traffic(
        self,
        *,
        request: TrafficPopulationRequest,
    ) -> TrafficPopulationResult:
        """Spawn vehicles and register them with Traffic Manager."""

    def set_autopilot(
        self,
        *,
        request: AutopilotRequest,
    ) -> TrafficPopulationResult:
        """Toggle Traffic Manager autopilot for existing vehicles."""

    def configure_traffic_manager(
        self,
        *,
        request: TrafficManagerRequest,
    ) -> TrafficManagerSettings:
        """Configure Traffic Manager behavior settings."""


def populate_traffic(
    adapter: TrafficAdapter,
    snapshots: RunSnapshots,
    *,
    request: TrafficPopulationRequest,
) -> ToolResult:
    """Populate the current map with Traffic Manager-controlled vehicles."""
    try:
        population = adapter.populate_traffic(request=request)
    except CarlaAdapterError as exc:
        return _adapter_error("populate_traffic_failed", exc)
    payload = population.to_dict()
    snapshots.register_snapshot("carla-snapshot://traffic/population", payload)
    snapshots.register_snapshot("carla-snapshot://world/current", population.world_state.to_dict())
    return ToolResult.ok(payload)


def set_autopilot(
    adapter: TrafficAdapter,
    snapshots: RunSnapshots,
    *,
    request: AutopilotRequest,
) -> ToolResult:
    """Toggle autopilot for existing vehicle actors."""
    try:
        result = adapter.set_autopilot(request=request)
    except CarlaAdapterError as exc:
        return _adapter_error("set_autopilot_failed", exc)
    payload = result.to_dict()
    snapshots.register_snapshot("carla-snapshot://traffic/autopilot", payload)
    snapshots.register_snapshot("carla-snapshot://world/current", result.world_state.to_dict())
    return ToolResult.ok(payload)


def configure_traffic_manager(
    adapter: TrafficAdapter,
    snapshots: RunSnapshots,
    *,
    request: TrafficManagerRequest,
) -> ToolResult:
    """Configure Traffic Manager behavior and publish applied settings."""
    try:
        settings = adapter.configure_traffic_manager(request=request)
    except CarlaAdapterError as exc:
        return _adapter_error("configure_traffic_manager_failed", exc)
    payload = settings.to_dict()
    snapshots.register_snapshot("carla-snapshot://traffic/manager", payload)
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
