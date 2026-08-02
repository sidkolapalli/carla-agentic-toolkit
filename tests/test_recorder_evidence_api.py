"""Behavior specs for recorder and evidence script facade operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from carla_mcp.adapter import PythonCarlaAdapter
from carla_mcp.errors import CarlaAdapterError
from carla_mcp.models import RecordingInfo
from carla_mcp.snapshots import RunSnapshots
from tests.api_helpers import build_api

RECORDING_ID: Final = "recording-001"
REQUESTED_RECORDING: Final = "live-mcp/episode.log"
ACCEPTED_RECORDING: Final = "E:/CARLA_0.9.16/live-mcp/episode.log"
RECORDER_REJECTED: Final = "CARLA did not open recorder path"


@dataclass
@dataclass
class RecorderClient:
    """Fake official client recorder return contract."""

    accepted_path: str
    requested_path: str | None = None

    def start_recorder(self, path: str) -> str:
        """Return the path accepted by the simulator server."""
        self.requested_path = path
        return self.accepted_path

    def stop_recorder(self) -> None:
        """Stop the fake recorder."""


@dataclass
class RecorderAdapter:
    """Test double for CARLA recorder operations."""

    started_recording: Path | None = None

    def record_episode(self, output_path: Path) -> RecordingInfo:
        """Start recording to a managed path."""
        self.started_recording = output_path
        return RecordingInfo(recording_id=RECORDING_ID, path=output_path, active=True)

    def stop_recording(self) -> RecordingInfo:
        """Stop the active recording."""
        if self.started_recording is None:
            return RecordingInfo(recording_id=RECORDING_ID, path=Path("missing.log"), active=False)
        return RecordingInfo(recording_id=RECORDING_ID, path=self.started_recording, active=False)


def test_python_adapter_rejects_empty_recorder_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty official start_recorder result must never report active recording."""
    client = RecorderClient(accepted_path="")
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", lambda: client)

    with pytest.raises(CarlaAdapterError, match=RECORDER_REJECTED):
        adapter.record_episode(Path(REQUESTED_RECORDING))


def test_python_adapter_uses_server_recorder_dir_and_accepted_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-host recording should use and report the simulator-side path."""
    client = RecorderClient(accepted_path=ACCEPTED_RECORDING)
    adapter = PythonCarlaAdapter()
    monkeypatch.setenv("CARLA_MCP_RECORDER_DIR", "E:/CARLA_0.9.16")
    monkeypatch.setattr(adapter, "_client", lambda: client)

    recording = adapter.record_episode(Path(REQUESTED_RECORDING))
    stopped = adapter.stop_recording()

    assert {
        "requested_path": client.requested_path,
        "accepted_path": recording.path,
        "started": recording.active,
        "same_recording": stopped.recording_id == recording.recording_id,
        "stopped_path": stopped.path,
        "stopped": not stopped.active,
    } == {
        "requested_path": ACCEPTED_RECORDING,
        "accepted_path": Path(ACCEPTED_RECORDING),
        "started": True,
        "same_recording": True,
        "stopped_path": recording.path,
        "stopped": True,
    }


def test_record_episode_registers_recording_snapshot(tmp_path: Path) -> None:
    """Starting a recording should register the active recording snapshot."""
    snapshots = RunSnapshots()
    adapter = RecorderAdapter()
    output_path = tmp_path / "episode.log"

    result = build_api(adapter, snapshots).record_episode(str(output_path))

    assert result["active"] is True
    assert snapshots.read_snapshot(f"carla-snapshot://recordings/{RECORDING_ID}") == result


def test_stop_recording_updates_recording_snapshot(tmp_path: Path) -> None:
    """Stopping a recording should publish the inactive recording snapshot."""
    snapshots = RunSnapshots()
    adapter = RecorderAdapter(started_recording=tmp_path / "episode.log")

    result = build_api(adapter, snapshots).stop_recording()

    assert result["active"] is False
    assert snapshots.read_snapshot(f"carla-snapshot://recordings/{RECORDING_ID}") == result


def test_export_evidence_packet_writes_manifest_and_snapshot(tmp_path: Path) -> None:
    """Evidence export should write a compact manifest and expose its snapshot."""
    snapshots = RunSnapshots()
    snapshots.register_snapshot("carla-snapshot://session/status", {"connected": True})
    snapshots.register_snapshot("carla-snapshot://world/current", {"current_map": "Town10HD_Opt"})

    result = build_api(RecorderAdapter(), snapshots).export_evidence_packet(str(tmp_path))

    manifest_path = Path(str(result["manifest_path"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["snapshots"] == [
        "carla-snapshot://session/status",
        "carla-snapshot://world/current",
    ]
    snapshot_uri = str(result["snapshot_uri"])
    assert snapshots.read_snapshot(snapshot_uri) == result
