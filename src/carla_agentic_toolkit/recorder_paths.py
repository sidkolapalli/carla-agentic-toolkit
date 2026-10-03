"""Validate simulator-side recorder names without resolving them on the client host."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath

from carla_agentic_toolkit.errors import CarlaAdapterError


def _server_recorder_path(output_path: Path) -> str:
    """Join a validated recording name to the simulator's configured directory."""
    relative_path = _relative_recorder_path(output_path)
    directory = os.environ.get("CARLA_AGENTIC_TOOLKIT_RECORDER_DIR")
    if directory:
        base = directory.replace("\\", "/").rstrip("/")
        return f"{base}/{relative_path}"
    return relative_path


def _relative_recorder_path(output_path: Path) -> str:
    """Validate both host path dialects without resolving on the client filesystem."""
    path = PurePosixPath(output_path.as_posix().replace("\\", "/"))
    if not path.parts or "\x00" in str(path):
        msg = "Recorder path must not be empty or contain NUL bytes."
        raise CarlaAdapterError(msg)
    if PureWindowsPath(path).anchor:
        msg = "Recorder path must be relative to the configured recorder directory."
        raise CarlaAdapterError(msg)
    if ".." in path.parts:
        msg = "Recorder path must not contain '..' components."
        raise CarlaAdapterError(msg)
    return path.as_posix()
