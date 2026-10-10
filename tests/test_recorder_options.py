"""Recorder options survive the script facade without changing server-path evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api

ACCEPTED_PATH = "/simulator/recordings/accepted.log"
REQUESTED_PATH = "/simulator/recordings/requested.log"


@dataclass
class NativeRecorder:
    """Expose CARLA's boolean option and a single observable recorder RPC."""

    accepted_path: str = ACCEPTED_PATH
    calls: list[tuple[str, bool]] = field(default_factory=list)
    failure: RuntimeError | None = None

    def start_recorder(self, path: str, *, additional_data: bool = False) -> str:
        """Record the native arguments and return the simulator-accepted path."""
        self.calls.append((path, additional_data))
        if self.failure is not None:
            raise self.failure
        return self.accepted_path


def _adapter(monkeypatch: pytest.MonkeyPatch, client: NativeRecorder) -> PythonCarlaAdapter:
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_RECORDER_DIR", "/simulator/recordings")
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", lambda: client)
    return adapter


@pytest.mark.parametrize(
    ("options", "expected"),
    [({}, False), ({"additional_data": False}, False), ({"additional_data": True}, True)],
)
def test_native_recorder_receives_default_or_explicit_additional_data(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, bool], *, expected: bool
) -> None:
    """The adapter forwards both booleans once and keeps the native accepted path."""
    client = NativeRecorder()
    recording = _adapter(monkeypatch, client).record_episode(Path("requested.log"), **options)

    assert client.calls == [(REQUESTED_PATH, expected)]
    assert (recording.path, recording.active) == (Path(ACCEPTED_PATH), True)


@pytest.mark.parametrize(
    ("options", "expected"),
    [({}, False), ({"additional_data": False}, False), ({"additional_data": True}, True)],
)
def test_script_facade_forwards_recorder_option_and_publishes_accepted_path(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, bool], *, expected: bool
) -> None:
    """The real facade does not swallow the option or invent a local recorder path."""
    client = NativeRecorder()
    snapshots = RunSnapshots()
    api = build_api(_adapter(monkeypatch, client), snapshots)

    result = api.record_episode("requested.log", **options)

    assert client.calls == [(REQUESTED_PATH, expected)]
    assert (result["path"], result["active"]) == (ACCEPTED_PATH, True)
    assert snapshots.read_snapshot("carla-snapshot://recordings/accepted") == result


def test_native_recorder_failure_is_not_retried_or_published_as_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed recorder start retains its structured error and no success snapshot."""
    client = NativeRecorder(failure=RuntimeError("recorder unavailable"))
    snapshots = RunSnapshots()
    api = build_api(_adapter(monkeypatch, client), snapshots)

    result = api.record_episode("requested.log", additional_data=True)

    _assert_failed_start(result, snapshots)
    assert client.calls == [(REQUESTED_PATH, True)]


def _assert_failed_start(result: dict[str, object], snapshots: RunSnapshots) -> None:
    """Verify a failed start retains its error without an active snapshot."""
    assert result["ok"] is False
    assert result["error_type"] == "record_episode_failed"
    assert "recorder unavailable" in str(result["error"])
    assert snapshots.snapshot_uris() == ()


def test_empty_native_response_with_additional_data_stays_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new option cannot make a rejected simulator-side path look active."""
    client = NativeRecorder(accepted_path="")
    adapter = _adapter(monkeypatch, client)

    with pytest.raises(CarlaAdapterError, match="did not open recorder path"):
        adapter.record_episode(Path("requested.log"), additional_data=True)
    assert client.calls == [(REQUESTED_PATH, True)]


@pytest.mark.parametrize("value", [None, "true", 0, 1])
def test_additional_data_requires_a_boolean_before_connecting(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """Native boolean coercion must not silently select a different recording format."""
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", _unexpected_connection)

    with pytest.raises(CarlaAdapterError, match="additional_data must be a boolean"):
        adapter.record_episode(Path("requested.log"), additional_data=cast("bool", value))


def _unexpected_connection() -> None:
    pytest.fail("recorder option must be validated before connecting")
