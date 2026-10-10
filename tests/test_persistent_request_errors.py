"""Local aliases and converted cleanup failures preserve persistent RPC authority."""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit.actor_identity import ActorIdentity
from carla_agentic_toolkit.actor_registry import ActorRegistry
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.persistent_namespace import PersistentNamespace
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.script_settings import RunSettings
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_persistent_reconnect import ReconnectCase, _case, _establish, _result

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaSensor, CarlaWorld

ACTOR_IDS = (11, 12)
WORLD_ID = 17
CLEANUP_BOUNDARIES = (
    "actor_destroy",
    "batch_fallback",
    "sensor_stop",
    "sensor_batch",
    "subscription_stop",
    "walker_stop",
    "walker_lookup",
)


@pytest.mark.parametrize(
    "failure", ["missing", "blank", "legacy", "json", "read", "write", "identity"]
)
def test_local_registry_failure_keeps_client_and_allows_same_request_rpc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    """Registry validation and local filesystem failures are not network failures."""
    case = _case(monkeypatch)
    _establish(case)
    command = _local_registry_failure(case, tmp_path, monkeypatch, failure)
    outcome = case.namespace.execute(f"failure = {command}\nresult = api.list_worlds()")
    assert cast("dict[str, object]", outcome["result"])["worlds"] == ["Town10HD"]
    assert case.adapter._connected_client is case.first  # noqa: SLF001
    case.first.get_available_maps.assert_called_once_with()
    case.connect.assert_not_called()


def _local_registry_failure(
    case: ReconnectCase, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> str:
    path = tmp_path / "aliases.json"
    case.api._actor_registry = ActorRegistry(path)  # noqa: SLF001 -- actual local registry.
    _prepare_registry_file(path, failure)
    if failure == "write":
        monkeypatch.setattr(type(path), "replace", Mock(side_effect=OSError("registry disk full")))
    elif failure == "identity":
        case.actor.type_id = ""
    commands = {"missing": "api.resolve_actor('missing')", "blank": "api.name_actor('', 11)"}
    return commands.get(failure, "api.name_actor('owned', 11)")


def _prepare_registry_file(path: Path, failure: str) -> None:
    if failure == "read":
        path.mkdir()
    elif failure in {"legacy", "json"}:
        path.write_text(json.dumps({"old": 11}) if failure == "legacy" else "{")


@pytest.mark.parametrize("error", [RuntimeError("lookup RPC timeout"), OSError("lookup reset")])
def test_native_alias_lookup_failure_freezes_request_without_removing_alias(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    """A native lookup failure retains its cause and alias instead of looking absent."""
    case = _case(monkeypatch)
    _establish(case)
    registry = ActorRegistry(tmp_path / "aliases.json")
    identity = ActorIdentity(11, WORLD_ID, "vehicle.test", "owned")
    registry.set("owned", identity)
    case.api._actor_registry = registry  # noqa: SLF001
    case.world.get_actors.side_effect = error
    outcome = case.namespace.execute("api.resolve_actor('owned')\nresult = api.list_worlds()")
    assert cast("dict[str, object]", outcome["result"])["ok"] is False
    assert registry.get("owned") == identity
    _assert_frozen(case, error)


@dataclass
class CleanupCase:
    """Keep durable ownership, settings, original handles and native call counters."""

    connection: ReconnectCase
    ownership: RunOwnership
    settings: RunSettings
    other: Mock


def _cleanup_case(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CleanupCase:
    connection = _case(monkeypatch)
    other = Mock(id=12, type_id="vehicle.other", attributes={"role_name": "owned-other"})
    actors = {11: connection.actor, 12: other}
    connection.world.get_actors.return_value.find.side_effect = actors.get
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add(ACTOR_IDS, world_id=WORLD_ID)
    settings = RunSettings(tmp_path / "world-settings.json")
    settings.initialize()
    settings.capture_world(cast("CarlaWorld", connection.world))
    connection.adapter._settings_journal = settings  # noqa: SLF001
    connection.api = CarlaScriptApi(connection.adapter, RunSnapshots(), ownership=ownership)
    connection.namespace = PersistentNamespace(connection.api)
    _establish(connection)
    monkeypatch.setattr(
        "carla_agentic_toolkit.experiment_replay.import_module",
        lambda _name: SimpleNamespace(
            command=SimpleNamespace(DestroyActor=lambda identity: identity)
        ),
    )
    return CleanupCase(connection, ownership, settings, other)


def _configure_cleanup(case: CleanupCase, boundary: str) -> Mock:
    connection, actor = case.connection, case.connection.actor
    if boundary in {"sensor_stop", "sensor_batch", "subscription_stop"}:
        return _configure_sensor_cleanup(connection, boundary)
    if boundary == "batch_fallback":
        actor.destroy.return_value = False
    if boundary == "walker_stop":
        actor.type_id = "controller.ai.walker"
    native_methods = {
        "actor_destroy": actor.destroy,
        "batch_fallback": connection.first.apply_batch_sync,
        "walker_stop": actor.stop,
        "walker_lookup": connection.world.get_actors,
    }
    return native_methods[boundary]


def _configure_sensor_cleanup(connection: ReconnectCase, boundary: str) -> Mock:
    actor = connection.actor
    actor.type_id = "sensor.other.gnss"
    actor.is_listening.return_value = boundary != "sensor_batch"
    if boundary == "subscription_stop":
        payload = _result(connection, "result = api.subscribe_sensor(11)")
        assert payload["sensor_id"] == ACTOR_IDS[0]
    else:
        connection.adapter._sensor_handles[11] = cast("CarlaSensor", actor)  # noqa: SLF001
        connection.adapter._sensor_world_ids[11] = WORLD_ID  # noqa: SLF001
    return connection.first.apply_batch_sync if boundary == "sensor_batch" else actor.stop


@pytest.mark.parametrize("boundary", CLEANUP_BOUNDARIES)
@pytest.mark.parametrize("error_type", [RuntimeError, OSError])
def test_converted_cleanup_transport_failure_stops_all_followup_rpc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: str, error_type: type[Exception]
) -> None:
    """Converted failure evidence must freeze transport before cleanup can advance."""
    case = _cleanup_case(monkeypatch, tmp_path)
    native = _configure_cleanup(case, boundary)
    error = error_type(f"{boundary} native timeout")
    after_failure: list[tuple[int, ...]] = []

    def fail(*_args: object, **_kwargs: object) -> None:
        after_failure.append(_native_counts(case))
        raise error

    native.side_effect = fail
    outcome = case.connection.namespace.execute(
        "api.destroy_actors([11, 12])\nresult = api.list_worlds()"
    )
    assert cast("dict[str, object]", outcome["result"])["ok"] is False
    assert after_failure == [_native_counts(case)]
    _assert_retained(case, boundary)
    _assert_frozen(case.connection, error)


def _native_counts(case: CleanupCase) -> tuple[int, ...]:
    connection = case.connection
    return (
        connection.first.get_world.call_count,
        connection.world.get_actors.call_count,
        connection.first.get_available_maps.call_count,
        connection.first.apply_batch_sync.call_count,
        connection.actor.destroy.call_count,
        connection.actor.stop.call_count,
        case.other.destroy.call_count,
    )


def _assert_retained(case: CleanupCase, boundary: str) -> None:
    _assert_owned_journals(case)
    case.other.destroy.assert_not_called()
    if boundary.startswith("sensor"):
        assert case.connection.adapter._sensor_handles[11] is case.connection.actor  # noqa: SLF001
    if boundary == "subscription_stop":
        _assert_retained_subscription(case.connection)


def _assert_owned_journals(case: CleanupCase) -> None:
    assert RunOwnership(case.ownership._path).actor_ids() == ACTOR_IDS  # noqa: SLF001
    assert RunSettings(case.settings._path, require_existing=True).pending() is True  # noqa: SLF001


def _assert_retained_subscription(case: ReconnectCase) -> None:
    assert case.adapter._subscribed_sensor_handles[11] is case.actor  # noqa: SLF001
    assert case.adapter._subscription_world_ids[11] == WORLD_ID  # noqa: SLF001
    assert ACTOR_IDS[0] in case.adapter._sensor_subscriptions  # noqa: SLF001


def _assert_frozen(case: ReconnectCase, error: Exception) -> None:
    assert case.adapter._connected_client is None  # noqa: SLF001
    case.first.get_available_maps.assert_not_called()
    case.connect.assert_not_called()
    assert _original_cause(_retained_failure(case)) is error


def _retained_failure(case: ReconnectCase) -> Exception:
    state = case.adapter._persistent_connection  # noqa: SLF001
    assert state is not None
    failure = state._request_failure  # noqa: SLF001 -- inspect retained native cause, not text.
    assert failure is not None
    return failure


def _original_cause(error: Exception) -> BaseException:
    current: BaseException = error
    while current.__cause__ is not None:
        current = current.__cause__
    return current


@pytest.mark.parametrize(
    "boundary", ["actor_destroy", "batch_fallback", "sensor_stop", "walker_stop"]
)
def test_cleanup_timeout_reconnects_only_next_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: str
) -> None:
    """The next request may reconnect, without replaying a failed mutation."""
    case = _cleanup_case(monkeypatch, tmp_path)
    _configure_cleanup(case, boundary).side_effect = RuntimeError("native timeout")
    case.connection.namespace.execute("result = api.destroy_actors([11])")
    assert _result(case.connection)["current_map"] == "Town10HD"
    case.connection.connect.assert_called_once_with()
    assert case.connection.adapter._connected_client is case.connection.second  # noqa: SLF001


def test_finite_actor_destroy_failure_preserves_structured_result_and_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No persistent context hook is installed by a direct finite facade call."""
    _finite_destroy_failure(monkeypatch)


def _finite_destroy_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    case = _case(monkeypatch)
    case.actor.destroy.side_effect = RuntimeError("native timeout")
    payload = case.api.destroy_actors([11])
    assert cast("list[dict[str, object]]", payload["results"])[0]["error"] == "native timeout"
    assert case.adapter._connected_client is case.first  # noqa: SLF001
    case.connect.assert_not_called()


def test_converted_cleanup_hook_does_not_leak_into_finite_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A finished persistent operation cannot record another adapter's failure."""
    case = _cleanup_case(monkeypatch, tmp_path)
    case.connection.actor.destroy.side_effect = RuntimeError("persistent timeout")
    case.connection.namespace.execute("result = api.destroy_actors([11])")
    state = case.connection.adapter._persistent_connection  # noqa: SLF001
    assert state is not None
    retained = state._request_failure  # noqa: SLF001
    _finite_destroy_failure(monkeypatch)
    assert state._request_failure is retained  # noqa: SLF001


def test_local_actor_capability_failure_is_not_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A malformed handle is local evidence, not a native delete timeout."""
    case = _cleanup_case(monkeypatch, tmp_path)
    malformed = SimpleNamespace(id=11, type_id="vehicle.test", attributes={"role_name": "owned"})
    case.connection.world.get_actors.return_value.find.side_effect = None
    case.connection.world.get_actors.return_value.find.return_value = malformed
    outcome = case.connection.namespace.execute(
        "api.destroy_actors([11])\nresult = api.list_worlds()"
    )
    assert cast("dict[str, object]", outcome["result"])["worlds"] == ["Town10HD"]
    assert case.connection.adapter._connected_client is case.connection.first  # noqa: SLF001
    assert case.ownership.actor_ids() == ACTOR_IDS


def test_server_declined_destroy_result_is_not_parsed_as_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only native exceptions, not arbitrary response strings, restrict transport."""
    case = _cleanup_case(monkeypatch, tmp_path)
    case.connection.actor.destroy.return_value = False
    case.connection.first.apply_batch_sync.return_value = [
        SimpleNamespace(actor_id=11, error="server rejected destruction")
    ]
    outcome = case.connection.namespace.execute(
        "api.destroy_actors([11])\nresult = api.list_worlds()"
    )
    assert cast("dict[str, object]", outcome["result"])["worlds"] == ["Town10HD"]
    assert case.connection.adapter._connected_client is case.connection.first  # noqa: SLF001
    assert case.ownership.actor_ids() == ACTOR_IDS


def test_reconnect_after_cleanup_timeout_refuses_replacement_episode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A reconnect never grants a reused actor ID authority in a replacement world."""
    case = _cleanup_case(monkeypatch, tmp_path)
    _configure_cleanup(case, "sensor_stop").side_effect = RuntimeError("native timeout")
    case.connection.namespace.execute("result = api.destroy_actors([11])")
    replacement = Mock(id=18)
    case.connection.second.get_world.return_value = replacement
    outcome = case.connection.namespace.execute("api.destroy_actors([11])\nresult = 42")
    assert outcome["error_type"] == "simulator_restarted"
    assert outcome["retryable"] is False
    replacement.get_actors.assert_not_called()
    case.connection.second.apply_batch_sync.assert_not_called()
    _assert_retained(case, "sensor_stop")
