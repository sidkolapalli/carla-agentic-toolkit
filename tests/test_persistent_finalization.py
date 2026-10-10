"""Worker listener finalization shares the persistent native-failure boundary."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import persistent_runner
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.session_protocol import write_message
from tests.test_persistent_reconnect import ReconnectCase, _case, _result
from tests.test_persistent_request_errors import (
    ACTOR_IDS,
    WORLD_ID,
    CleanupCase,
    _assert_frozen,
    _assert_owned_journals,
    _cleanup_case,
)

if TYPE_CHECKING:
    from pathlib import Path


def _subscribe_pair(case: ReconnectCase, other: Mock, *, persistent: bool) -> None:
    actors = {ACTOR_IDS[0]: case.actor, ACTOR_IDS[1]: other}
    case.world.get_actors.return_value.find.side_effect = actors.get
    for actor_id, actor in actors.items():
        actor.type_id = "sensor.other.gnss"
        actor.is_listening.return_value = True
        if persistent:
            assert (
                _result(case, f"result = api.subscribe_sensor({actor_id})")["sensor_id"] == actor_id
            )
        else:
            case.adapter.subscribe_sensor(actor_id)


def _run_worker_finalization(
    case: CleanupCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "session.json"
    write_message(
        config,
        {
            "absolute_deadline_monotonic": time.monotonic() + 60.0,
            "request_timeout_seconds": 10.0,
            "session_id": "finalization-test",
        },
    )
    args = SimpleNamespace(
        script=config,
        ownership_file=case.ownership._path,  # noqa: SLF001 -- initialized real durable journal.
        host="127.0.0.1",
        port=2000,
        timeout_seconds=10.0,
    )
    monkeypatch.setattr(persistent_runner, "_parse_args", lambda: args)
    monkeypatch.setattr(
        persistent_runner, "PythonCarlaAdapter", lambda **_: case.connection.adapter
    )
    monkeypatch.setattr(
        persistent_runner.SessionLoop, "run", Mock(side_effect=RuntimeError("serving ended"))
    )
    persistent_runner.main()


def _finalization_counts(case: CleanupCase) -> tuple[int, int, int]:
    connection = case.connection
    return (
        connection.first.get_world.call_count,
        connection.actor.stop.call_count,
        case.other.stop.call_count,
    )


def _assert_pair_retained(case: CleanupCase) -> None:
    adapter = case.connection.adapter
    assert adapter._subscribed_sensor_handles == {  # noqa: SLF001
        ACTOR_IDS[0]: case.connection.actor,
        ACTOR_IDS[1]: case.other,
    }
    assert adapter._subscription_world_ids == dict.fromkeys(ACTOR_IDS, WORLD_ID)  # noqa: SLF001
    assert tuple(adapter._sensor_subscriptions) == ACTOR_IDS  # noqa: SLF001
    _assert_owned_journals(case)


@pytest.mark.parametrize("error_type", [RuntimeError, OSError])
def test_persistent_worker_finalization_stops_after_first_native_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error_type: type[Exception]
) -> None:
    """The actual worker finally path cannot continue RPCs on its failed stream."""
    case = _cleanup_case(monkeypatch, tmp_path)
    _subscribe_pair(case.connection, case.other, persistent=True)
    failure = error_type("first Stop timeout")
    frozen_counts: list[tuple[int, int, int]] = []

    def fail_stop() -> None:
        frozen_counts.append(_finalization_counts(case))
        raise failure

    case.connection.actor.stop.side_effect = fail_stop
    with pytest.raises((CarlaAdapterError, OSError), match="first Stop timeout"):
        _run_worker_finalization(case, monkeypatch, tmp_path)
    assert frozen_counts == [_finalization_counts(case)]
    case.other.stop.assert_not_called()
    _assert_pair_retained(case)
    _assert_frozen(case.connection, failure)


def test_finite_listener_close_continues_after_one_stop_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct finite cleanup still attempts other listeners after a failed Stop."""
    case = _case(monkeypatch)
    other = Mock(id=ACTOR_IDS[1])
    _subscribe_pair(case, other, persistent=False)
    case.actor.stop.side_effect = RuntimeError("first Stop timeout")
    with pytest.raises(CarlaAdapterError, match="first Stop timeout"):
        case.adapter.close_sensor_subscriptions()
    other.stop.assert_called_once_with()
    assert tuple(case.adapter._sensor_subscriptions) == (ACTOR_IDS[0],)  # noqa: SLF001
    assert case.adapter._connected_client is case.first  # noqa: SLF001
    case.connect.assert_not_called()
