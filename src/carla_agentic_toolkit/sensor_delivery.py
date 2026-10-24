"""Immutable measurement and drain evidence shared by native sensor consumers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReceivedSample:
    """A raw sample and its measured client arrival time."""

    data: object
    frame: int
    received_monotonic: float
    size_bytes: int

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
    no_sample_due: bool | None = None
    schedule: dict[str, object] | None = None

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
            "no_sample_due": self.no_sample_due,
            "schedule": self.schedule,
        }
