"""Bounded, atomic private files shared by lifecycle clients and trusted workers."""

from __future__ import annotations

import json
import os
import stat
import uuid
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from pathlib import Path

MAX_CONTROL_BYTES = 65_536


def read_control(path: Path) -> dict[str, object]:
    """Read one bounded JSON object without following a file symlink."""
    if path.is_symlink():
        message = "Control files cannot be symbolic links."
        raise ValueError(message)
    descriptor = os.open(
        path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    )
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            message = "Control files must be regular bounded JSON files."
            raise ValueError(message)
        payload = stream.read(MAX_CONTROL_BYTES + 1)
    if len(payload) > MAX_CONTROL_BYTES:
        message = "Control files require bounded JSON."
        raise ValueError(message)
    return _object(json.loads(payload))


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        message = "Control file must contain a JSON object."
        raise TypeError(message)
    return cast("dict[str, object]", value)


def write_control(path: Path, value: dict[str, object]) -> None:
    """Atomically publish a bounded update; readers never observe partial JSON."""
    encoded = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    if len(encoded) > MAX_CONTROL_BYTES:
        message = "Control output exceeds its bounded JSON limit."
        raise ValueError(message)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def create_stop_marker(job: Path) -> None:
    """Request cancellation without asserting that the worker has terminated."""
    descriptor = os.open(
        job / "stop", os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    os.close(descriptor)


def private_directory(path: Path) -> Path:
    """Require local-owner-only storage for provider and simulator control state."""
    if path.is_symlink():
        message = "Managed state cannot use a symbolic-link directory."
        raise ValueError(message)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        message = "Managed state requires ownership by this user and permissions 0700."
        raise PermissionError(message)
    return path
