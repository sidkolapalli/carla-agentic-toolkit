"""Diagnostic transport failures and local evidence faults keep distinct authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit.connection_journal import CONNECTION_FILENAME
from tests.test_persistent_reconnect import (
    _assert_terminal,
    _case,
    _durable_case,
    _establish,
    _result,
)

if TYPE_CHECKING:
    from pathlib import Path

COMPATIBLE_VERSION_READS = 2


@pytest.mark.parametrize("getter", ["get_client_version", "get_server_version"])
@pytest.mark.parametrize("error", [RuntimeError("rpc timeout"), OSError("connection reset")])
def test_diagnostic_timeout_drops_cache_without_world_or_later_getter(
    monkeypatch: pytest.MonkeyPatch, getter: str, error: Exception
) -> None:
    """Retain partial health while preventing the failed native stream's continued use."""
    case = _case(monkeypatch)
    getattr(case.first, getter).side_effect = error
    result = _result(case, "result = api.health_check()")
    assert result["connected"] is False
    assert case.adapter._connected_client is None  # noqa: SLF001
    case.first.get_world.assert_not_called()
    if getter == "get_client_version":
        case.first.get_server_version.assert_not_called()
    case.connect.assert_not_called()
    assert _result(case)["current_map"] == "Town10HD"
    case.connect.assert_called_once_with()


def test_diagnostic_timeout_cannot_reconnect_again_in_same_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second diagnostic call returns the failure without repeating native reads."""
    case = _case(monkeypatch)
    case.first.get_client_version.side_effect = RuntimeError("rpc timeout")
    outcome = case.namespace.execute("api.health_check()\nresult = api.health_check()")
    assert cast("dict[str, object]", outcome["result"])["ok"] is False
    case.first.get_client_version.assert_called_once_with()
    case.first.get_server_version.assert_not_called()
    case.connect.assert_not_called()


def test_late_world_state_diagnostic_timeout_stops_native_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after compatible episode checks still invalidates the native stream."""
    case = _case(monkeypatch)
    _establish(case)
    case.first.get_client_version.side_effect = [
        "0.9.16-client",
        "0.9.16-client",
        RuntimeError("late diagnostic timeout"),
    ]
    case.first.get_server_version.reset_mock()
    case.world.get_settings.reset_mock()
    result = _result(case)
    assert result["ok"] is False
    assert case.adapter._connected_client is None  # noqa: SLF001
    assert case.first.get_server_version.call_count == COMPATIBLE_VERSION_READS
    case.world.get_settings.assert_not_called()
    case.connect.assert_not_called()


@pytest.mark.parametrize("damage", ["missing", "malformed", "symlink"])
def test_ignored_connection_read_failure_is_terminal_not_transport(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, damage: str
) -> None:
    """Script exception handling cannot turn local evidence loss into native authority."""
    case = _durable_case(monkeypatch, tmp_path)
    _establish(case)
    path = tmp_path / CONNECTION_FILENAME
    _damage_connection(path, damage)
    case.first.get_world.reset_mock()
    outcome = case.namespace.execute(
        "try:\n    api.get_world_state()\nexcept:\n    pass\nresult = 42"
    )
    _assert_terminal(outcome, "persistent_connection_evidence_failed")
    assert case.adapter._connected_client is case.first  # noqa: SLF001
    case.first.get_world.assert_not_called()
    case.connect.assert_not_called()


def _damage_connection(path: Path, damage: str) -> None:
    if damage == "missing":
        path.unlink()
    elif damage == "malformed":
        path.write_text('{"schema_version":1,"world_id":false,"restart":null}')
    else:
        other = path.with_name("other.json")
        path.rename(other)
        path.symlink_to(other)


@pytest.mark.parametrize("error", [ValueError("invalid JSON evidence"), TypeError("invalid value")])
def test_ignored_connection_persist_failure_is_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: Exception
) -> None:
    """Persistence validation faults freeze native use just like a failed durable write."""
    case = _durable_case(monkeypatch, tmp_path)
    writer = Mock(side_effect=error)
    monkeypatch.setattr("carla_agentic_toolkit.connection_journal.write_control", writer)
    outcome = case.namespace.execute(
        "try:\n    api.get_world_state()\nexcept:\n    pass\nresult = 42"
    )
    assert outcome["ok"] is False
    assert outcome["error_type"] == "persistent_connection_evidence_failed"
    assert outcome["retryable"] is False
    assert case.adapter._connected_client is case.first  # noqa: SLF001
    writer.assert_called_once()
