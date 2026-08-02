"""Sensor tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_mcp.adapter import CarlaAdapterError, SensorAdapter
from carla_mcp.models import CameraAttachRequest, ToolResult

if TYPE_CHECKING:
    from pathlib import Path

    from carla_mcp.snapshots import RunSnapshots


def attach_camera(
    adapter: SensorAdapter,
    snapshots: RunSnapshots,
    *,
    request: CameraAttachRequest,
) -> ToolResult:
    """Attach a CARLA camera sensor and publish the sensor snapshot."""
    try:
        sensor = adapter.attach_camera(request=request)
    except CarlaAdapterError as exc:
        return _adapter_error("attach_camera_failed", exc)
    payload = sensor.to_dict()
    snapshots.register_snapshot(f"carla-snapshot://sensors/{sensor.sensor_id}", payload)
    return ToolResult.ok(payload)


def capture_sensor_frame(
    adapter: SensorAdapter,
    snapshots: RunSnapshots,
    *,
    sensor_id: int,
    output_path: Path,
) -> ToolResult:
    """Capture one sensor frame and publish the capture snapshot."""
    try:
        capture = adapter.capture_sensor_frame(sensor_id=sensor_id, output_path=output_path)
    except CarlaAdapterError as exc:
        return _adapter_error("capture_sensor_frame_failed", exc)
    payload = capture.to_dict()
    snapshots.register_snapshot(f"carla-snapshot://captures/{capture.capture_id}", payload)
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
