"""Behavior specs for CARLA sensor script facade operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Final

from carla_agentic_toolkit.models import (
    CameraAttachRequest,
    CaptureInfo,
    Location,
    Rotation,
    SensorInfo,
    Transform,
)
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api

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

    def capture_sensor_frame(
        self, *, sensor_id: int, output_path: Path, color_converter: str | None = None
    ) -> CaptureInfo:
        """Capture one frame to disk."""
        assert color_converter is None
        return CaptureInfo(
            capture_id=self.capture.capture_id,
            sensor_id=sensor_id,
            path=output_path,
            frame=self.capture.frame,
            mime_type=self.capture.mime_type,
        )


def test_attach_camera_returns_sensor_info_and_snapshot() -> None:
    """Attaching a camera should publish a stable sensor snapshot."""
    snapshots = RunSnapshots()
    transform = build_transform()
    adapter = SensorAdapter(
        sensor=build_sensor_info(transform=transform),
        capture=build_capture_info(Path("frame.png")),
    )

    request = CameraAttachRequest(
        blueprint_id=CAMERA_BLUEPRINT_ID,
        transform=transform,
        attributes={"image_size_x": "800", "image_size_y": "600"},
        parent_actor_id=17,
    )
    result = build_api(adapter, snapshots).attach_camera(asdict(request))

    assert result["sensor_id"] == SENSOR_ID
    assert snapshots.read_snapshot(f"carla-snapshot://sensors/{SENSOR_ID}") == result


def test_capture_sensor_frame_returns_capture_info_and_snapshot(tmp_path: Path) -> None:
    """Capturing a sensor frame should publish a capture snapshot."""
    snapshots = RunSnapshots()
    output_path = tmp_path / "front-camera.png"
    adapter = SensorAdapter(
        sensor=build_sensor_info(transform=build_transform()),
        capture=build_capture_info(output_path),
    )

    result = build_api(adapter, snapshots).capture_sensor_frame(SENSOR_ID, str(output_path))

    assert result == replace(adapter.capture, path=output_path).to_dict()
    assert snapshots.read_snapshot(f"carla-snapshot://captures/{CAPTURE_ID}") == result


def test_capture_can_be_marked_for_native_mcp_image_content(tmp_path: Path) -> None:
    """Scripts should explicitly opt a durable capture into MCP publication."""
    snapshots = RunSnapshots()
    output_path = tmp_path / "published.png"
    adapter = SensorAdapter(
        sensor=build_sensor_info(transform=build_transform()),
        capture=build_capture_info(output_path),
    )

    result = build_api(adapter, snapshots).capture_sensor_frame(
        SENSOR_ID,
        str(output_path),
        publish=True,
    )

    assert result["publish"] is True
    assert snapshots.read_snapshot(f"carla-snapshot://captures/{CAPTURE_ID}")["publish"] is True


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
