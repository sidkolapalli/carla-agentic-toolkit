"""Deferred delivery waits, bounded owner drains, and evidence-based sensor schedules."""

from __future__ import annotations

import inspect
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, TypedDict, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.merge_sensor_evidence import MergeSensor
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.sensor_subscription import SensorSubscription
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api
from tests.test_sensor_byte_budget import BudgetSensor

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaSensor

FIXED_STEP = 0.05
DEFAULT_WAIT = 2.0
PROMPT_LIMIT = 0.5
BOUNDARIES = ("subscription", "adapter", "facade")


class _DrainOptions(TypedDict, total=False):
    timeout_seconds: float


def _measurement(frame: int, timestamp: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        frame=frame,
        timestamp=frame * FIXED_STEP if timestamp is None else timestamp,
        latitude=1.0,
        longitude=2.0,
        altitude=3.0,
    )


@dataclass
class DeliveryCase:
    """Exercise the real public boundary with a native-like retained listener."""

    boundary: str
    sensor: BudgetSensor
    adapter: PythonCarlaAdapter
    api: CarlaScriptApi
    subscription: SensorSubscription
    world: Mock
    timers: list[threading.Timer] = field(default_factory=list)

    def drain(self, frame: int, timeout: float | None = None) -> dict[str, object]:
        """Leave the timeout argument absent when checking each boundary's default."""
        options: _DrainOptions = {} if timeout is None else {"timeout_seconds": timeout}
        if self.boundary == "subscription":
            return self.subscription.drain(frame, **options).to_dict()
        if self.boundary == "adapter":
            return self.adapter.drain_sensor(7, frame, **options)
        return self.api.drain_sensor(7, frame, **options)

    def tick_with_delayed_delivery(self, requested: int, delivered: int) -> None:
        """Publish a callback from a timer only after the explicit owner tick returned."""
        self.world.tick.return_value = requested
        frame = self.api.tick()["frame"] if self.boundary == "facade" else self.adapter.tick()
        assert frame == requested
        timer = threading.Timer(0.02, self.sensor.emit, args=(_measurement(delivered),))
        self.timers.append(timer)
        timer.start()

    def close(self) -> None:
        """Reap timer threads before closing the original listener handle."""
        for timer in self.timers:
            timer.join(timeout=1)
        if self.boundary == "subscription":
            self.subscription.close()
        else:
            self.adapter.close_sensor_subscription(7)


def _case(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    *,
    sensor_type: str = "sensor.other.gnss",
    sensor_tick: str = "0.0",
) -> DeliveryCase:
    attributes = {"sensor_tick": sensor_tick, "image_size_x": "480", "image_size_y": "270"}
    sensor = BudgetSensor(type_id=sensor_type, attributes=attributes)
    world = Mock(id=17)
    world.get_settings.return_value = SimpleNamespace(
        synchronous_mode=True, no_rendering_mode=False, fixed_delta_seconds=FIXED_STEP
    )
    world.get_actors.return_value.find.return_value = sensor
    client = Mock()
    client.get_world.return_value = world
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", Mock(return_value=client))
    api = build_api(adapter, RunSnapshots())
    event_sensor = sensor_type in {"sensor.other.collision", "sensor.other.lane_invasion"}
    if boundary == "subscription":
        subscription = SensorSubscription(cast("CarlaSensor", sensor), event_sensor=event_sensor)
    else:
        api.subscribe_sensor(7)
        subscription = adapter._sensor_subscriptions[7]  # noqa: SLF001 -- Verify real queue waits.
    return DeliveryCase(boundary, sensor, adapter, api, subscription, world)


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("sensor_type", ["sensor.camera.rgb", "sensor.other.gnss"])
def test_default_drain_receives_callback_after_owner_tick_returns(
    monkeypatch: pytest.MonkeyPatch, boundary: str, sensor_type: str
) -> None:
    """Allow asynchronous native delivery after the owner tick is acknowledged."""
    case = _case(monkeypatch, boundary, sensor_type=sensor_type)
    try:
        case.tick_with_delayed_delivery(11, 11)
        report = case.drain(11)
        assert report["timed_out"] is False
        assert _sample_frames(report) == [11]
        case.world.tick.assert_called_once()
    finally:
        case.close()


@pytest.mark.parametrize(
    "method",
    [SensorSubscription.drain, PythonCarlaAdapter.drain_sensor, CarlaScriptApi.drain_sensor],
)
def test_periodic_default_is_two_seconds_at_every_boundary(
    method: Callable[..., object],
) -> None:
    """The declared default is positive and remains inside the existing30-second cap."""
    assert inspect.signature(method).parameters["timeout_seconds"].default == DEFAULT_WAIT


@pytest.mark.parametrize("boundary", BOUNDARIES)
def test_later_frame_ends_wait_without_relabeling_or_consuming_future_data(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """An absent exact frame cannot arrive after a later stream frame has already arrived."""
    case = _case(monkeypatch, boundary)
    try:
        case.tick_with_delayed_delivery(11, 12)
        started = time.monotonic()
        report = case.drain(11, timeout=1.0)
        assert time.monotonic() - started < PROMPT_LIMIT
        assert report["timed_out"] is True
        assert _sample_frames(report) == []
        assert _sample_frames(case.drain(12, timeout=0)) == [12]
        case.world.tick.assert_called_once()
    finally:
        case.close()


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("sensor_type", ["sensor.other.collision", "sensor.other.lane_invasion"])
def test_event_sensors_never_wait_even_with_positive_default(
    monkeypatch: pytest.MonkeyPatch, boundary: str, sensor_type: str
) -> None:
    """No collision or lane event is a normal empty batch, not a missing periodic sample."""
    case = _case(monkeypatch, boundary, sensor_type=sensor_type)
    wait = Mock(side_effect=AssertionError("event sensors cannot wait"))
    monkeypatch.setattr(case.subscription, "_wait", wait)
    try:
        assert case.drain(11)["timed_out"] is False
        wait.assert_not_called()
        case.world.tick.assert_not_called()
    finally:
        case.close()


@pytest.mark.parametrize("boundary", ["adapter", "facade"])
def test_observed_double_step_cadence_skips_unscheduled_frames_without_waiting(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """Two phase-consistent measurements establish an integral cadence, not physics alignment."""
    case = _case(monkeypatch, boundary, sensor_tick="0.1")
    try:
        _observe_phase(case)
        wait = Mock(side_effect=AssertionError("unscheduled frame must not wait"))
        monkeypatch.setattr(case.subscription, "_wait", wait)
        report = case.drain(13)
        assert report["no_sample_due"] is True
        assert report["timed_out"] is False
        assert _sample_frames(report) == []
        wait.assert_not_called()
        case.world.tick.assert_not_called()
    finally:
        case.close()


def _observe_phase(case: DeliveryCase) -> None:
    for frame in (10, 12):
        case.sensor.emit(_measurement(frame))
        assert _sample_frames(case.drain(frame, timeout=0)) == [frame]


@pytest.mark.parametrize("stage", ["empty", "first_later", "pre_anchor_after_phase"])
def test_pre_anchor_history_is_unknown_not_fabricated_as_unscheduled(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    """The first received callback is not proof no earlier capture existed."""
    case = _case(monkeypatch, "adapter", sensor_tick="0.1")
    try:
        _observe_history_stage(case, stage)
        report = case.drain(9, timeout=0)
        assert report["no_sample_due"] is None
        assert report["timed_out"] is True
        assert _sample_frames(report) == []
    finally:
        case.close()


def _observe_history_stage(case: DeliveryCase, stage: str) -> None:
    if stage == "first_later":
        case.sensor.emit(_measurement(10))
    if stage == "pre_anchor_after_phase":
        _observe_phase(case)


def test_missing_due_frame_after_queue_overflow_is_not_marked_unscheduled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropped samples remain missing evidence, with the cumulative drop counter preserved."""
    case = _case(monkeypatch, "adapter", sensor_tick="0.1")
    case.adapter.close_sensor_subscription(7)
    case.adapter.subscribe_sensor(7, capacity=1)
    try:
        case.sensor.emit(_measurement(10))
        case.sensor.emit(_measurement(12))
        report = case.drain(10, timeout=0)
        assert report["no_sample_due"] is False
        assert report["timed_out"] is True
        assert report["dropped_samples"] == 1
        assert _sample_frames(case.drain(12, timeout=0)) == [12]
    finally:
        case.close()


@pytest.mark.parametrize("change", ["cadence", "clock"])
def test_changed_cadence_or_clock_makes_scheduling_unknown(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Do not retain a phase after contradictory native measurements arrive."""
    case = _case(monkeypatch, "adapter", sensor_tick="0.1")
    try:
        _observe_phase(case)
        frame = 13 if change == "cadence" else 14
        timestamp = None if change == "cadence" else 9.0
        case.sensor.emit(_measurement(frame, timestamp))
        case.drain(frame, timeout=0)
        report = case.drain(frame + 1, timeout=0)
        assert report["no_sample_due"] is None
        assert report["timed_out"] is True
    finally:
        case.close()


def test_nonintegral_cadence_is_unknown_not_rounded_to_a_frame_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A1.5-frame interval cannot justify an invented two-frame phase."""
    case = _case(monkeypatch, "adapter", sensor_tick="0.075")
    try:
        _observe_phase(case)
        assert case.drain(13, timeout=0)["no_sample_due"] is None
    finally:
        case.close()


@pytest.mark.parametrize(
    ("sensor_type", "sensor_tick"),
    [
        ("sensor.other.collision", "0.0"),
        ("sensor.other.gnss", "invalid"),
        ("sensor.other.gnss", "nan"),
    ],
)
def test_unusable_or_event_cadence_does_not_require_world_settings(
    monkeypatch: pytest.MonkeyPatch, sensor_type: str, sensor_tick: str
) -> None:
    """Only usable periodic scheduling configuration justifies a new timing read."""
    case = _case(monkeypatch, "adapter", sensor_type=sensor_type, sensor_tick=sensor_tick)
    try:
        case.world.get_settings.assert_not_called()
    finally:
        case.close()


def test_finite_cadence_with_unrepresentable_frame_ratio_remains_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finite native interval must not overflow phase inference before Listen."""
    case = _case(monkeypatch, "adapter", sensor_tick="1e308")
    try:
        assert case.drain(11, timeout=0)["no_sample_due"] is None
    finally:
        case.close()


@pytest.mark.parametrize("field", ["sensor_tick", "fixed_delta_seconds"])
def test_overflowing_numeric_configuration_leaves_schedule_unknown(field: str) -> None:
    """Malformed native numeric observations cannot prevent listener installation."""
    value = 10**10000
    attributes = {"sensor_tick": cast("str", value)} if field == "sensor_tick" else {}
    sensor = BudgetSensor(type_id="sensor.other.gnss", attributes=attributes)
    step = cast("float", value) if field == "fixed_delta_seconds" else FIXED_STEP
    subscription = SensorSubscription(cast("CarlaSensor", sensor), fixed_delta_seconds=step)
    try:
        report = subscription.drain(11, timeout_seconds=0).to_dict()
        assert report["no_sample_due"] is None
    finally:
        subscription.close()


def test_overflowing_measurement_timestamp_does_not_escape_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed clock observation cannot lose a valid bounded raw measurement."""
    case = _case(monkeypatch, "adapter", sensor_tick="0.1")
    try:
        case.sensor.emit(_measurement(10, cast("float", 10**10000)))
        assert _sample_frames(case.subscription.drain(10, timeout_seconds=0).to_dict()) == [10]
        assert case.subscription.drain(11, timeout_seconds=0).no_sample_due is None
    finally:
        case.close()


def test_large_fractional_cadence_is_not_rounded_into_scheduling_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An integer-phase tolerance must not grow with the configured period."""
    case = _case(monkeypatch, "adapter", sensor_tick="50000.0125")
    try:
        for frame in (0, 1000000):
            case.sensor.emit(_measurement(frame))
            case.drain(frame, timeout=0)
        assert case.drain(1000001, timeout=0)["no_sample_due"] is None
    finally:
        case.close()


@pytest.mark.parametrize("kind", ["gnss", "collision", "lane_invasion"])
def test_merge_sensor_uses_positive_wait_only_for_periodic_gnss(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Managed numerical GNSS should allow delivery, event drains keep their zero wait."""
    case = _case(monkeypatch, "subscription", sensor_type=f"sensor.other.{kind}")
    drain = Mock(wraps=case.subscription.drain)
    monkeypatch.setattr(case.subscription, "drain", drain)
    sensor = MergeSensor(7, 3, "policy", kind, case.subscription)
    try:
        if kind == "gnss":
            case.sensor.emit(_measurement(11))
        sensor.drain(11)
        assert drain.call_args.kwargs["timeout_seconds"] == (DEFAULT_WAIT if kind == "gnss" else 0)
        case.world.tick.assert_not_called()
    finally:
        case.close()


def _sample_frames(report: dict[str, object]) -> list[int]:
    samples = cast("list[dict[str, object]]", report["samples"])
    return [cast("int", sample["frame"]) for sample in samples]
