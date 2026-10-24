"""Observed sensor cadence evidence without inventing pre-subscription emission history."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaSensor, CarlaWorld


class SensorSchedule:
    """Confirm an integral configured cadence from two consistent native measurements."""

    def __init__(self, sensor: CarlaSensor, fixed_delta_seconds: float | None) -> None:
        """Retain nullable configuration evidence, not a guessed initial capture phase."""
        self.sensor_tick = _sensor_tick(sensor)
        self.fixed_delta_seconds = _fixed_step(fixed_delta_seconds)
        self.period_frames = _period_frames(self.sensor_tick, self.fixed_delta_seconds)
        self._first_frame: int | None = None
        self._last_frame: int | None = None
        self._last_timestamp: float | None = None
        self._phase_frame: int | None = None

    def observe(self, frame: int, data: object) -> None:
        """Reset contradicted phase evidence and confirm a consecutive configured interval."""
        timestamp = _measurement_timestamp(data)
        if self._restart_needed(frame, timestamp):
            self._first_frame = frame
            self._last_frame = None
            self._phase_frame = None
        self._confirm_phase(frame)
        self._last_frame = frame
        self._last_timestamp = timestamp

    def _restart_needed(self, frame: int, timestamp: float | None) -> bool:
        if self._last_frame is None:
            return True
        return (
            frame <= self._last_frame
            or not self._clock_consistent(frame, timestamp)
            or not self._cadence_consistent(frame)
        )

    def _clock_consistent(self, frame: int, timestamp: float | None) -> bool:
        if timestamp is None or self._last_timestamp is None:
            return False
        elapsed = timestamp - self._last_timestamp
        if elapsed <= 0:
            return False
        return self._matches_fixed_clock(frame, elapsed)

    def _matches_fixed_clock(self, frame: int, elapsed: float) -> bool:
        if self._last_frame is None:
            return False
        if self.fixed_delta_seconds is None:
            return True
        expected = (frame - self._last_frame) * self.fixed_delta_seconds
        return math.isclose(elapsed, expected, rel_tol=1e-5, abs_tol=1e-6)

    def _cadence_consistent(self, frame: int) -> bool:
        if self._last_frame is None:
            return False
        if self.period_frames is None:
            return True
        return (frame - self._last_frame) % self.period_frames == 0

    def _confirm_phase(self, frame: int) -> None:
        if self._last_frame is None or self.period_frames is None:
            return
        if frame - self._last_frame == self.period_frames:
            self._phase_frame = frame

    def no_sample_due(self, frame: int) -> bool | None:
        """Infer skipped frames only within an observed, still-consistent integral phase."""
        if self._phase_frame is None or self._first_frame is None or self.period_frames is None:
            return None
        if frame < self._first_frame:
            return None
        return (frame - self._phase_frame) % self.period_frames != 0

    def to_dict(self) -> dict[str, object]:
        """Keep the configured and observed evidence available beside scheduling status."""
        return {
            "sensor_tick": self.sensor_tick,
            "fixed_delta_seconds": self.fixed_delta_seconds,
            "period_frames": self.period_frames,
            "first_observed_frame": self._first_frame,
            "phase_frame": self._phase_frame,
        }


def observed_fixed_sensor_step(
    world: CarlaWorld,
    sensor: CarlaSensor,
    *,
    event_sensor: bool,
    after_settings_read: Callable[[], None],
) -> float | None:
    """Use a readable fixed step or leave scheduling unknown without failing a listener."""
    if event_sensor or _sensor_tick(sensor) is None:
        return None
    try:
        return _read_fixed_sensor_step(world)
    finally:
        after_settings_read()


def _read_fixed_sensor_step(world: CarlaWorld) -> float | None:
    try:
        return _fixed_step(getattr(world.get_settings(), "fixed_delta_seconds", None))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _sensor_tick(sensor: CarlaSensor) -> float | None:
    attributes = getattr(sensor, "attributes", None)
    if not isinstance(attributes, Mapping):
        return None
    value = attributes.get("sensor_tick")
    if type(value) not in (str, float, int):
        return None
    return _read_seconds(cast("str | float | int", value))


def _measurement_timestamp(data: object) -> float | None:
    value = getattr(data, "timestamp", None)
    if type(value) not in (float, int):
        return None
    return _read_seconds(cast("float | int", value))


def _fixed_step(value: object) -> float | None:
    if type(value) not in (float, int):
        return None
    result = _read_seconds(cast("float | int", value))
    return result or None


def _nonnegative_seconds(value: float) -> float | None:
    return value if math.isfinite(value) and value >= 0 else None


def _read_seconds(value: str | float) -> float | None:
    try:
        return _nonnegative_seconds(float(value))
    except (OverflowError, ValueError):
        return None


def _period_frames(sensor_tick: float | None, fixed_step: float | None) -> int | None:
    if sensor_tick is None:
        return None
    if sensor_tick == 0:
        return 1
    return _integral_period(sensor_tick, fixed_step)


def _integral_period(sensor_tick: float, fixed_step: float | None) -> int | None:
    if fixed_step is None:
        return None
    ratio = sensor_tick / fixed_step
    if not math.isfinite(ratio):
        return None
    period = round(ratio)
    if period < 1 or not math.isclose(ratio, period, rel_tol=0, abs_tol=1e-6):
        return None
    return period
