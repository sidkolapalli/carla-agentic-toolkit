"""Session file messages retain state and bounded owned-actor telemetry."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.persistent_runner import SessionLoop
from carla_agentic_toolkit.session_protocol import ProtocolError, read_message, write_message

if TYPE_CHECKING:
    from pathlib import Path


class Api:
    """Tiny retained API with observable telemetry reads."""

    calls = 0

    def get_vehicle_telemetry(self, actor_id: int) -> dict[str, object]:
        """Return current telemetry without any tick ownership."""
        self.calls += 1
        return {"actor_id": actor_id, "sample": self.calls}


def _loop(tmp_path: Path) -> tuple[SessionLoop, Api]:
    ownership = RunOwnership(tmp_path / "owned.json")
    ownership.add([7])
    api = Api()
    return SessionLoop(tmp_path, "session", api, ownership), api


def _request(tmp_path: Path, sequence: int, **values: object) -> None:
    write_message(
        tmp_path / "request.json",
        {
            "version": 1,
            "session_id": "session",
            "sequence": sequence,
            **values,
        },
    )


def test_file_requests_continue_one_namespace(tmp_path: Path) -> None:
    """The file IPC exchanges compact results while local state remains in the child."""
    loop, _api = _loop(tmp_path)
    _request(tmp_path, 1, action="execute", code="vehicle = 7")
    loop.poll_once(0.0)
    _request(tmp_path, 2, action="execute", code="result = vehicle")
    loop.poll_once(1.0)
    payload = read_message(tmp_path / "response.json")
    result = cast("dict[str, object]", payload["payload"])
    assert (payload["sequence"], result["result"]) == (2, 7)


def test_telemetry_is_owned_rate_limited_and_latest_only(tmp_path: Path) -> None:
    """Repeated reads neither enqueue unlimited samples nor bypass the sampling interval."""
    loop, api = _loop(tmp_path)
    _request(tmp_path, 1, action="telemetry", actor_id=7, interval_seconds=0.2)
    loop.poll_once(0.0)
    loop.poll_once(0.1)
    loop.poll_once(0.2)
    assert (api.calls, read_message(tmp_path / "telemetry.json")["sequence"]) == (2, 2)


def test_unowned_telemetry_and_repeated_sequence_fail_closed(tmp_path: Path) -> None:
    """Malformed sequencing or cross-owner reads terminate the worker protocol."""
    loop, _api = _loop(tmp_path)
    _request(tmp_path, 1, action="telemetry", actor_id=99, interval_seconds=0.2)
    with pytest.raises(ProtocolError, match="owned"):
        loop.poll_once(0.0)
    _request(tmp_path, 1, action="execute", code="result = 7")
    with pytest.raises(ProtocolError, match="sequence"):
        loop.poll_once(0.0)
