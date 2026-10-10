"""Bounded sensor listeners shared by synchronous owners and asynchronous readers."""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.sensor_delivery import ReceivedSample, SensorDrain
from carla_agentic_toolkit.sensor_memory import SensorQueueBudget, sample_payload_bytes
from carla_agentic_toolkit.sensor_schedule import SensorSchedule

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaSensor

MAX_SENSOR_QUEUE = 1024
MAX_SENSOR_WAIT_SECONDS = 30.0
DEFAULT_SENSOR_DRAIN_SECONDS = 2.0
EVENT_SENSOR_TYPES = frozenset(
    {"sensor.other.collision", "sensor.other.lane_invasion", "sensor.other.obstacle"}
)


class SensorSubscription:
    """Listen now; let one caller own ticks; drain observations by frame; close."""

    def __init__(
        self,
        sensor: CarlaSensor,
        *,
        event_sensor: bool = False,
        capacity: int = 32,
        byte_budget: SensorQueueBudget | None = None,
        fixed_delta_seconds: float | None = None,
    ) -> None:
        """Install a listener with bounded storage and no producer backpressure."""
        validate_capacity(capacity)
        self._sensor = sensor
        self.event_sensor = event_sensor
        self._samples: deque[ReceivedSample] = deque(maxlen=capacity)
        self._condition = threading.Condition()
        self._close_lock = threading.Lock()
        self._closed = False
        self._stop_acknowledged = False
        self._dropped = 0
        self._queued_bytes = 0
        self._schedule = SensorSchedule(sensor, fixed_delta_seconds)
        self._reservation = (byte_budget or SensorQueueBudget()).reserve(sensor, capacity)
        self._listen()

    @property
    def dropped_samples(self) -> int:
        """Read cumulative invalid or overwritten delivery counts, including after close."""
        with self._condition:
            return self._dropped

    @property
    def pending_samples(self) -> int:
        """Read the bounded queue size without consuming data or requiring an open listener."""
        with self._condition:
            return len(self._samples)

    def _listen(self) -> None:
        """Normalize listener startup errors and release any partial registration."""
        try:
            self._sensor.listen(self._receive)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            with self._condition:
                self._freeze()
            with contextlib.suppress(AttributeError, RuntimeError, TypeError, ValueError):
                self._sensor.stop()
            raise CarlaAdapterError(str(exc)) from exc

    def _receive(self, data: object) -> None:
        """Drop the oldest sample on overflow; never wait for buffer space."""
        with self._condition:
            if self._closed:
                return
            sample = _received_sample(data)
            self._observe_schedule(sample)
            self._enqueue(sample)
            self._condition.notify_all()

    def _observe_schedule(self, sample: ReceivedSample | None) -> None:
        if sample is not None and not self.event_sensor:
            self._schedule.observe(sample.frame, sample.data)

    def _enqueue(self, sample: ReceivedSample | None) -> None:
        """Account for invalid or overwritten samples under the queue lock."""
        if sample is None or sample.size_bytes > self._reservation.limit:
            self._dropped += 1
            return
        while self._queue_full(sample.size_bytes):
            self._queued_bytes -= self._samples.popleft().size_bytes
            self._dropped += 1
        self._samples.append(sample)
        self._queued_bytes += sample.size_bytes

    def _queue_full(self, size_bytes: int) -> bool:
        return (
            len(self._samples) == self._samples.maxlen
            or self._queued_bytes + size_bytes > self._reservation.limit
        )

    def drain(
        self, frame: int, *, timeout_seconds: float = DEFAULT_SENSOR_DRAIN_SECONDS
    ) -> SensorDrain:
        """Drain frames up to the owner's frame, retaining future frames; never tick."""
        _validate_frame(frame)
        _validate_timeout(timeout_seconds)
        with self._condition:
            self._require_open()
            if self._should_wait(frame):
                self._wait(frame, timeout_seconds)
            self._require_open()
            return self._drain_ready(frame)

    def _should_wait(self, frame: int) -> bool:
        return not self.event_sensor and self._schedule.no_sample_due(frame) is not True

    def _wait(self, frame: int, timeout_seconds: float) -> None:
        """Wait only for callback delivery of an already requested periodic frame."""
        self._condition.wait_for(
            lambda: self._closed or self._has_frame(frame), timeout=timeout_seconds
        )

    def _has_frame(self, frame: int) -> bool:
        """Stop waiting when delivery has reached or passed the requested measurement frame."""
        return any(sample.frame >= frame for sample in self._samples)

    def _drain_ready(self, frame: int) -> SensorDrain:
        """Partition available data while preserving bounded future observations."""
        ready = self._take_ready(frame)
        return self._drain_result(frame, ready)

    def _drain_result(self, frame: int, ready: tuple[ReceivedSample, ...]) -> SensorDrain:
        """Describe a regular drain or the final listener cutoff using identical metadata."""
        no_sample_due = self._no_sample_due(frame, ready)
        return SensorDrain(
            requested_frame=frame,
            samples=ready,
            event_sensor=self.event_sensor,
            timed_out=self._periodic_missing(frame, ready, no_sample_due=no_sample_due),
            dropped_samples=self._dropped,
            pending_samples=len(self._samples),
            drained_at=time.monotonic(),
            no_sample_due=no_sample_due,
            schedule=None if self.event_sensor else self._schedule.to_dict(),
        )

    def _take_ready(self, frame: int) -> tuple[ReceivedSample, ...]:
        """Retain future observations in the same capacity-limited queue."""
        ready = tuple(sample for sample in self._samples if sample.frame <= frame)
        future = tuple(sample for sample in self._samples if sample.frame > frame)
        self._samples.clear()
        self._samples.extend(future)
        self._queued_bytes = _sample_bytes(future)
        return ready

    def _no_sample_due(self, frame: int, ready: tuple[ReceivedSample, ...]) -> bool | None:
        if self.event_sensor:
            return None
        if any(sample.frame == frame for sample in ready):
            return False
        return self._schedule.no_sample_due(frame)

    def _periodic_missing(
        self,
        frame: int,
        ready: tuple[ReceivedSample, ...],
        *,
        no_sample_due: bool | None,
    ) -> bool:
        """Treat empty event batches as normal and missing periodic frames as timeouts."""
        if self.event_sensor or no_sample_due is True:
            return False
        return not any(sample.frame == frame for sample in ready)

    def next_frame(self, *, timeout_seconds: float = 5.0) -> object:
        """Wait for the next asynchronous sample without advancing the simulator."""
        return self.next_sample(timeout_seconds=timeout_seconds).data

    @property
    def byte_limit(self) -> int:
        """Expose the reservation for bounded one-shot consumers of raw samples."""
        return self._reservation.limit

    def next_sample(self, *, timeout_seconds: float = 5.0) -> ReceivedSample:
        """Retain measured byte size when consuming a raw asynchronous sample."""
        _validate_timeout(timeout_seconds)
        with self._condition:
            self._require_open()
            self._condition.wait_for(lambda: self._closed or bool(self._samples), timeout_seconds)
            self._require_open()
            if not self._samples:
                message = "Timed out waiting for a sensor frame."
                raise CarlaAdapterError(message)
            sample = self._samples.popleft()
            self._queued_bytes -= sample.size_bytes
            return sample

    def close(self) -> None:
        """Freeze queued data and retry upstream Stop until its return is acknowledged."""
        with self._close_lock:
            with self._condition:
                if not self._closed:
                    self._freeze()
            self._stop()

    def close_and_drain(self, frame: int) -> SensorDrain:
        """Stop upstream, then freeze and return all bounded trailing samples without ticks."""
        _validate_frame(frame)
        with self._close_lock:
            try:
                self._stop()
            finally:
                with self._condition:
                    ready = tuple(self._samples)
                    self._freeze()
                    result = self._drain_result(frame, ready)
            return result

    def _freeze(self) -> None:
        """Close callback acceptance under the queue lock and wake waiting consumers."""
        self._closed = True
        self._samples.clear()
        self._queued_bytes = 0
        self._reservation.release()
        self._condition.notify_all()

    def _stop(self) -> None:
        """Call upstream outside the queue lock so final callbacks cannot deadlock."""
        if self._stop_acknowledged:
            return
        try:
            self._sensor.stop()
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        self._stop_acknowledged = True

    def _require_open(self) -> None:
        """Reject use after listener cleanup."""
        if self._closed:
            message = "Sensor subscription is closed."
            raise CarlaAdapterError(message)


def validate_capacity(capacity: int) -> None:
    """Require a bounded positive queue or collection size."""
    if type(capacity) is not int or not 1 <= capacity <= MAX_SENSOR_QUEUE:
        message = f"Sensor capacity must be an integer in 1..{MAX_SENSOR_QUEUE}."
        raise CarlaAdapterError(message)


def _validate_frame(frame: int) -> None:
    """Require an explicit nonnegative simulator frame."""
    if type(frame) is not int or frame < 0:
        message = "Sensor drain frame must be a nonnegative integer."
        raise CarlaAdapterError(message)


def _validate_timeout(seconds: float) -> None:
    """Reject unbounded or negative listener waits."""
    if not math.isfinite(seconds) or not 0 <= seconds <= MAX_SENSOR_WAIT_SECONDS:
        message = f"Sensor timeout must be finite and in 0..{MAX_SENSOR_WAIT_SECONDS} seconds."
        raise CarlaAdapterError(message)


def _received_sample(data: object) -> ReceivedSample | None:
    """Ignore malformed callback data while keeping drop accounting truthful."""
    frame = getattr(data, "frame", None)
    if type(frame) is not int or frame < 0:
        return None
    return ReceivedSample(data, frame, time.monotonic(), sample_payload_bytes(data))


def _sample_bytes(samples: tuple[ReceivedSample, ...]) -> int:
    """Recalculate retained bytes after partitioning a queue by measurement frame."""
    return sum(sample.size_bytes for sample in samples)
