"""Behavior specs for CARLA sensor MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from carla_mcp.models import (
    CameraAttachRequest,
    CaptureInfo,
    Location,
    Rotation,
    SensorInfo,
    Transform,
)
from carla_mcp.session import CarlaSession
from carla_mcp.tools.sensors import attach_camera, capture_sensor_frame

CAMERA_BLUEPRINT_ID: Final = "sensor.camera.rgb"
SENSOR_ID: Final = 77
CAPTURE_ID: Final = "capture-000077"
FRAME_ID: Final = 120


@dataclass
class SensorAdapter:
    """Test double for CARLA sensor operations."""

    sensor: SensorInfo
    capture: CaptureInfo

    def attach_camera(
        self,
        *,
        request: CameraAttachRequest,
    ) -> SensorInfo:
        """Attach a camera sensor."""
        return SensorInfo(
            sensor_id=self.sensor.sensor_id,
            blueprint_id=request.blueprint_id,
            parent_actor_id=request.parent_actor_id,
            attributes=dict(request.attributes),
            transform=request.transform,
        )

    def capture_sensor_frame(self, *, sensor_id: int, output_path: Path) -> CaptureInfo:
        """Capture one frame to disk."""
        return CaptureInfo(
            capture_id=self.capture.capture_id,
            sensor_id=sensor_id,
            path=output_path,
            frame=self.capture.frame,
            mime_type=self.capture.mime_type,
        )


def test_attach_camera_returns_sensor_info_and_resource() -> None:
    """Attaching a camera should publish a stable sensor resource."""
    session = CarlaSession()
    transform = build_transform()
    adapter = SensorAdapter(
        sensor=build_sensor_info(transform=transform),
        capture=build_capture_info(Path("frame.png")),
    )

    result = attach_camera(
        adapter=adapter,
        session=session,
        request=CameraAttachRequest(
            blueprint_id=CAMERA_BLUEPRINT_ID,
            transform=transform,
            attributes={"image_size_x": "800", "image_size_y": "600"},
            parent_actor_id=17,
        ),
    )

    assert result.is_error is False
    assert result.structured_content["sensor_id"] == SENSOR_ID
    assert session.read_resource(f"carla://sensors/{SENSOR_ID}") == result.structured_content


def test_capture_sensor_frame_returns_capture_info_and_resource(tmp_path: Path) -> None:
    """Capturing a sensor frame should publish a capture resource."""
    session = CarlaSession()
    output_path = tmp_path / "front-camera.png"
    adapter = SensorAdapter(
        sensor=build_sensor_info(transform=build_transform()),
        capture=build_capture_info(output_path),
    )

    result = capture_sensor_frame(
        adapter=adapter,
        session=session,
        sensor_id=SENSOR_ID,
        output_path=output_path,
    )

    assert result.is_error is False
    assert result.structured_content == adapter.capture.with_path(output_path).to_dict()
    assert session.read_resource(f"carla://captures/{CAPTURE_ID}") == result.structured_content


def build_transform() -> Transform:
    """Create a camera transform for tests."""
    return Transform(
        location=Location(x=1.5, y=0.0, z=2.2),
        rotation=Rotation(pitch=-5.0, yaw=0.0, roll=0.0),
    )


def build_sensor_info(transform: Transform) -> SensorInfo:
    """Create sensor metadata for tests."""
    return SensorInfo(
        sensor_id=SENSOR_ID,
        blueprint_id=CAMERA_BLUEPRINT_ID,
        parent_actor_id=17,
        attributes={"image_size_x": "800", "image_size_y": "600"},
        transform=transform,
    )


def build_capture_info(path: Path) -> CaptureInfo:
    """Create capture metadata for tests."""
    return CaptureInfo(
        capture_id=CAPTURE_ID,
        sensor_id=SENSOR_ID,
        path=path,
        frame=FRAME_ID,
        mime_type="image/png",
    )
