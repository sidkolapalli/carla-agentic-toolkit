"""Bounded sensor listeners shared by synchronous owners and asynchronous readers."""

from __future__ import annotations

import contextlib
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import CarlaAdapterError

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaSensor

MAX_SENSOR_QUEUE = 1024
MAX_SENSOR_WAIT_SECONDS = 30.0
EVENT_SENSOR_TYPES = frozenset(
    {"sensor.other.collision", "sensor.other.lane_invasion", "sensor.other.obstacle"}
)


@dataclass(frozen=True)
class ReceivedSample:
    """A raw sample and its measured client arrival time."""

    data: object
    frame: int
    received_monotonic: float

    def metadata(self, owner_frame: int, drained_at: float) -> dict[str, object]:
        """Report simulation lag separately from client queue residence."""
        return {
            "frame": self.frame,
            "timestamp": getattr(self.data, "timestamp", None),
            "frame_lag": owner_frame - self.frame,
            "received_monotonic": self.received_monotonic,
            "queue_latency_seconds": max(drained_at - self.received_monotonic, 0.0),
        }


@dataclass(frozen=True)
class SensorDrain:
    """One bounded drain result with raw frames for trusted consumers."""

    requested_frame: int
    samples: tuple[ReceivedSample, ...]
    event_sensor: bool
    timed_out: bool
    dropped_samples: int
    pending_samples: int
    drained_at: float

    @property
    def frames(self) -> tuple[object, ...]:
        """Expose CARLA data without losing the corresponding frame metadata."""
        return tuple(sample.data for sample in self.samples)

    def to_dict(self) -> dict[str, object]:
        """Return JSON-compatible delivery and timeout evidence."""
        return {
            "requested_frame": self.requested_frame,
            "samples": [
                sample.metadata(self.requested_frame, self.drained_at) for sample in self.samples
            ],
            "event_sensor": self.event_sensor,
            "timed_out": self.timed_out,
            "dropped_samples": self.dropped_samples,
            "pending_samples": self.pending_samples,
        }


class SensorSubscription:
    """Listen now; let one caller own ticks; drain observations by frame; close."""

    def __init__(
        self, sensor: CarlaSensor, *, event_sensor: bool = False, capacity: int = 32
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
        self._listen()

    def _listen(self) -> None:
        """Normalize listener startup errors and release any partial registration."""
        try:
            self._sensor.listen(self._receive)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            self._closed = True
            with contextlib.suppress(AttributeError, RuntimeError, TypeError, ValueError):
                self._sensor.stop()
            raise CarlaAdapterError(str(exc)) from exc

    def _receive(self, data: object) -> None:
        """Drop the oldest sample on overflow; never wait for buffer space."""
        sample = _received_sample(data)
        with self._condition:
            if self._closed:
                return
            self._enqueue(sample)
            self._condition.notify_all()

    def _enqueue(self, sample: ReceivedSample | None) -> None:
        """Account for invalid or overwritten samples under the queue lock."""
        if sample is None:
            self._dropped += 1
            return
        if len(self._samples) == self._samples.maxlen:
            self._dropped += 1
        self._samples.append(sample)

    def drain(self, frame: int, *, timeout_seconds: float = 0.0) -> SensorDrain:
        """Drain frames up to the owner's frame, retaining future frames; never tick."""
        _validate_frame(frame)
        _validate_timeout(timeout_seconds)
        with self._condition:
            self._require_open()
            if not self.event_sensor:
                self._wait(frame, timeout_seconds)
            self._require_open()
            return self._drain_ready(frame)

    def _wait(self, frame: int, timeout_seconds: float) -> None:
        """Wait only for callback delivery of an already requested periodic frame."""
        self._condition.wait_for(
            lambda: self._closed or self._has_frame(frame), timeout=timeout_seconds
        )

    def _has_frame(self, frame: int) -> bool:
        """Return whether a requested periodic frame has arrived."""
        return any(sample.frame == frame for sample in self._samples)

    def _drain_ready(self, frame: int) -> SensorDrain:
        """Partition available data while preserving bounded future observations."""
        ready = self._take_ready(frame)
        return self._drain_result(frame, ready)

    def _drain_result(self, frame: int, ready: tuple[ReceivedSample, ...]) -> SensorDrain:
        """Describe a regular drain or the final listener cutoff using identical metadata."""
        return SensorDrain(
            requested_frame=frame,
            samples=ready,
            event_sensor=self.event_sensor,
            timed_out=self._periodic_missing(frame, ready),
            dropped_samples=self._dropped,
            pending_samples=len(self._samples),
            drained_at=time.monotonic(),
        )

    def _take_ready(self, frame: int) -> tuple[ReceivedSample, ...]:
        """Retain future observations in the same capacity-limited queue."""
        ready = tuple(sample for sample in self._samples if sample.frame <= frame)
        future = tuple(sample for sample in self._samples if sample.frame > frame)
        self._samples.clear()
        self._samples.extend(future)
        return ready

    def _periodic_missing(self, frame: int, ready: tuple[ReceivedSample, ...]) -> bool:
        """Treat empty event batches as normal and missing periodic frames as timeouts."""
        return not self.event_sensor and not any(sample.frame == frame for sample in ready)

    def next_frame(self, *, timeout_seconds: float = 5.0) -> object:
        """Wait for the next asynchronous sample without advancing the simulator."""
        _validate_timeout(timeout_seconds)
        with self._condition:
            self._require_open()
            self._condition.wait_for(lambda: self._closed or bool(self._samples), timeout_seconds)
            self._require_open()
            if not self._samples:
                message = "Timed out waiting for a sensor frame."
                raise CarlaAdapterError(message)
            return self._samples.popleft().data

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
    return ReceivedSample(data, frame, time.monotonic())
