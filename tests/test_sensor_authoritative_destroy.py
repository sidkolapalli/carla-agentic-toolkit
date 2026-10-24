"""Sensor destruction uses server evidence even before its first cached snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter as adapter_module
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import CameraAttachRequest, Location, Rotation, Transform
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_sensor_subscriptions import TickSensor
from tests.test_sync_settings import FakeSettings

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld

WORLD_ID = 17


@dataclass
class SensorFixture:
    """A created handle exists while the world's cached actor list stays empty."""

    adapter: PythonCarlaAdapter
    sensor: TickSensor
    world: Mock
    batch: Mock
    events: list[str]


def _sensor_fixture(monkeypatch: pytest.MonkeyPatch) -> SensorFixture:
    adapter = PythonCarlaAdapter()
    sensor = TickSensor()
    sensor.destroy.return_value = False
    world = Mock(id=WORLD_ID)
    world.get_settings.return_value = FakeSettings(no_rendering_mode=False)
    world.get_actors.return_value.find.return_value = None
    client = cast("CarlaClient", SimpleNamespace(get_world=lambda: world))
    monkeypatch.setattr(adapter, "_client", lambda: client)
    monkeypatch.setattr(adapter, "_world", lambda _client: cast("CarlaWorld", world))
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
    events: list[str] = []
    stop = sensor.stop

    def record_stop() -> None:
        events.append("stop")
        stop()

    monkeypatch.setattr(sensor, "stop", record_stop)

    def batch(_commands: list[dict[str, object]], *, do_tick: bool) -> dict[str, object]:
        assert do_tick is False
        events.append("batch")
        return {"responses": [{"actor_id": sensor.id, "error": ""}]}

    batch_mock = Mock(side_effect=batch)
    monkeypatch.setattr(adapter, "apply_batch", batch_mock)
    return SensorFixture(adapter, sensor, world, batch_mock, events)


def _destroy_sensor(fixture: SensorFixture, operation: str) -> bool:
    if operation == "detach":
        return fixture.adapter.detach_sensor(fixture.sensor.id)["destroyed"] is True
    return fixture.adapter.destroy_actors((fixture.sensor.id,))[0].destroyed


def _assert_released_handle(fixture: SensorFixture) -> None:
    assert fixture.sensor.id not in fixture.adapter._sensor_handles  # noqa: SLF001
    assert fixture.sensor.id not in fixture.adapter._sensor_world_ids  # noqa: SLF001


def _assert_detached_payload(payload: dict[str, object], *, already_absent: bool) -> None:
    assert payload["destroyed"] is not already_absent
    if already_absent:
        assert payload["error"] == "Actor was not found."


@pytest.mark.parametrize("operation", ["detach", "destroy"])
def test_false_cached_sensor_destroy_uses_batch_after_listener_close(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """The cached destroy method cannot decide whether a newly spawned sensor is alive."""
    fixture = _sensor_fixture(monkeypatch)
    fixture.adapter.subscribe_sensor(fixture.sensor.id)
    assert _destroy_sensor(fixture, operation) is True

    assert fixture.events == ["stop", "batch"]
    assert fixture.sensor.stops == 1
    fixture.sensor.destroy.assert_not_called()
    fixture.batch.assert_called_once_with(
        [{"action": "destroy_actor", "actor_id": fixture.sensor.id}], do_tick=False
    )
    fixture.world.tick.assert_not_called()
    _assert_released_handle(fixture)


@pytest.mark.parametrize("already_absent", [False, True])
def test_direct_sensor_detach_releases_confirmed_ownership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, already_absent: bool
) -> None:
    """Authoritative absence is clean without pretending a new destruction occurred."""
    fixture = _sensor_fixture(monkeypatch)
    if already_absent:
        fixture.batch.side_effect = None
        fixture.batch.return_value = {
            "responses": [{"actor_id": 0, "error": "unable to destroy actor: not found"}]
        }
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((fixture.sensor.id,), world_id=WORLD_ID)
    api = CarlaScriptApi(fixture.adapter, RunSnapshots(), ownership=ownership)

    payload = api.detach_sensor(fixture.sensor.id)

    assert ownership.actor_ids() == ()
    _assert_detached_payload(payload, already_absent=already_absent)
    fixture.sensor.destroy.assert_not_called()
    _assert_released_handle(fixture)


@pytest.mark.parametrize("error_type", [AttributeError, RuntimeError, TypeError, ValueError])
def test_failed_reload_retains_sensor_ownership_for_later_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error_type: type[Exception]
) -> None:
    """A failed map RPC closes listeners but cannot discard same-episode ownership."""
    fixture = _sensor_fixture(monkeypatch)
    fixture.adapter.subscribe_sensor(fixture.sensor.id)
    reload = Mock(side_effect=error_type("reload refused"))
    client = cast(
        "CarlaClient", SimpleNamespace(get_world=lambda: fixture.world, reload_world=reload)
    )
    monkeypatch.setattr(fixture.adapter, "_client", lambda: client)
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((fixture.sensor.id,), world_id=WORLD_ID)
    api = CarlaScriptApi(fixture.adapter, RunSnapshots(), ownership=ownership)

    payload = api.reload_world(reset_settings=False)

    _assert_reload_failure(payload)
    reload.assert_called_once()
    assert reload.call_args.args == (False,)
    _assert_reload_retained_ownership(fixture, ownership)
    assert fixture.sensor.stops == 1
    fixture.batch.assert_not_called()

    cleanup = api.cleanup_owned_actors()

    _assert_failed_reload_cleanup(fixture, ownership, cleanup)
    fixture.sensor.destroy.assert_not_called()
    _assert_released_handle(fixture)


def _assert_reload_failure(payload: dict[str, object]) -> None:
    assert payload["ok"] is False
    assert payload["error_type"] == "reload_world_failed"
    assert payload["retryable"] is False
    assert payload["message"] == "reload refused"


def _assert_reload_retained_ownership(fixture: SensorFixture, ownership: RunOwnership) -> None:
    assert ownership.actor_ids() == (fixture.sensor.id,)
    assert ownership.world_id() == WORLD_ID
    assert fixture.adapter._sensor_handles[fixture.sensor.id] is fixture.sensor  # noqa: SLF001
    assert fixture.adapter._sensor_world_ids[fixture.sensor.id] == WORLD_ID  # noqa: SLF001


def _assert_failed_reload_cleanup(
    fixture: SensorFixture, ownership: RunOwnership, cleanup: dict[str, object]
) -> None:
    assert cleanup["failures"] == []
    assert ownership.actor_ids() == ()
    assert fixture.events == ["stop", "batch"]
    assert fixture.sensor.stops == 1


@pytest.mark.parametrize(
    "response",
    [
        {"actor_id": 0, "error": "server busy"},
        {"actor_id": 0, "error": "Actor was not found."},
        {"actor_id": 99, "error": ""},
        {"actor_id": True, "error": ""},
        {"actor_id": 7},
        {"actor_id": 7, "error": False},
        {"actor_id": 7, "error": 0},
        {"actor_id": 7, "error": []},
    ],
)
def test_failed_sensor_batch_retains_handle_and_ownership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, response: dict[str, object]
) -> None:
    """Only a matching acknowledgement or exact server absence releases the sensor."""
    fixture = _sensor_fixture(monkeypatch)
    fixture.batch.side_effect = None
    fixture.batch.return_value = {"responses": [response]}
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((fixture.sensor.id,), world_id=WORLD_ID)
    api = CarlaScriptApi(fixture.adapter, RunSnapshots(), ownership=ownership)

    payload = api.detach_sensor(fixture.sensor.id)

    assert payload["ok"] is False
    assert payload["error_type"] == "detach_sensor_failed"
    assert ownership.actor_ids() == (fixture.sensor.id,)
    assert fixture.adapter._sensor_handles[fixture.sensor.id] is fixture.sensor  # noqa: SLF001
    fixture.sensor.destroy.assert_not_called()
    fixture.world.tick.assert_not_called()


@pytest.mark.parametrize("error", [None, ""])
def test_successful_acknowledgement_ignores_extra_metadata(
    monkeypatch: pytest.MonkeyPatch, error: str | None
) -> None:
    """Only identity and a valid error field determine server acknowledgement success."""
    fixture = _sensor_fixture(monkeypatch)
    fixture.batch.side_effect = None
    fixture.batch.return_value = {
        "responses": [{"actor_id": fixture.sensor.id, "error": error, "metadata": {"n": 1}}]
    }

    assert fixture.adapter.detach_sensor(fixture.sensor.id)["destroyed"] is True
    _assert_released_handle(fixture)


@pytest.mark.parametrize("during", ["listener_close", "batch"])
def test_sensor_episode_change_never_authorizes_replacement_cleanup(
    monkeypatch: pytest.MonkeyPatch, during: str
) -> None:
    """An episode change during listener shutdown or RPC leaves the cached handle intact."""
    fixture = _sensor_fixture(monkeypatch)
    fixture.adapter.subscribe_sensor(fixture.sensor.id)
    if during == "listener_close":
        stop = fixture.sensor.stop

        def replace_after_stop() -> None:
            stop()
            fixture.world.id += 1

        monkeypatch.setattr(fixture.sensor, "stop", replace_after_stop)
    else:

        def replace_during_batch(
            _commands: list[dict[str, object]], *, do_tick: bool
        ) -> dict[str, object]:
            assert do_tick is False
            fixture.world.id += 1
            return {"responses": [{"actor_id": fixture.sensor.id, "error": ""}]}

        fixture.batch.side_effect = replace_during_batch

    with pytest.raises(CarlaAdapterError, match="episode changed"):
        fixture.adapter.detach_sensor(fixture.sensor.id)

    if during == "listener_close":
        fixture.batch.assert_not_called()
    else:
        fixture.batch.assert_called_once()
    assert fixture.adapter._sensor_handles[fixture.sensor.id] is fixture.sensor  # noqa: SLF001
    fixture.sensor.destroy.assert_not_called()
    fixture.world.tick.assert_not_called()


def test_sensor_stop_failure_prevents_batch_and_retains_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A listener that cannot be stopped is not silently discarded before destruction."""
    fixture = _sensor_fixture(monkeypatch)
    fixture.adapter.subscribe_sensor(fixture.sensor.id)
    monkeypatch.setattr(fixture.sensor, "stop", Mock(side_effect=RuntimeError("stop failed")))

    with pytest.raises(CarlaAdapterError, match="stop failed"):
        fixture.adapter.detach_sensor(fixture.sensor.id)

    fixture.batch.assert_not_called()
    fixture.sensor.destroy.assert_not_called()
    assert fixture.adapter._sensor_handles[fixture.sensor.id] is fixture.sensor  # noqa: SLF001


@pytest.mark.parametrize("operation", ["detach", "destroy"])
def test_old_sensor_handle_cannot_authorize_reused_replacement_id(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """An external replacement before cleanup invalidates the cached creation identity."""
    fixture = _sensor_fixture(monkeypatch)
    fixture.adapter.subscribe_sensor(fixture.sensor.id)
    fixture.world.id += 1

    _assert_cleanup_refused(fixture, operation)
    fixture.batch.assert_not_called()
    fixture.sensor.destroy.assert_not_called()
    assert fixture.sensor.stops == 0
    assert fixture.adapter._sensor_handles[fixture.sensor.id] is fixture.sensor  # noqa: SLF001


def _assert_cleanup_refused(fixture: SensorFixture, operation: str) -> None:
    if operation == "detach":
        with pytest.raises(CarlaAdapterError, match="episode changed"):
            fixture.adapter.detach_sensor(fixture.sensor.id)
    else:
        result = fixture.adapter.destroy_actors((fixture.sensor.id,))[0]
        assert result.destroyed is False
        assert "episode changed" in str(result.error)


@pytest.mark.parametrize("rebind_journal", [False, True])
def test_absence_release_cannot_clear_another_episode_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, rebind_journal: bool
) -> None:
    """A late replacement cannot turn old-episode absence into new ownership removal."""
    fixture = _sensor_fixture(monkeypatch)
    fixture.batch.side_effect = None
    fixture.batch.return_value = {
        "responses": [{"actor_id": 0, "error": "unable to destroy actor: not found"}]
    }
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((fixture.sensor.id,), world_id=WORLD_ID)
    api = CarlaScriptApi(fixture.adapter, RunSnapshots(), ownership=ownership)
    detach = fixture.adapter.detach_sensor

    def replace_after_acknowledgement(sensor_id: int) -> dict[str, object]:
        payload = detach(sensor_id)
        fixture.world.id += 1
        if rebind_journal:
            ownership.clear()
            ownership.add((sensor_id,), world_id=fixture.world.id)
        return payload

    monkeypatch.setattr(fixture.adapter, "detach_sensor", replace_after_acknowledgement)

    assert api.detach_sensor(fixture.sensor.id)["error"] == "Actor was not found."
    assert ownership.actor_ids() == (fixture.sensor.id,)
