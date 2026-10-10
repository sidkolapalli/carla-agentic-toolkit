"""Single-owner sensor subscription tests without a live simulator."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter as adapter_module
from carla_agentic_toolkit import experiment_perception as perception
from carla_agentic_toolkit import script_runner
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import CameraAttachRequest, Location, Rotation, Transform
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaSensor, CarlaWorld


class TickSensor:
    """Emit frames only when the explicit test tick owner requests them."""

    id = 7
    type_id = "sensor.camera.rgb"

    def __init__(self) -> None:
        """Initialize an inactive listener."""
        self.callback: Callable[[object], None] | None = None
        self.stops = 0
        self._listening = False
        self.destroy = Mock(return_value=True)

    def listen(self, callback: object) -> None:
        """Register a nonblocking callback."""
        self.callback = cast("Callable[[object], None]", callback)
        self._listening = True

    def is_listening(self) -> bool:
        """Expose the native method form of the listener state."""
        return self._listening

    def stop(self) -> None:
        """Record listener cleanup."""
        self.stops += 1
        self._listening = False

    def emit(self, frame: int) -> None:
        """Produce one owner-triggered sample."""
        assert self.callback is not None
        self.callback(SimpleNamespace(frame=frame, timestamp=frame * 0.05))


def _newly_attached_sensor(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PythonCarlaAdapter, TickSensor, Mock]:
    """Spawn succeeds while the last world snapshot deliberately omits the new sensor."""
    adapter = PythonCarlaAdapter()
    world = Mock(id=17)
    world.get_settings.return_value.no_rendering_mode = False
    world.get_actors.return_value.find.return_value = None
    sensor = TickSensor()
    client = Mock()
    client.get_world.return_value = world
    monkeypatch.setattr(adapter, "_client", Mock(return_value=client))
    monkeypatch.setattr(adapter, "_world", Mock(return_value=world))
    monkeypatch.setattr(
        adapter,
        "apply_batch",
        Mock(return_value={"responses": [{"actor_id": sensor.id, "error": ""}]}),
    )
    monkeypatch.setattr(adapter_module, "_configured_blueprint", Mock())
    monkeypatch.setattr(adapter_module, "_parent_actor", Mock())
    monkeypatch.setattr(adapter_module, "_spawn_sensor", Mock(return_value=sensor))
    adapter.attach_camera(
        request=CameraAttachRequest(
            "sensor.camera.rgb",
            Transform(Location(0.0, 0.0, 1.0), Rotation(0.0, 0.0, 0.0)),
            {},
            None,
        )
    )
    return adapter, sensor, world


def test_new_sensor_can_subscribe_before_first_snapshot_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A synchronous spawn returns the listener handle before actor-list propagation."""
    adapter, sensor, world = _newly_attached_sensor(monkeypatch)

    adapter.subscribe_sensor(sensor.id)
    sensor.emit(1)
    report = adapter.drain_sensor(sensor.id, 1)
    adapter.close_sensor_subscriptions()

    assert report["timed_out"] is False
    world.tick.assert_not_called()


@pytest.mark.parametrize("operation", ["destroy", "detach"])
def test_new_sensor_cleanup_does_not_depend_on_snapshot_visibility(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Failure before the first owner tick must still destroy the created sensor."""
    adapter, sensor, world = _newly_attached_sensor(monkeypatch)
    if operation == "destroy":
        destroyed = adapter.destroy_actors((sensor.id,))[0].destroyed
    else:
        destroyed = adapter.detach_sensor(sensor.id)["destroyed"]

    assert destroyed is True
    sensor.destroy.assert_not_called()
    cast("Mock", adapter.apply_batch).assert_called_once_with(
        [{"action": "destroy_actor", "actor_id": sensor.id}], do_tick=False
    )
    world.tick.assert_not_called()


def test_adapter_retains_client_stream_for_synchronous_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused world's observations must use the established client stream, not a new empty one."""
    established = Mock()
    factory = Mock(side_effect=[established, Mock()])
    monkeypatch.setattr(adapter_module, "_carla_client_factory", Mock(return_value=factory))
    monkeypatch.setattr(adapter_module, "_require_carla_client", lambda value: value)
    adapter = PythonCarlaAdapter()

    first = adapter._client()  # noqa: SLF001 -- Verify the connection lifetime boundary.
    second = adapter._client()  # noqa: SLF001

    assert first is second is established
    factory.assert_called_once()


def test_detach_already_stopped_sensor_does_not_unsubscribe_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit listener close followed by actor cleanup should not emit native stop warnings."""
    adapter, sensor, _world = _newly_attached_sensor(monkeypatch)
    adapter.subscribe_sensor(sensor.id)
    adapter.close_sensor_subscription(sensor.id)

    assert sensor.is_listening() is False
    assert adapter.detach_sensor(sensor.id)["destroyed"] is True
    assert sensor.stops == 1


@pytest.mark.parametrize(("listening", "expected_stops"), [(False, 0), (True, 1)])
def test_detach_sensor_checks_native_listening_method(
    *, listening: bool, expected_stops: int
) -> None:
    """A native sensor is stopped exactly once only while its listener is active."""
    sensor = TickSensor()
    if listening:
        sensor.listen(lambda _frame: None)

    perception.detach_sensor_handle(cast("CarlaSensor", sensor))

    assert sensor.stops == expected_stops
    assert sensor.is_listening() is False
    sensor.destroy.assert_called_once()


@pytest.mark.parametrize(("listening", "expected_stops"), [(False, 0), (True, 1)])
def test_detach_sensor_accepts_boolean_listening_state(
    monkeypatch: pytest.MonkeyPatch, *, listening: bool, expected_stops: int
) -> None:
    """Older clients exposing the listener state as a boolean remain compatible."""
    sensor = TickSensor()
    monkeypatch.setattr(sensor, "is_listening", listening)

    perception.detach_sensor_handle(cast("CarlaSensor", sensor))

    assert sensor.stops == expected_stops
    sensor.destroy.assert_called_once()


def test_owner_can_subscribe_tick_then_drain_exact_frame() -> None:
    """Collection never hides a tick or blocks the owner before subscribing."""
    sensor = TickSensor()
    tick = Mock(side_effect=lambda: sensor.emit(8))
    subscription = perception.SensorSubscription(cast("CarlaSensor", sensor))
    tick.assert_not_called()
    tick()
    batch = subscription.drain(8)

    assert [cast("SimpleNamespace", frame).frame for frame in batch.frames] == [8]
    assert batch.to_dict()["timed_out"] is False
    subscription.close()
    assert sensor.stops == 1


def test_future_sensor_frames_remain_for_their_owner_frame() -> None:
    """A drain must not mislabel a future sample as the requested observation."""
    sensor = TickSensor()
    subscription = perception.SensorSubscription(cast("CarlaSensor", sensor))
    sensor.emit(11)

    assert subscription.drain(10).to_dict()["timed_out"] is True
    assert [cast("SimpleNamespace", frame).frame for frame in subscription.drain(11).frames] == [11]
    subscription.close()


def test_event_sensor_empty_drain_is_normal_and_never_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absent collision event must not stall the simulator control loop."""
    sensor = TickSensor()
    subscription = perception.SensorSubscription(cast("CarlaSensor", sensor), event_sensor=True)
    wait = Mock(side_effect=AssertionError("event drain must not wait"))
    monkeypatch.setattr(subscription, "_wait", wait)

    assert subscription.drain(12, timeout_seconds=5.0).to_dict()["timed_out"] is False
    wait.assert_not_called()
    subscription.close()


def test_subscription_reports_overflow_frame_lag_and_delivery_latency() -> None:
    """Bounded producer overflow and stale observations remain measurable."""
    sensor = TickSensor()
    subscription = perception.SensorSubscription(
        cast("CarlaSensor", sensor), event_sensor=True, capacity=1
    )
    sensor.emit(1)
    sensor.emit(2)
    report = subscription.drain(3).to_dict()

    assert report["dropped_samples"] == 1
    samples = cast("list[dict[str, object]]", report["samples"])
    assert samples[0]["frame_lag"] == 1
    assert cast("float", samples[0]["queue_latency_seconds"]) >= 0
    subscription.close()


def test_subscription_close_is_idempotent_and_ignores_late_callbacks() -> None:
    """Closing a listener does not permit late callbacks to revive its queue."""
    sensor = TickSensor()
    subscription = perception.SensorSubscription(cast("CarlaSensor", sensor))
    subscription.close()
    subscription.close()
    sensor.emit(1)

    assert sensor.stops == 1
    with pytest.raises(CarlaAdapterError, match="closed"):
        subscription.drain(1)


def test_close_and_drain_preserves_samples_delivered_during_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final evidence includes buffered and shutdown callbacks before freezing the queue."""
    sensor = TickSensor()
    subscription = perception.SensorSubscription(cast("CarlaSensor", sensor), event_sensor=True)
    sensor.emit(8)
    stop = Mock(side_effect=lambda: sensor.emit(10))
    monkeypatch.setattr(sensor, "stop", stop)

    batch = subscription.close_and_drain(9)
    sensor.emit(11)

    assert [sample.frame for sample in batch.samples] == [8, 10]
    assert batch.pending_samples == 0
    assert batch.timed_out is False
    assert subscription.close_and_drain(9).samples == ()
    subscription.close()
    stop.assert_called_once()


def test_close_and_drain_failure_still_closes_listener(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop failure remains visible while preventing subsequent callback acceptance."""
    sensor = TickSensor()
    subscription = perception.SensorSubscription(cast("CarlaSensor", sensor))
    monkeypatch.setattr(sensor, "stop", Mock(side_effect=RuntimeError("stop failed")))

    with pytest.raises(CarlaAdapterError, match="stop failed"):
        subscription.close_and_drain(9)
    with pytest.raises(CarlaAdapterError, match="closed"):
        subscription.drain(9)


def test_periodic_subscription_reports_bounded_timeout() -> None:
    """A missing camera sample has an explicit timeout instead of a fabricated frame."""
    subscription = perception.SensorSubscription(cast("CarlaSensor", TickSensor()))
    batch = subscription.drain(4, timeout_seconds=0.001)

    assert batch.frames == ()
    assert batch.to_dict()["timed_out"] is True
    subscription.close()


def test_synchronous_blocking_capture_fails_fast_before_listening(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The legacy capture helper directs sync callers to subscribe/tick/drain."""
    world = Mock(id=17)
    world.get_settings.return_value.synchronous_mode = True
    world.get_settings.return_value.no_rendering_mode = False
    sensor = Mock(id=7, type_id="sensor.camera.rgb")
    world.get_actors.return_value.find.return_value = sensor
    client = Mock()
    client.get_world.return_value = world
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", Mock(return_value=client))
    monkeypatch.setattr(adapter, "_world", Mock(return_value=world))

    with pytest.raises(CarlaAdapterError, match="subscribe_sensor"):
        adapter.capture_sensor_frame(sensor_id=7, output_path=tmp_path / "frame.png")

    world.get_actors.assert_called_once_with([7])
    sensor.listen.assert_not_called()


def test_synchronous_blocking_stream_fails_fast_before_listening() -> None:
    """A synchronous stream read must never wait for a tick its caller cannot send."""
    world = Mock()
    world.get_settings.return_value.synchronous_mode = True
    world.get_settings.return_value.no_rendering_mode = False
    sensor = Mock(id=7, type_id="sensor.camera.rgb")
    world.get_actors.return_value.find.return_value = sensor

    with pytest.raises(CarlaAdapterError, match="subscribe_sensor"):
        perception.read_sensor_stream(
            cast("CarlaWorld", world), sensor_id=7, frame_count=1, output_dir=None
        )

    world.get_actors.assert_called_once_with([7])
    sensor.listen.assert_not_called()


@pytest.mark.parametrize("code", ["result = 1", "assert False, 'script failed'"])
def test_script_exit_closes_subscriptions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: str
) -> None:
    """Normal and failing scripts both stop any listener they forgot to close."""
    adapter = Mock()
    monkeypatch.setattr(script_runner, "PythonCarlaAdapter", Mock(return_value=adapter))
    script = tmp_path / "subscribed.py"
    script.write_text(code, encoding="utf-8")

    script_runner.run_script_file(
        script_path=script, host="localhost", port=2000, timeout_seconds=1
    )

    adapter.close_sensor_subscriptions.assert_called_once_with()


def test_async_collector_receives_callback_and_always_stops() -> None:
    """The shared listener retains the existing asynchronous capture behavior."""
    sensor = Mock()
    image = SimpleNamespace(frame=23, timestamp=1.15)
    sensor.listen.side_effect = lambda callback: callback(image)

    assert perception.collect_sensor_frames(cast("CarlaSensor", sensor), 1) == [image]
    sensor.stop.assert_called_once_with()


def test_facade_subscribe_tick_drain_and_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The script facade exposes the same non-ticking lifecycle as trusted owners."""
    sensor = TickSensor()
    world = Mock(id=17)
    world.get_settings.return_value.no_rendering_mode = False
    world.get_actors.return_value.find.return_value = sensor
    client = Mock()
    client.get_world.return_value = world
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", Mock(return_value=client))
    monkeypatch.setattr(adapter, "_world", Mock(return_value=world))
    api = build_api(adapter, RunSnapshots())
    api.subscribe_sensor(7)
    expected_frame = 19
    sensor.emit(expected_frame)
    report = api.drain_sensor(7, expected_frame)
    api.close_sensor_subscription(7)

    assert report["timed_out"] is False
    assert cast("list[dict[str, object]]", report["frames"])[0]["frame"] == expected_frame
    world.tick.assert_not_called()
