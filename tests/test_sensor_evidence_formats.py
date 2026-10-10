"""Native sensor evidence keeps file formats and raw image ground truth truthful."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.output_content import published_captures
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api

if TYPE_CHECKING:
    from collections.abc import Callable

PNG_BYTES = b"\x89PNG\r\n\x1a\nraw-image"
JPEG_BYTES = b"\xff\xd8\xffimage"
PLY_BYTES = b"ply\nformat ascii 1.0\nelement vertex 0\nend_header\n"
CONVERTED_BYTES = b"\x89PNG\r\n\x1a\ndisplay-image"


@dataclass
class WritableFrame:
    """Save a raw frame or a converted view without mutating the frame bytes."""

    data: bytes = PNG_BYTES
    frame: int = 42
    timestamp: float = 1.25
    writes: list[tuple[Path, object | None]] = field(default_factory=list)
    write_output: bool = True

    def save_to_disk(self, path: str, color_converter: object | None = None) -> str:
        """Model CARLA's writer retaining raw data while converting the saved view."""
        target = Path(path)
        self.writes.append((target, color_converter))
        if self.write_output:
            target.write_bytes(self.data if color_converter is None else CONVERTED_BYTES)
        return path


@dataclass
class DigestFrame:
    """Represent GNSS/IMU data without a native file persistence method."""

    frame: int = 42
    timestamp: float = 1.25


@dataclass
class Sensor:
    """Deliver one native-style frame and track the listener lifetime."""

    type_id: str
    measurement: object
    id: int = 7
    listens: int = 0
    stops: int = 0

    def listen(self, callback: Callable[[object], None]) -> None:
        """Deliver a frame immediately without ticking the simulator."""
        self.listens += 1
        callback(self.measurement)

    def stop(self) -> None:
        """Acknowledge stopping the original listener."""
        self.stops += 1


def sensor_adapter(monkeypatch: pytest.MonkeyPatch, sensor: Sensor) -> PythonCarlaAdapter:
    """Build the real adapter around one explicitly identified inherited sensor."""
    world = Mock(id=17)
    world.get_settings.return_value = SimpleNamespace(
        synchronous_mode=False, no_rendering_mode=False
    )
    world.get_actors.return_value.find.return_value = sensor
    client = Mock()
    client.get_world.return_value = world
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", Mock(return_value=client))
    return adapter


def _collect(adapter: PythonCarlaAdapter, operation: str, output_dir: Path) -> dict[str, object]:
    if operation == "stream":
        return adapter.read_sensor_stream(sensor_id=7, frame_count=1, output_dir=output_dir)
    adapter.subscribe_sensor(7)
    try:
        return adapter.drain_sensor(7, 42, output_dir=output_dir)
    finally:
        adapter.close_sensor_subscription(7)


@pytest.mark.parametrize("operation", ["stream", "drain"])
@pytest.mark.parametrize(
    "sensor_case",
    [
        ("sensor.camera.rgb", PNG_BYTES, ".png"),
        ("sensor.camera.depth", PNG_BYTES, ".png"),
        ("sensor.lidar.ray_cast", PLY_BYTES, ".ply"),
        ("sensor.lidar.ray_cast_semantic", PLY_BYTES, ".ply"),
    ],
)
def test_stream_and_drain_save_the_known_sensor_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    sensor_case: tuple[str, bytes, str],
) -> None:
    """LiDAR must not be mislabeled PNG in either public collection path."""
    sensor_type, payload, suffix = sensor_case
    measurement = WritableFrame(payload)
    sensor = Sensor(sensor_type, measurement)

    report = _collect(sensor_adapter(monkeypatch, sensor), operation, tmp_path)

    expected = tmp_path / f"sensor-7-42{suffix}"
    assert report["paths"] == [str(expected)]
    assert expected.read_bytes() == payload
    assert measurement.writes == [(expected.absolute(), None)]
    assert sensor.stops == 1


@pytest.mark.parametrize("operation", ["stream", "drain"])
@pytest.mark.parametrize("sensor_type", ["sensor.other.gnss", "sensor.other.imu"])
def test_digest_only_frames_never_claim_a_nonexistent_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, sensor_type: str
) -> None:
    """A numerical digest is still returned when the frame has no writer."""
    report = _collect(
        sensor_adapter(monkeypatch, Sensor(sensor_type, DigestFrame())), operation, tmp_path
    )

    assert report["paths"] == []
    assert len(cast("list[object]", report["frames"])) == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["stream", "drain"])
def test_callable_writer_without_a_file_cannot_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """A silent writer failure cannot fabricate durable evidence."""
    adapter = sensor_adapter(
        monkeypatch, Sensor("sensor.camera.rgb", WritableFrame(write_output=False))
    )
    api = build_api(adapter, RunSnapshots())
    if operation == "stream":
        report = api.read_sensor_stream(7, 1, str(tmp_path))
    else:
        api.subscribe_sensor(7)
        report = api.drain_sensor(7, 42, output_dir=str(tmp_path))
        api.close_sensor_subscription(7)

    assert report["ok"] is False
    assert "file" in str(report["error"]).lower()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("payload", "filename", "mime_type"),
    [(PNG_BYTES, "raw.jpg", "image/png"), (JPEG_BYTES, "frame.png", "image/jpeg")],
)
def test_capture_mime_comes_from_written_bytes_not_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    filename: str,
    mime_type: str,
) -> None:
    """Capture metadata and publication must agree about the same image bytes."""
    adapter = sensor_adapter(monkeypatch, Sensor("sensor.camera.rgb", WritableFrame(payload)))
    snapshots = RunSnapshots()
    path = tmp_path / filename

    report = build_api(adapter, snapshots).capture_sensor_frame(7, str(path), publish=True)
    published = published_captures(
        {"carla-snapshot://captures/capture-000007": report}, tmp_path, text_bytes=0
    )

    assert report["mime_type"] == mime_type
    assert published[0].mime_type == mime_type


@pytest.mark.parametrize(
    "sensor_type", ["sensor.camera.depth", "sensor.camera.semantic_segmentation"]
)
@pytest.mark.parametrize("suffix", [".jpg", ".jpeg"])
def test_encoded_camera_capture_refuses_lossy_output_before_listen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sensor_type: str, suffix: str
) -> None:
    """Encoded channels cannot be preserved by a lossy native writer."""
    sensor = Sensor(sensor_type, WritableFrame())
    snapshots = RunSnapshots()

    report = build_api(sensor_adapter(monkeypatch, sensor), snapshots).capture_sensor_frame(
        7, str(tmp_path / f"raw{suffix}")
    )

    _assert_lossless_refusal(report, sensor)
    assert snapshots.snapshot_uris() == ()
    assert list(tmp_path.iterdir()) == []


def _assert_lossless_refusal(report: dict[str, object], sensor: Sensor) -> None:
    assert report["ok"] is False
    assert report["error_type"] == "capture_sensor_frame_failed"
    assert "lossless PNG" in str(report["error"])
    assert sensor.listens == 0


def test_screenshot_default_is_publication_sized(tmp_path: Path) -> None:
    """The temporary camera uses the bounded 480x270 publication default."""
    adapter = Mock()
    adapter.save_screenshot.return_value = {"capture_id": "screenshot", "path": "view.png"}

    build_api(adapter, RunSnapshots()).save_screenshot(str(tmp_path / "view.png"))

    assert adapter.save_screenshot.call_args.kwargs["attributes"] == {
        "image_size_x": "480",
        "image_size_y": "270",
    }


@pytest.mark.parametrize("sensor_type", ["sensor.lidar.ray_cast", "sensor.lidar.ray_cast_semantic"])
def test_single_lidar_capture_refuses_an_image_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sensor_type: str
) -> None:
    """A caller-selected PNG suffix cannot mislabel a native point-cloud file."""
    sensor = Sensor(sensor_type, WritableFrame(PLY_BYTES))

    report = build_api(sensor_adapter(monkeypatch, sensor), RunSnapshots()).capture_sensor_frame(
        7, str(tmp_path / "cloud.png")
    )

    assert report["ok"] is False
    assert ".ply" in str(report["error"])
    assert sensor.listens == 0
    assert list(tmp_path.iterdir()) == []


def test_single_lidar_capture_retains_its_durable_point_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw point cloud remains available as a file, not an MCP image."""
    path = tmp_path / "cloud.ply"
    sensor = Sensor("sensor.lidar.ray_cast", WritableFrame(PLY_BYTES))

    report = build_api(sensor_adapter(monkeypatch, sensor), RunSnapshots()).capture_sensor_frame(
        7, str(path)
    )

    assert report["path"] == str(path)
    assert report["mime_type"] == "application/octet-stream"
    assert path.read_bytes() == PLY_BYTES


@pytest.mark.parametrize("sensor_type", ["sensor.other.gnss", "sensor.other.imu"])
def test_single_digest_only_capture_returns_error_without_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sensor_type: str
) -> None:
    """Numerical frames use stream digests instead of nonexistent capture paths."""
    sensor = Sensor(sensor_type, DigestFrame())

    report = build_api(sensor_adapter(monkeypatch, sensor), RunSnapshots()).capture_sensor_frame(
        7, str(tmp_path / "frame.png")
    )

    assert report["ok"] is False
    assert report["error_type"] == "capture_sensor_frame_failed"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("operation", ["stream", "capture"])
def test_false_writer_acknowledgment_cannot_publish_a_preexisting_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """A native refusal cannot turn stale bytes into a new measurement record."""
    path = tmp_path / ("sensor-7-42.png" if operation == "stream" else "frame.png")
    path.write_bytes(PNG_BYTES)
    writer = Mock(return_value=False)
    measurement = SimpleNamespace(frame=42, timestamp=1.25, save_to_disk=writer)
    api = build_api(
        sensor_adapter(monkeypatch, Sensor("sensor.camera.rgb", measurement)), RunSnapshots()
    )

    if operation == "stream":
        report = api.read_sensor_stream(7, 1, str(tmp_path))
    else:
        report = api.capture_sensor_frame(7, str(path), publish=True)

    _assert_failed_save_preserved_file(report, path)
    writer.assert_called_once_with(str(path.absolute()))


def _assert_failed_save_preserved_file(report: dict[str, object], path: Path) -> None:
    assert report["ok"] is False
    assert "save" in str(report["error"]).lower()
    assert path.read_bytes() == PNG_BYTES
