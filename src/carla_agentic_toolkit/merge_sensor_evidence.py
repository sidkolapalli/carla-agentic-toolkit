"""Numerical event/periodic evidence for the managed merge sensor listeners."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from carla_agentic_toolkit.sensor_subscription import SensorDrain, SensorSubscription


@dataclass(frozen=True, slots=True)
class MergeSensor:
    """One owned listener and its role/type identity."""

    sensor_id: int
    actor_id: int
    role: str
    kind: str
    subscription: SensorSubscription

    def drain(self, frame: int, *, trailing: bool = False) -> dict[str, object]:
        """Collect existing events without waiting for a collision or owning a tick."""
        drained = self.subscription.drain(frame, timeout_seconds=0.0)
        return self._evidence(drained, frame, trailing=trailing)

    def close(self, frame: int) -> dict[str, object]:
        """Stop upstream before retaining the final bounded callback batch."""
        return self._evidence(self.subscription.close_and_drain(frame), frame, trailing=True)

    def _evidence(self, drained: SensorDrain, frame: int, *, trailing: bool) -> dict[str, object]:
        payload = drained.to_dict()
        samples = [
            {
                **sample.metadata(frame, drained.drained_at),
                **measurement_payload(self.kind, sample.data),
                "trailing": trailing,
            }
            for sample in drained.samples
        ]
        return {
            **payload,
            "samples": samples,
            "sensor_id": self.sensor_id,
            "actor_id": self.actor_id,
            "role": self.role,
            "kind": self.kind,
            "trailing": trailing,
        }


def measurement_payload(kind: str, measurement: object) -> dict[str, object]:
    """Keep original physical event fields alongside sensor measurement frames."""
    value = cast("Any", measurement)
    common: dict[str, object] = {"measurement_frame": int(value.frame)}
    handlers = {"collision": _collision, "lane_invasion": _lane_invasion, "gnss": _gnss}
    return {**common, **handlers[kind](value)}


def _collision(value: object) -> dict[str, object]:
    measurement = cast("Any", value)
    return {
        "collision_impulse": _vector(measurement.normal_impulse),
        "other_actor_id": int(measurement.other_actor.id),
    }


def _lane_invasion(value: object) -> dict[str, object]:
    markings = [str(item.type) for item in cast("Any", value).crossed_lane_markings]
    return {"lane_invasion": {"count": len(markings), "markings": markings}}


def _gnss(value: object) -> dict[str, object]:
    measurement = cast("Any", value)
    return {
        "latitude": float(measurement.latitude),
        "longitude": float(measurement.longitude),
        "altitude": float(measurement.altitude),
    }


def _vector(value: object) -> dict[str, float]:
    vector = cast("Any", value)
    return {"x": float(vector.x), "y": float(vector.y), "z": float(vector.z)}
