"""Persistent transport recovery never adopts an unexpected simulator episode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit.actor_runtime import actor_by_id
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.connection_journal import CONNECTION_FILENAME, ConnectionJournal
from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.managed_control_io import read_control
from carla_agentic_toolkit.ownership import RunOwnership, cleanup_owned_actors
from carla_agentic_toolkit.persistent_namespace import PersistentNamespace
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.script_settings import RunSettings
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_sync_settings import FakeSettings

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaSensor, CarlaWorld

ORIGIN_WORLD = 17
REPLACEMENT_WORLD = 18
OWNED_ACTOR = 11
LOCAL_RESULT = 42
FINAL_SETTINGS_READ = 3


@dataclass
class ReconnectCase:
    """Use real adapter/facade execution with explicitly separate native clients."""

    adapter: PythonCarlaAdapter
    api: CarlaScriptApi
    namespace: PersistentNamespace
    first: Mock
    second: Mock
    world: Mock
    actor: Mock
    connect: Mock


def _world(identity: int = 17) -> Mock:
    world = Mock(id=identity)
    world.get_map.return_value.name = "Town10HD"
    world.get_settings.return_value = FakeSettings()
    world.get_snapshot.return_value.frame = 12
    world.get_actors.return_value.filter.return_value = []
    return world


def _client(world: Mock) -> Mock:
    client = Mock()
    client.get_client_version.return_value = "0.9.16-client"
    client.get_server_version.return_value = "0.9.16-server"
    client.get_available_maps.return_value = ["Town10HD"]
    client.get_world.return_value = world
    client.apply_batch_sync.return_value = []
    return client


def _case(monkeypatch: pytest.MonkeyPatch) -> ReconnectCase:
    world = _world()
    actor = Mock(id=11, type_id="vehicle.test", attributes={"role_name": "owned"})
    world.get_actors.return_value.find.return_value = actor
    first, second = _client(world), _client(world)
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", first)  # noqa: SLF001
    connect = Mock(return_value=cast("CarlaClient", second))
    monkeypatch.setattr(adapter, "_connect", connect)
    api = CarlaScriptApi(adapter, RunSnapshots())
    return ReconnectCase(
        adapter, api, PersistentNamespace(api), first, second, world, actor, connect
    )


def _result(case: ReconnectCase, code: str = "result = api.get_world_state()") -> dict[str, object]:
    outcome = case.namespace.execute(code)
    result = outcome.get("result")
    return cast("dict[str, object]", result) if isinstance(result, dict) else outcome


def _establish(case: ReconnectCase) -> None:
    assert _result(case)["current_map"] == "Town10HD"


def _timeout(case: ReconnectCase, error: Exception | None = None) -> dict[str, object]:
    case.first.get_world.side_effect = error or RuntimeError("time-out of 10000ms")
    return _result(case)


def _assert_restarted(result: dict[str, object]) -> None:
    _assert_terminal(result, "simulator_restarted")
    assert (result["expected_world_id"], result["observed_world_id"]) == (
        ORIGIN_WORLD,
        REPLACEMENT_WORLD,
    )


def _assert_terminal(result: dict[str, object], error_type: str) -> None:
    assert result["ok"] is False
    assert result["error_type"] == error_type
    assert result["retryable"] is False


def _assert_sensor_origin(case: ReconnectCase, sensor: CarlaSensor) -> None:
    assert case.adapter._sensor_handles[23] is sensor  # noqa: SLF001
    assert case.adapter._sensor_world_ids[23] == ORIGIN_WORLD  # noqa: SLF001


@pytest.mark.parametrize("error", [RuntimeError("rpc timeout"), OSError("connection reset")])
def test_transport_failure_discards_client_then_reconnects_same_episode(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Invalidate only the failed stream and reconnect on the next bounded request."""
    case = _case(monkeypatch)
    _establish(case)
    assert _timeout(case, error)["ok"] is False
    assert case.adapter._connected_client is None  # noqa: SLF001
    case.connect.assert_not_called()

    assert _result(case)["current_map"] == "Town10HD"
    case.connect.assert_called_once_with()
    assert case.adapter._connected_client is case.second  # noqa: SLF001


def test_timeout_does_not_reconnect_or_replay_within_failed_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent replay of the failed native call inside the same request."""
    case = _case(monkeypatch)
    _establish(case)
    case.first.get_available_maps.side_effect = RuntimeError("rpc timeout")
    outcome = case.namespace.execute("api.list_worlds()\nresult = api.list_worlds()")
    assert cast("dict[str, object]", outcome["result"])["ok"] is False
    case.first.get_available_maps.assert_called_once_with()
    case.connect.assert_not_called()


@pytest.mark.parametrize(
    "operation", ["result = api.get_world_state()", "result = api.apply_batch([])"]
)
def test_reconnected_new_episode_is_nonretryable_before_native_operation(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Reject reused IDs before native reads or a client-only batch mutation."""
    case = _case(monkeypatch)
    _establish(case)
    _timeout(case)
    replacement = _world(18)
    case.second.get_world.return_value = replacement

    _assert_restarted(_result(case, operation))
    case.second.apply_batch_sync.assert_not_called()
    replacement.get_settings.assert_not_called()
    replacement.get_actors.assert_not_called()


def test_ignored_restart_error_still_fails_request_and_stays_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep an ignored episode failure terminal across later requests."""
    case = _case(monkeypatch)
    _establish(case)
    case.first.get_world.return_value = _world(18)
    outcome = case.namespace.execute("api.get_world_state()\nresult = 42")
    assert outcome["ok"] is False
    assert outcome["error_type"] == "simulator_restarted"
    case.second.get_world.return_value = case.world
    _assert_restarted(_result(case))
    case.connect.assert_not_called()


def test_lookup_episode_change_refuses_actor_setter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recheck the actual client after server actor lookup, before its setter."""
    case = _case(monkeypatch)
    _establish(case)
    replacement = _world(18)

    def find_actor(_actor_id: int) -> Mock:
        case.first.get_world.return_value = replacement
        return case.actor

    case.world.get_actors.return_value.find.side_effect = find_actor
    monkeypatch.setattr(
        "carla_agentic_toolkit.experiment_vehicle.carla_transform", lambda _: object()
    )
    result = _result(
        case,
        "result = api.set_actor_transform(11, "
        "{'location': {'x': 1, 'y': 0, 'z': 0}, "
        "'rotation': {'pitch': 0, 'yaw': 0, 'roll': 0}})",
    )
    _assert_restarted(result)
    case.actor.set_transform.assert_not_called()


def test_restart_preserves_original_journals_handles_and_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Retain all original recovery authority instead of adopting a replacement world."""
    case = _case(monkeypatch)
    settings = RunSettings(tmp_path / "world-settings.json")
    settings.initialize()
    settings.capture_world(cast("CarlaWorld", case.world))
    ownership_path = tmp_path / "owned-actors.json"
    ownership = RunOwnership(ownership_path)
    ownership.add([11], world_id=17)
    case.adapter._settings_journal = settings  # noqa: SLF001
    sensor = Mock(id=23)
    case.adapter._sensor_handles[23] = cast("CarlaSensor", sensor)  # noqa: SLF001
    case.adapter._sensor_world_ids[23] = 17  # noqa: SLF001
    _establish(case)
    _timeout(case)
    case.second.get_world.return_value = _world(18)
    _assert_restarted(_result(case))

    report = cleanup_owned_actors(case.adapter, ownership)
    assert report["failures"]
    assert RunOwnership(ownership_path).actor_ids() == (11,)
    assert RunSettings(tmp_path / "world-settings.json", require_existing=True).pending() is True
    _assert_sensor_origin(case, cast("CarlaSensor", sensor))
    case.second.apply_batch_sync.assert_not_called()
    sensor.stop.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        CarlaAdapterError("local validation"),
        UnsupportedFeatureError("missing capability"),
        ValueError("local value"),
        TypeError("local type"),
    ],
)
def test_local_or_unsupported_errors_keep_connection(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Keep native connection ownership after nontransport input/capability errors."""
    case = _case(monkeypatch)
    monkeypatch.setattr(case.adapter, "get_world_state", Mock(side_effect=error))
    assert _result(case)["ok"] is False
    assert case.adapter._connected_client is case.first  # noqa: SLF001
    case.connect.assert_not_called()


@pytest.mark.parametrize("boundary", ["lookup", "attribute"])
def test_local_native_blueprint_rejection_keeps_persistent_client(
    monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """Local blueprint RuntimeError is not a failed native simulator stream."""
    case = _case(monkeypatch)
    case.world.get_settings.return_value.no_rendering_mode = False
    _establish(case)
    library = case.world.get_blueprint_library.return_value
    library.find.return_value.id = "sensor.camera.rgb"
    reject = library.find if boundary == "lookup" else library.find.return_value.set_attribute
    reject.side_effect = RuntimeError("std::exception")
    result = _result(
        case,
        "result = api.attach_camera({'blueprint_id': 'sensor.camera.rgb', "
        "'attributes': {'unknown_field': 'invalid'}, "
        "'transform': {'location': {'x': 0, 'y': 0, 'z': 1}, "
        "'rotation': {'pitch': 0, 'yaw': 0, 'roll': 0}}})",
    )
    assert result["error_type"] == "attach_camera_failed"
    assert "std::exception" in str(result["error"])
    assert case.adapter._connected_client is case.first  # noqa: SLF001
    case.world.spawn_actor.assert_not_called()
    case.connect.assert_not_called()


def test_settings_read_episode_change_refuses_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    """Recheck after the final native settings read, immediately before ApplySettings."""
    case = _case(monkeypatch)
    _establish(case)
    replacement = _world(18)
    reads = 0

    def settings() -> FakeSettings:
        nonlocal reads
        reads += 1
        if reads == FINAL_SETTINGS_READ:
            case.first.get_world.return_value = replacement
        return FakeSettings()

    case.world.get_settings.side_effect = settings
    _assert_restarted(
        _result(case, "result = api.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)")
    )
    case.world.apply_settings.assert_not_called()
    replacement.apply_settings.assert_not_called()


def _durable_case(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ReconnectCase:
    case = _case(monkeypatch)
    path = tmp_path / CONNECTION_FILENAME
    ConnectionJournal(path).initialize()
    case.adapter._persistent_connection_path = path  # noqa: SLF001
    return case


def _set_transform(case: ReconnectCase) -> dict[str, object]:
    return _result(
        case,
        "result = api.set_actor_transform(11, "
        "{'location': {'x': 1, 'y': 0, 'z': 0}, "
        "'rotation': {'pitch': 0, 'yaw': 0, 'roll': 0}})",
    )


def test_origin_is_durable_before_first_actor_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Publish the originating episode before the first native actor setter."""
    case = _durable_case(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "carla_agentic_toolkit.experiment_vehicle.carla_transform", lambda _: object()
    )

    def mutate(_transform: object) -> None:
        assert read_control(tmp_path / CONNECTION_FILENAME)["world_id"] == ORIGIN_WORLD

    case.actor.set_transform.side_effect = mutate
    assert _set_transform(case)["actor_id"] == OWNED_ACTOR
    case.actor.set_transform.assert_called_once()


def test_origin_write_failure_is_fatal_without_connection_invalidation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Treat local write failure as sticky evidence loss, not a retriable RPC."""
    case = _durable_case(monkeypatch, tmp_path)
    writer = Mock(side_effect=OSError("journal volume is full"))
    monkeypatch.setattr("carla_agentic_toolkit.connection_journal.write_control", writer)
    outcome = case.namespace.execute("api.get_world_state()\nresult = 42")
    _assert_terminal(outcome, "persistent_connection_evidence_failed")
    assert case.adapter._connected_client is case.first  # noqa: SLF001
    assert _result(case)["retryable"] is False
    writer.assert_called_once()
    case.actor.set_transform.assert_not_called()


def test_lookup_guard_does_not_leak_after_failed_persistent_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reset the context guard even when the originating operation failed."""
    case = _case(monkeypatch)
    _establish(case)
    case.first.get_world.return_value = _world(18)
    _assert_restarted(_result(case))
    assert actor_by_id(cast("CarlaWorld", case.world), 11) is case.actor


@pytest.mark.parametrize("method", ["load_world", "reload_world"])
def test_acknowledged_map_rebind_is_durable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, method: str
) -> None:
    """Persist only a successful toolkit map response as the new episode."""
    case = _durable_case(monkeypatch, tmp_path)
    _establish(case)
    replacement = _world(18)

    def replace(*_args: object, **_kwargs: object) -> Mock:
        case.first.get_world.return_value = replacement
        return replacement

    getattr(case.first, method).side_effect = replace
    command = (
        "result = api.load_world('Town10HD')"
        if method == "load_world"
        else "result = api.reload_world(reset_settings=False)"
    )
    assert _result(case, command)["current_map"] == "Town10HD"
    assert read_control(tmp_path / CONNECTION_FILENAME) == {
        "schema_version": 1,
        "world_id": 18,
        "restart": None,
    }


def test_failed_reload_does_not_rebind_or_clear_retained_handles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A timed-out reload cannot reset original sensor origins or journal identity."""
    case = _durable_case(monkeypatch, tmp_path)
    _establish(case)
    sensor = cast("CarlaSensor", Mock(id=23))
    case.adapter._sensor_handles[23] = sensor  # noqa: SLF001
    case.adapter._sensor_world_ids[23] = 17  # noqa: SLF001
    case.first.reload_world.side_effect = RuntimeError("rpc timeout")

    result = _result(case, "result = api.reload_world(reset_settings=False)")

    _assert_terminal(result, "reload_world_failed")
    assert read_control(tmp_path / CONNECTION_FILENAME)["world_id"] == ORIGIN_WORLD
    _assert_sensor_origin(case, sensor)
    assert case.adapter._connected_client is None  # noqa: SLF001


def test_non_world_requests_do_not_establish_episode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep local code and map inventory requests free of a world handshake."""
    case = _case(monkeypatch)
    assert case.namespace.execute("result = 42")["result"] == LOCAL_RESULT
    assert _result(case, "result = api.list_worlds()")["worlds"] == ["Town10HD"]
    case.first.get_world.assert_not_called()
    case.first.get_world.return_value = _world(18)
    assert _result(case)["current_map"] == "Town10HD"


@pytest.mark.parametrize("versions", [("0.10.0", "0.9.16"), ("opaque", "opaque"), ("", "0.9.16")])
def test_persistent_unverified_health_remains_version_only(
    monkeypatch: pytest.MonkeyPatch, versions: tuple[str, str]
) -> None:
    """Preserve version-only diagnostics for mismatched, missing, or unknown releases."""
    case = _case(monkeypatch)
    case.first.get_client_version.return_value, case.first.get_server_version.return_value = (
        versions
    )
    case.first.get_world.side_effect = AssertionError("incompatible handshake must be skipped")
    result = _result(case, "result = api.health_check()")
    assert result["connected"] is False
    assert result["settings"] is None
    case.first.get_world.assert_not_called()


@pytest.mark.parametrize("method", ["load_world", "reload_world", "generate_opendrive_world"])
def test_acknowledged_toolkit_replacement_allows_next_request(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """Allow normal acknowledged toolkit replacements across later requests."""
    case = _case(monkeypatch)
    _establish(case)
    replacement = _world(18)

    def replace(*_args: object, **_kwargs: object) -> Mock:
        case.first.get_world.return_value = replacement
        return replacement

    getattr(case.first, method).side_effect = replace
    commands = {
        "load_world": "result = api.load_world('Town10HD')",
        "reload_world": "result = api.reload_world(reset_settings=False)",
        "generate_opendrive_world": "result = api.generate_opendrive_world('road')",
    }
    if method == "generate_opendrive_world":
        monkeypatch.setattr(
            "carla_agentic_toolkit.adapter_experiments.experiment_environment.generate_opendrive_world",
            lambda client, *_args, **_kwargs: client.generate_opendrive_world("road"),
        )
    assert _result(case, commands[method])["current_map"] == "Town10HD"
    assert _result(case)["current_map"] == "Town10HD"
    case.connect.assert_not_called()


def test_finite_facade_keeps_existing_connection_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not enable persistent reconnect or lookup guards for one-shot facade use."""
    client = _client(_world())
    client.get_world.side_effect = RuntimeError("rpc timeout")
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    monkeypatch.setattr(adapter, "_connect", Mock(side_effect=AssertionError("no reconnect")))
    assert CarlaScriptApi(adapter, RunSnapshots()).get_world_state()["ok"] is False
    assert adapter._connected_client is client  # noqa: SLF001
