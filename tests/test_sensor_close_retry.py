"""Queue closure cannot substitute for acknowledgement of the original sensor Stop."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.sensor_subscription import SensorDrain, SensorSubscription
from tests.test_sensor_subscriptions import TickSensor

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaSensor

STOP_ATTEMPTS_AFTER_RETRY = 2


class RetrySensor(TickSensor):
    """Keep the original handle listening until a native Stop actually returns."""

    def __init__(self, failures: int | None, *, error: type[Exception] = RuntimeError) -> None:
        """Use None for persistent failure and count each attempted native call."""
        super().__init__()
        self.failures = failures
        self.error = error
        self.stop_attempts = 0
        self.on_stop: Callable[[], None] | None = None

    def stop(self) -> None:
        """Permit realistic shutdown callbacks without acknowledging a failed Stop."""
        self.stop_attempts += 1
        if self.on_stop is not None:
            self.on_stop()
        message = "native Stop failed"
        if self.failures is None:
            raise self.error(message)
        if self.failures:
            self.failures -= 1
            raise self.error(message)
        super().stop()


def _close(subscription: SensorSubscription, operation: str) -> SensorDrain | None:
    if operation == "close_and_drain":
        return subscription.close_and_drain(9)
    subscription.close()
    return None


def _assert_queue_frozen(subscription: SensorSubscription, sensor: RetrySensor) -> None:
    sensor.emit(99)
    with pytest.raises(CarlaAdapterError, match="closed"):
        subscription.drain(99)
    with pytest.raises(CarlaAdapterError, match="closed"):
        subscription.next_frame(timeout_seconds=0)


@pytest.mark.parametrize("first", ["close", "close_and_drain"])
@pytest.mark.parametrize("retry", ["close", "close_and_drain"])
@pytest.mark.parametrize("error", [AttributeError, RuntimeError, TypeError, ValueError])
def test_persistent_stop_failure_remains_visible_on_every_close(
    first: str, retry: str, error: type[Exception]
) -> None:
    """A later cleanup boundary must not report success while upstream Stop still fails."""
    sensor = RetrySensor(None, error=error)
    subscription = SensorSubscription(cast("CarlaSensor", sensor))
    with pytest.raises(CarlaAdapterError, match="native Stop failed"):
        _close(subscription, first)
    _assert_queue_frozen(subscription, sensor)
    with pytest.raises(CarlaAdapterError, match="native Stop failed"):
        _close(subscription, retry)
    assert sensor.stop_attempts == STOP_ATTEMPTS_AFTER_RETRY
    assert sensor.is_listening() is True


@pytest.mark.parametrize("first", ["close", "close_and_drain"])
@pytest.mark.parametrize("retry", ["close", "close_and_drain"])
def test_transient_stop_failure_retries_original_handle_before_idempotent_success(
    first: str, retry: str
) -> None:
    """Only an acknowledged retry can make repeated close boundaries resource-free."""
    sensor = RetrySensor(1)
    subscription = SensorSubscription(cast("CarlaSensor", sensor), event_sensor=True)
    sensor.emit(8)
    sensor.on_stop = lambda: sensor.emit(10)
    with pytest.raises(CarlaAdapterError, match="native Stop failed"):
        _close(subscription, first)
    _assert_queue_frozen(subscription, sensor)
    result = _close(subscription, retry)
    _assert_recovered_stop(sensor)
    if result is not None:
        assert result.samples == ()
    subscription.close()
    assert subscription.close_and_drain(9).samples == ()
    assert sensor.stop_attempts == STOP_ATTEMPTS_AFTER_RETRY


def _assert_recovered_stop(sensor: RetrySensor) -> None:
    assert sensor.stop_attempts == STOP_ATTEMPTS_AFTER_RETRY
    assert sensor.stops == 1
    assert sensor.is_listening() is False


@pytest.mark.parametrize("operation", ["close", "close_and_drain"])
def test_successful_stop_remains_idempotent_across_close_boundaries(operation: str) -> None:
    """Acknowledged Stop need not be reissued just because the close API changes."""
    sensor = RetrySensor(0)
    subscription = SensorSubscription(cast("CarlaSensor", sensor), event_sensor=True)
    _close(subscription, operation)
    subscription.close()
    assert subscription.close_and_drain(9).samples == ()
    assert sensor.stop_attempts == 1
    assert sensor.stops == 1
    _assert_queue_frozen(subscription, sensor)


def test_close_and_drain_preserves_final_callbacks_before_successful_stop_acknowledgement() -> None:
    """The successful first drain retains existing shutdown evidence without queue resurrection."""
    sensor = RetrySensor(0)
    subscription = SensorSubscription(cast("CarlaSensor", sensor), event_sensor=True)
    sensor.emit(8)
    sensor.on_stop = lambda: sensor.emit(10)

    result = subscription.close_and_drain(9)

    assert [sample.frame for sample in result.samples] == [8, 10]
    assert result.pending_samples == 0
    _assert_queue_frozen(subscription, sensor)
    assert subscription.close_and_drain(9).samples == ()
    assert sensor.stop_attempts == 1
