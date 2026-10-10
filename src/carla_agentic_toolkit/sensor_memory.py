"""Execution-wide reservations for bounded native sensor queue payloads."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.errors import CarlaAdapterError

if TYPE_CHECKING:
    from collections.abc import Buffer

    from carla_agentic_toolkit.carla_protocols import CarlaSensor

MAX_SENSOR_QUEUE_BYTES = 512 * 1024 * 1024
VARIABLE_SENSOR_QUEUE_BYTES = 16 * 1024 * 1024
SAMPLE_METADATA_BYTES = 256


class SensorQueueBudget:
    """Reserve queue ceilings atomically across one execution's listening sensors."""

    def __init__(self, limit: int = MAX_SENSOR_QUEUE_BYTES) -> None:
        """Allow a lower payload policy, never a value above the toolkit ceiling."""
        if type(limit) is not int or not 1 <= limit <= MAX_SENSOR_QUEUE_BYTES:
            message = f"Sensor queue byte budget must be an integer in 1..{MAX_SENSOR_QUEUE_BYTES}."
            raise CarlaAdapterError(message)
        self.limit = limit
        self._reserved = 0
        self._lock = threading.Lock()

    def reserve(self, sensor: CarlaSensor, capacity: int) -> QueueReservation:
        """Refuse oversized aggregate configurations before native Listen."""
        ceiling = camera_queue_bytes(sensor, capacity)
        if ceiling is None:
            ceiling = min(VARIABLE_SENSOR_QUEUE_BYTES, self.limit)
        with self._lock:
            if self._reserved + ceiling > self.limit:
                message = (
                    f"Sensor queue byte budget exceeded: {ceiling} requested, "
                    f"{self._reserved} reserved, {self.limit} available in total; "
                    "reduce image_size_x/image_size_y or capacity, or close other listeners."
                )
                raise CarlaAdapterError(message)
            self._reserved += ceiling
        return QueueReservation(self, ceiling)

    def release(self, ceiling: int) -> None:
        """Release a ceiling after callback acceptance has been frozen."""
        with self._lock:
            self._reserved -= ceiling


class QueueReservation:
    """A queue's byte ceiling, released exactly once even after failed Stop retries."""

    def __init__(self, budget: SensorQueueBudget, limit: int) -> None:
        """Retain the owning budget and its approved ceiling."""
        self.limit = limit
        self._budget = budget
        self._released = False

    def release(self) -> None:
        """Release under the owning subscription's queue lock."""
        if not self._released:
            self._budget.release(self.limit)
            self._released = True


def camera_queue_bytes(sensor: CarlaSensor, capacity: int) -> int | None:
    """Use native camera dimensions; legacy handles use the variable payload ceiling."""
    if not sensor.type_id.startswith("sensor.camera."):
        return None
    attributes = getattr(sensor, "attributes", None)
    if not isinstance(attributes, Mapping):
        return None
    if not {"image_size_x", "image_size_y"}.intersection(attributes):
        return None
    width = _dimension(attributes, "image_size_x")
    height = _dimension(attributes, "image_size_y")
    return width * height * 4 * capacity


def _dimension(attributes: Mapping[str, object], name: str) -> int:
    """Require an actual positive integral native attribute, not float coercion."""
    value = attributes.get(name)
    message = f"Sensor {name} must be a positive integer."
    if type(value) not in (str, int):
        raise CarlaAdapterError(message)
    try:
        result = int(cast("str | int", value))
    except ValueError as exc:
        raise CarlaAdapterError(message) from exc
    if result < 1:
        raise CarlaAdapterError(message)
    return result


def sample_payload_bytes(data: object) -> int:
    """Count BGRA cameras without copying raw bytes; count variable buffer payloads."""
    camera_bytes = _camera_payload_bytes(data)
    if camera_bytes is not None:
        return camera_bytes
    return _buffer_payload_bytes(getattr(data, "raw_data", None))


def _camera_payload_bytes(data: object) -> int | None:
    width, height = getattr(data, "width", None), getattr(data, "height", None)
    if type(width) is int and type(height) is int:
        return width * height * 4 if width > 0 and height > 0 else MAX_SENSOR_QUEUE_BYTES + 1
    return None


def _buffer_payload_bytes(raw: object) -> int:
    if raw is None:
        return SAMPLE_METADATA_BYTES
    try:
        return max(memoryview(cast("Buffer", raw)).nbytes, SAMPLE_METADATA_BYTES)
    except TypeError:
        return MAX_SENSOR_QUEUE_BYTES + 1
