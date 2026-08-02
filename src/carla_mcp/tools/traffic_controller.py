"""Persistent Traffic Manager controller tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from carla_mcp.errors import CarlaAdapterError
from carla_mcp.models import (
    ToolResult,
    TrafficControllerStartRequest,
    TrafficControllerStatus,
    TrafficDensityRequest,
    VehicleBehaviorRequest,
    VehicleBehaviorResult,
)

if TYPE_CHECKING:
    from carla_mcp.snapshots import RunSnapshots


class TrafficControllerService(Protocol):
    """Service boundary for persistent Traffic Manager control."""

    def start(self, request: TrafficControllerStartRequest) -> TrafficControllerStatus:
        """Start the persistent traffic controller."""

    def stop(self) -> TrafficControllerStatus:
        """Stop the persistent traffic controller."""

    def get_status(self) -> TrafficControllerStatus:
        """Return current controller status."""

    def set_density(self, request: TrafficDensityRequest) -> TrafficControllerStatus:
        """Converge traffic to the requested vehicle density."""

    def set_vehicle_behavior(self, request: VehicleBehaviorRequest) -> VehicleBehaviorResult:
        """Apply a behavior profile to explicit actors."""


def start_traffic_controller(
    service: TrafficControllerService,
    snapshots: RunSnapshots,
    request: TrafficControllerStartRequest,
) -> ToolResult:
    """Start the persistent traffic controller and publish status."""
    try:
        return _status_result(service.start(request), snapshots)
    except CarlaAdapterError as exc:
        return _controller_error("start_traffic_controller_failed", exc)


def stop_traffic_controller(
    service: TrafficControllerService, snapshots: RunSnapshots
) -> ToolResult:
    """Stop the persistent traffic controller and publish status."""
    try:
        return _status_result(service.stop(), snapshots)
    except CarlaAdapterError as exc:
        return _controller_error("stop_traffic_controller_failed", exc)


def traffic_controller_status(
    service: TrafficControllerService,
    snapshots: RunSnapshots,
) -> ToolResult:
    """Read the persistent traffic controller status."""
    try:
        return _status_result(service.get_status(), snapshots)
    except CarlaAdapterError as exc:
        return _controller_error("traffic_controller_status_failed", exc)


def set_traffic_density(
    service: TrafficControllerService,
    snapshots: RunSnapshots,
    request: TrafficDensityRequest,
) -> ToolResult:
    """Converge traffic to a requested vehicle density."""
    try:
        return _status_result(service.set_density(request), snapshots)
    except CarlaAdapterError as exc:
        return _controller_error("set_traffic_density_failed", exc)


def set_vehicle_behavior(
    service: TrafficControllerService,
    snapshots: RunSnapshots,
    request: VehicleBehaviorRequest,
) -> ToolResult:
    """Apply a named behavior profile to explicit actors."""
    try:
        result = service.set_vehicle_behavior(request)
    except CarlaAdapterError as exc:
        return _controller_error("set_vehicle_behavior_failed", exc)
    payload = result.to_dict()
    snapshots.register_snapshot("carla-snapshot://traffic/behaviors", payload)
    return ToolResult.ok(payload)


def _status_result(status: TrafficControllerStatus, snapshots: RunSnapshots) -> ToolResult:
    """Publish and return a controller status payload."""
    payload = status.to_dict()
    snapshots.register_snapshot("carla-snapshot://traffic/controller", payload)
    return ToolResult.ok(payload)


def _controller_error(error_type: str, exc: CarlaAdapterError) -> ToolResult:
    """Convert controller failures into MCP-style tool errors."""
    return ToolResult.error(
        {
            "error_type": error_type,
            "message": str(exc),
            "retryable": True,
        }
    )
