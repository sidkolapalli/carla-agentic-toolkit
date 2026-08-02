"""Behavior specs for persistent CARLA traffic controller tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from carla_mcp.models import (
    TrafficControllerStartRequest,
    TrafficControllerStatus,
    TrafficDensityRequest,
    VehicleBehaviorRequest,
    VehicleBehaviorResult,
)
from carla_mcp.snapshots import RunSnapshots
from carla_mcp.tools.traffic_controller import (
    set_traffic_density,
    set_vehicle_behavior,
    start_traffic_controller,
    stop_traffic_controller,
    traffic_controller_status,
)

ACTOR_ID: Final = 91
TARGET_DENSITY: Final = 18


@dataclass
class TrafficControllerService:
    """Test double for persistent traffic controller operations."""

    status: TrafficControllerStatus
    density_request: TrafficDensityRequest | None = None
    behavior_request: VehicleBehaviorRequest | None = None
    started_with: TrafficControllerStartRequest | None = None
    stopped: bool = False

    def start(self, request: TrafficControllerStartRequest) -> TrafficControllerStatus:
        """Record controller start requests."""
        self.started_with = request
        self.status = _status(active=True, vehicle_count=request.density.vehicle_count)
        return self.status

    def stop(self) -> TrafficControllerStatus:
        """Record controller stop requests."""
        self.stopped = True
        self.status = _status(active=False, vehicle_count=0)
        return self.status

    def get_status(self) -> TrafficControllerStatus:
        """Return controller status."""
        return self.status

    def set_density(self, request: TrafficDensityRequest) -> TrafficControllerStatus:
        """Record density requests."""
        self.density_request = request
        self.status = _status(active=True, vehicle_count=request.vehicle_count)
        return self.status

    def set_vehicle_behavior(self, request: VehicleBehaviorRequest) -> VehicleBehaviorResult:
        """Record behavior requests."""
        self.behavior_request = request
        return VehicleBehaviorResult(
            actor_ids=request.actor_ids,
            profile=request.profile,
            applied_settings={"speed_difference": -10.0},
            failed_applications=(),
        )


def test_start_traffic_controller_publishes_controller_status() -> None:
    """Starting the controller should expose persistent Traffic Manager status."""
    snapshots = RunSnapshots()
    service = TrafficControllerService(status=_status(active=False, vehicle_count=0))
    request = TrafficControllerStartRequest(
        density=TrafficDensityRequest(vehicle_count=TARGET_DENSITY, seed=7)
    )

    result = start_traffic_controller(service=service, snapshots=snapshots, request=request)

    assert result.is_error is False
    assert service.started_with == request
    assert result.structured_content["active"] is True
    assert (
        snapshots.read_snapshot("carla-snapshot://traffic/controller") == result.structured_content
    )


def test_stop_traffic_controller_publishes_inactive_status() -> None:
    """Stopping the controller should publish an inactive controller snapshot."""
    snapshots = RunSnapshots()
    service = TrafficControllerService(status=_status(active=True, vehicle_count=TARGET_DENSITY))

    result = stop_traffic_controller(service=service, snapshots=snapshots)

    assert result.is_error is False
    assert service.stopped is True
    assert result.structured_content["active"] is False
    assert (
        snapshots.read_snapshot("carla-snapshot://traffic/controller") == result.structured_content
    )


def test_traffic_controller_status_reads_without_mutation() -> None:
    """Status reads should publish current controller state."""
    snapshots = RunSnapshots()
    service = TrafficControllerService(status=_status(active=True, vehicle_count=TARGET_DENSITY))

    result = traffic_controller_status(service=service, snapshots=snapshots)

    assert result.is_error is False
    assert result.structured_content["vehicle_count"] == TARGET_DENSITY
    assert (
        snapshots.read_snapshot("carla-snapshot://traffic/controller") == result.structured_content
    )


def test_set_traffic_density_converges_target_vehicle_count() -> None:
    """Density changes should ask the controller to converge to the target count."""
    snapshots = RunSnapshots()
    service = TrafficControllerService(status=_status(active=True, vehicle_count=10))
    request = TrafficDensityRequest(vehicle_count=TARGET_DENSITY, reset_existing=True)

    result = set_traffic_density(service=service, snapshots=snapshots, request=request)

    assert result.is_error is False
    assert service.density_request == request
    assert result.structured_content["target_vehicle_count"] == TARGET_DENSITY


def test_set_vehicle_behavior_applies_profile_to_explicit_actors() -> None:
    """Behavior profiles should target explicit actor IDs."""
    snapshots = RunSnapshots()
    service = TrafficControllerService(status=_status(active=True, vehicle_count=10))
    request = VehicleBehaviorRequest(actor_ids=(ACTOR_ID,), profile="aggressive")

    result = set_vehicle_behavior(service=service, snapshots=snapshots, request=request)

    assert result.is_error is False
    assert service.behavior_request == request
    assert result.structured_content == {
        "actor_ids": [ACTOR_ID],
        "profile": "aggressive",
        "applied_settings": {"speed_difference": -10.0},
        "failed_applications": [],
    }
    assert (
        snapshots.read_snapshot("carla-snapshot://traffic/behaviors") == result.structured_content
    )


def _status(*, active: bool, vehicle_count: int) -> TrafficControllerStatus:
    """Create a controller status for tests."""
    return TrafficControllerStatus(
        active=active,
        host="127.0.0.1",
        port=2000,
        traffic_manager_port=8000,
        target_vehicle_count=vehicle_count,
        vehicle_count=vehicle_count,
        moving_vehicle_count=vehicle_count,
        last_error=None,
    )
