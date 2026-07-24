"""Behavior specs for recorder and evidence MCP tools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from carla_mcp.models import RecordingInfo
from carla_mcp.session import CarlaSession
from carla_mcp.tools.evidence import export_evidence_packet
from carla_mcp.tools.recorder import record_episode, stop_recording

RECORDING_ID: Final = "recording-001"


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


def test_record_episode_registers_recording_resource(tmp_path: Path) -> None:
    """Starting a recording should register the active recording resource."""
    session = CarlaSession()
    adapter = RecorderAdapter()
    output_path = tmp_path / "episode.log"

    result = record_episode(adapter=adapter, session=session, output_path=output_path)

    assert result.is_error is False
    assert result.structured_content["active"] is True
    assert session.read_resource(f"carla://recordings/{RECORDING_ID}") == result.structured_content


def test_stop_recording_updates_recording_resource(tmp_path: Path) -> None:
    """Stopping a recording should publish the inactive recording resource."""
    session = CarlaSession()
    adapter = RecorderAdapter(started_recording=tmp_path / "episode.log")

    result = stop_recording(adapter=adapter, session=session)

    assert result.is_error is False
    assert result.structured_content["active"] is False
    assert session.read_resource(f"carla://recordings/{RECORDING_ID}") == result.structured_content


def test_export_evidence_packet_writes_manifest_and_resource(tmp_path: Path) -> None:
    """Evidence export should write a compact manifest and expose its resource."""
    session = CarlaSession()
    session.register_resource("carla://session/status", {"connected": True})
    session.register_resource("carla://world/current", {"current_map": "Town10HD_Opt"})

    result = export_evidence_packet(session=session, output_dir=tmp_path)

    manifest_path = Path(str(result.structured_content["manifest_path"]))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert result.is_error is False
    assert manifest["resources"] == ["carla://session/status", "carla://world/current"]
    resource_uri = str(result.structured_content["resource_uri"])
    assert session.read_resource(resource_uri) == result.structured_content
