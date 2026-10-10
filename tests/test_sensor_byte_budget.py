"""Listening queues bound native payload bytes and keep default drains metadata-only."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api
from tests.test_sensor_evidence_formats import PNG_BYTES, WritableFrame

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

DEFAULT_CAPACITY = 32
SMALL_BYTE_BUDGET = 2048
OVERFLOW_DROPS = 2
MEASUREMENT_FRAME = 42
DEFAULT_BYTE_BUDGET = 512 * 1024 * 1024


@dataclass
class BudgetSensor:
    """Expose native string dimensions and deliver samples only on explicit emission."""

    id: int = 7
    type_id: str = "sensor.camera.rgb"
    attributes: dict[str, str] = field(
        default_factory=lambda: {"image_size_x": "1920", "image_size_y": "1080"}
    )
    callback: Callable[[object], None] | None = None
    listens: int = 0
    stops: int = 0
    listen_error: bool = False

    def listen(self, callback: Callable[[object], None]) -> None:
        """Model registration without creating or adopting any simulator actor."""
        self.listens += 1
        if self.listen_error:
            message = "listener unavailable"
            raise RuntimeError(message)
        self.callback = callback

    def stop(self) -> None:
        """Acknowledge Stop on the same handle."""
        self.stops += 1

    def emit(self, measurement: object) -> None:
        """Deliver native callback data synchronously for deterministic queue assertions."""
        assert self.callback is not None
        self.callback(measurement)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    sensors: list[BudgetSensor],
    *,
    sensor_queue_budget_bytes: int = DEFAULT_BYTE_BUDGET,
) -> PythonCarlaAdapter:
    world = Mock(id=17)
    world.get_settings.return_value = SimpleNamespace(
        synchronous_mode=False, no_rendering_mode=False
    )
    by_id = {sensor.id: sensor for sensor in sensors}
    world.get_actors.return_value.find.side_effect = by_id.get
    client = Mock()
    client.get_world.return_value = world
    adapter = PythonCarlaAdapter(sensor_queue_budget_bytes=sensor_queue_budget_bytes)
    monkeypatch.setattr(adapter, "_client", Mock(return_value=client))
    return adapter


def _frames(report: dict[str, object]) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", report["frames"])


def test_oversized_camera_configuration_is_structured_before_listen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count-limited 1080p x1024 must not allocate an 8 GiB queue."""
    sensor = BudgetSensor()
    adapter = _adapter(monkeypatch, [sensor])

    report = build_api(adapter, RunSnapshots()).subscribe_sensor(7, capacity=1024)

    assert report.get("ok") is False
    assert report["error_type"] == "subscribe_sensor_failed"
    assert "byte" in str(report["error"]).lower()
    assert sensor.listens == 0


def test_default_1080p_queue_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The documented default32 fits the execution budget without reducing capacity."""
    sensor = BudgetSensor()
    adapter = _adapter(monkeypatch, [sensor])
    report = adapter.subscribe_sensor(7)
    assert report["capacity"] == DEFAULT_CAPACITY
    assert sensor.listens == 1
    adapter.close_sensor_subscription(7)


def test_camera_reservations_sum_and_release_on_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three 1080p queues cannot each independently claim the same 512 MiB budget."""
    sensors = [BudgetSensor(id=identity) for identity in (7, 8, 9)]
    adapter = _adapter(monkeypatch, sensors)
    adapter.subscribe_sensor(7)
    adapter.subscribe_sensor(8)
    with pytest.raises(CarlaAdapterError, match="byte"):
        adapter.subscribe_sensor(9)
    assert sensors[2].listens == 0
    adapter.close_sensor_subscription(7)
    adapter.subscribe_sensor(9)
    assert sensors[2].listens == 1
    adapter.close_sensor_subscriptions()


@pytest.mark.parametrize("capacity", [0, 1025, True, 1.5])
def test_invalid_capacity_refused_before_connection(
    monkeypatch: pytest.MonkeyPatch, capacity: int
) -> None:
    """Refuse invalid local queue limits before touching CARLA."""
    adapter = PythonCarlaAdapter()
    client = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(adapter, "_client", client)
    with pytest.raises(CarlaAdapterError, match="capacity"):
        adapter.subscribe_sensor(7, capacity=capacity)
    client.assert_not_called()


@pytest.mark.parametrize("dimension", ["", "0", "-1", "not-an-integer"])
def test_invalid_native_camera_dimensions_refused_before_listen(
    monkeypatch: pytest.MonkeyPatch, dimension: str
) -> None:
    """Never reinterpret a present invalid dimension as an unbounded legacy handle."""
    sensor = BudgetSensor(attributes={"image_size_x": dimension, "image_size_y": "1080"})
    adapter = _adapter(monkeypatch, [sensor])
    with pytest.raises(CarlaAdapterError, match="image_size"):
        adapter.subscribe_sensor(7)
    assert sensor.listens == 0


def test_variable_sensor_queue_drops_by_bytes_not_only_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LiDAR payloads retain the newest bounded sample, never a giant JSON array."""
    sensor = BudgetSensor(type_id="sensor.lidar.ray_cast", attributes={})
    adapter = _adapter(monkeypatch, [sensor], sensor_queue_budget_bytes=2048)
    adapter.subscribe_sensor(7, capacity=32)
    sensor.emit(SimpleNamespace(frame=1, raw_data=bytes(1400)))
    sensor.emit(SimpleNamespace(frame=2, raw_data=bytes(1400)))
    sensor.emit(SimpleNamespace(frame=3, raw_data=bytes(3000)))

    report = adapter.drain_sensor(7, 3)

    assert [item["frame"] for item in _frames(report)] == [2]
    assert report["dropped_samples"] == OVERFLOW_DROPS
    assert len(json.dumps(report)) < SMALL_BYTE_BUDGET
    assert "raw_data" not in json.dumps(report)
    adapter.close_sensor_subscription(7)


def test_actual_camera_bytes_cannot_exceed_reserved_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drop a measurement whose true dimensions exceed the queue reservation."""
    sensor = BudgetSensor(attributes={"image_size_x": "2", "image_size_y": "2"})
    adapter = _adapter(monkeypatch, [sensor])
    adapter.subscribe_sensor(7, capacity=2)
    sensor.emit(SimpleNamespace(frame=1, width=4, height=4, raw_data=b""))
    report = adapter.drain_sensor(7, 1)
    assert report["frames"] == []
    assert report["dropped_samples"] == 1
    adapter.close_sensor_subscription(7)


def test_failed_listener_releases_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not leak reservation capacity after a refused native registration."""
    sensor = BudgetSensor(listen_error=True)
    adapter = _adapter(monkeypatch, [sensor], sensor_queue_budget_bytes=1920 * 1080 * 4 * 32)
    with pytest.raises(CarlaAdapterError, match="listener unavailable"):
        adapter.subscribe_sensor(7)
    sensor.listen_error = False
    adapter.subscribe_sensor(7)
    adapter.close_sensor_subscription(7)


def test_output_directory_alone_does_not_encode_on_drain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An output directory alone must not request costly image encoding."""
    sensor = BudgetSensor()
    adapter = _adapter(monkeypatch, [sensor])
    adapter.subscribe_sensor(7)
    measurement = WritableFrame(PNG_BYTES)
    sensor.emit(measurement)

    report = build_api(adapter, RunSnapshots()).drain_sensor(
        7, 42, output_dir=str(tmp_path / "unused")
    )

    assert report["paths"] == []
    assert measurement.writes == []
    assert not (tmp_path / "unused").exists()
    assert _frames(report)[0]["frame"] == MEASUREMENT_FRAME
    adapter.close_sensor_subscription(7)


def test_explicit_drain_save_retains_lossless_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep issue123's raw PNG contract when persistence is explicitly requested."""
    sensor = BudgetSensor()
    adapter = _adapter(monkeypatch, [sensor])
    adapter.subscribe_sensor(7)
    measurement = WritableFrame(PNG_BYTES)
    sensor.emit(measurement)

    report = build_api(adapter, RunSnapshots()).drain_sensor(
        7, 42, output_dir=str(tmp_path), save_frames=True
    )

    expected = tmp_path / "sensor-7-42.png"
    assert report["paths"] == [str(expected)]
    assert expected.read_bytes() == PNG_BYTES
    assert measurement.writes == [(expected.absolute(), None)]
    adapter.close_sensor_subscription(7)


@pytest.mark.parametrize("budget", [0, -1, True, 512 * 1024 * 1024 + 1])
def test_invalid_execution_budget_rejected_without_connection(budget: int) -> None:
    """Lower limits are allowed, invalid or higher limits are not."""
    with pytest.raises(CarlaAdapterError, match="byte"):
        PythonCarlaAdapter(sensor_queue_budget_bytes=budget)


class StreamingSensor(BudgetSensor):
    """Deliver frames separately so one-shot collection retains both outside the queue."""

    producer: threading.Thread

    def listen(self, callback: Callable[[object], None]) -> None:
        """Start a small producer after registering the callback."""
        super().listen(callback)
        self.producer = threading.Thread(target=self._produce)
        self.producer.start()

    def _produce(self) -> None:
        self.emit(SimpleNamespace(frame=1, raw_data=bytes(1400)))
        time.sleep(0.05)
        self.emit(SimpleNamespace(frame=2, raw_data=bytes(1400)))


def test_one_shot_collection_cannot_escape_its_byte_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count raw frames retained by the collector as well as its pending queue."""
    sensor = StreamingSensor(type_id="sensor.lidar.ray_cast", attributes={})
    adapter = _adapter(monkeypatch, [sensor], sensor_queue_budget_bytes=2048)
    try:
        report = build_api(adapter, RunSnapshots()).read_sensor_stream(7, frames=2)
        assert report.get("ok") is False
        assert "byte" in str(report["error"])
        assert sensor.stops == 1
    finally:
        sensor.producer.join(timeout=1)


def test_closed_subscription_does_not_inspect_late_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frozen queues ignore payloads before any native byte-property access."""

    class LateFrame:
        frame = 1

        @property
        def raw_data(self) -> bytes:
            message = "closed callback must not copy payload"
            raise AssertionError(message)

    sensor = BudgetSensor(type_id="sensor.lidar.ray_cast", attributes={})
    adapter = _adapter(monkeypatch, [sensor])
    adapter.subscribe_sensor(7)
    adapter.close_sensor_subscription(7)
    sensor.emit(LateFrame())
