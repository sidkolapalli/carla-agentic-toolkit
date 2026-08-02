"""Recorder tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_mcp.adapter import CarlaAdapterError, RecorderAdapter
from carla_mcp.models import RecordingInfo, ToolResult

if TYPE_CHECKING:
    from pathlib import Path

    from carla_mcp.snapshots import RunSnapshots


def record_episode(
    adapter: RecorderAdapter,
    snapshots: RunSnapshots,
    output_path: Path,
) -> ToolResult:
    """Start CARLA recording and publish the recording snapshot."""
    try:
        recording = adapter.record_episode(output_path)
    except CarlaAdapterError as exc:
        return _adapter_error("record_episode_failed", exc)
    return _recording_result(recording, snapshots)


def stop_recording(adapter: RecorderAdapter, snapshots: RunSnapshots) -> ToolResult:
    """Stop CARLA recording and update the recording snapshot."""
    try:
        recording = adapter.stop_recording()
    except CarlaAdapterError as exc:
        return _adapter_error("stop_recording_failed", exc)
    return _recording_result(recording, snapshots)


def _recording_result(recording: RecordingInfo, snapshots: RunSnapshots) -> ToolResult:
    """Publish a recording snapshot result."""
    payload = recording.to_dict()
    snapshots.register_snapshot(f"carla-snapshot://recordings/{recording.recording_id}", payload)
    return ToolResult.ok(payload)


def _adapter_error(error_type: str, exc: CarlaAdapterError) -> ToolResult:
    """Convert adapter failures into MCP-style tool errors."""
    return ToolResult.error(
        {
            "error_type": error_type,
            "message": str(exc),
            "retryable": True,
        }
    )
