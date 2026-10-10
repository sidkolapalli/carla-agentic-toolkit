"""Validated persistent-session limits and bounded, symlink-resistant file IPC."""

from __future__ import annotations

import json
import math
import os
import stat
import uuid
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.runtime_ports import validate_optional_port

if TYPE_CHECKING:
    from pathlib import Path

MAX_MESSAGE_BYTES = 1024 * 1024
MAX_CODE_BYTES = 64 * 1024
MAX_SESSION_SECONDS = 3600.0
MAX_REQUEST_SECONDS = 60.0
POLL_SECONDS = 0.02
PROTOCOL_VERSION = 1
MAX_CARLA_PORT = 65533
MAX_TCP_PORT = 65535
MAX_SESSION_TM_PORTS = 16


class ProtocolError(ValueError):
    """An untrusted or malformed session message requires termination."""


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Explicit wall-clock budgets; CPU/memory/process caps remain sandbox-wide."""

    host: str = "127.0.0.1"
    port: int = 2000
    idle_timeout_seconds: float = 60.0
    absolute_timeout_seconds: float = 300.0
    request_timeout_seconds: float = 30.0
    traffic_manager_ports: tuple[int, ...] = ()
    streaming_port: int | None = None
    secondary_port: int | None = None

    @classmethod
    def parse(cls, value: dict[str, object]) -> SessionConfig:
        """Reject extra fields and unbounded values before acquiring a simulator lease."""
        if not isinstance(value, dict) or set(value) - {item.name for item in fields(cls)}:
            message = "Session config contains unsupported fields."
            raise ValueError(message)
        normalized = dict(value)
        if "traffic_manager_ports" in normalized:
            normalized["traffic_manager_ports"] = _traffic_ports(
                normalized["traffic_manager_ports"]
            )
        result = cls(**cast("dict[str, Any]", normalized))
        result.validate()
        return result

    def validate(self) -> None:
        """Require explicit finite endpoints and resource deadlines."""
        self._validate_endpoint()
        validate_optional_port(self.streaming_port, "streaming_port")
        validate_optional_port(self.secondary_port, "secondary_port")
        if not isinstance(self.traffic_manager_ports, tuple):
            message = "traffic_manager_ports must be an immutable tuple in SessionConfig."
            raise TypeError(message)
        _traffic_ports(self.traffic_manager_ports)
        bounded_seconds(self.idle_timeout_seconds, "idle_timeout_seconds", MAX_SESSION_SECONDS)
        bounded_seconds(
            self.absolute_timeout_seconds, "absolute_timeout_seconds", MAX_SESSION_SECONDS
        )
        bounded_seconds(
            self.request_timeout_seconds, "request_timeout_seconds", MAX_REQUEST_SECONDS
        )

    def _validate_endpoint(self) -> None:
        if not isinstance(self.host, str) or not self.host.strip():
            message = "host must be a non-empty string."
            raise ValueError(message)
        if type(self.port) is not int or not 1 <= self.port <= MAX_CARLA_PORT:
            message = "port must be an integer in 1..65533."
            raise ValueError(message)


def _traffic_ports(value: object) -> tuple[int, ...]:
    message = (
        f"traffic_manager_ports must be a list of at most {MAX_SESSION_TM_PORTS} "
        f"integers in 1..{MAX_TCP_PORT}."
    )
    if not isinstance(value, list | tuple) or len(value) > MAX_SESSION_TM_PORTS:
        raise ValueError(message)
    if not all(_valid_traffic_port(port) for port in value):
        raise ValueError(message)
    return cast("tuple[int, ...]", tuple(value))


def _valid_traffic_port(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_TCP_PORT


def bounded_seconds(value: object, name: str, maximum: float) -> float:
    """Return a strictly positive finite duration within its configured hard cap."""
    message = f"{name} must be a finite duration in (0, {maximum}]."
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(message)
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(message)
    return float(value)


def validate_code(code: object) -> str:
    """Bound incoming source before parsing or atomically publishing it."""
    if not isinstance(code, str) or len(code.encode("utf-8")) > MAX_CODE_BYTES:
        message = f"Script code must be a string of at most {MAX_CODE_BYTES} UTF-8 bytes."
        raise ValueError(message)
    return code


def read_message(path: Path) -> dict[str, object]:
    """Read at most one MiB from a regular, non-symlink file."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_MESSAGE_BYTES:
                message = "Session message exceeds its file limit."
                raise ProtocolError(message)
            payload = stream.read(MAX_MESSAGE_BYTES + 1)
        return _decode_message(payload)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(str(exc)) from exc


def _decode_message(payload: bytes) -> dict[str, object]:
    if len(payload) > MAX_MESSAGE_BYTES:
        message = "Session message exceeds its byte limit."
        raise ProtocolError(message)
    value = json.loads(payload)
    if not isinstance(value, dict):
        message = "Session message must be an object."
        raise ProtocolError(message)
    return cast("dict[str, object]", value)


def write_message(path: Path, value: dict[str, object]) -> None:
    """Atomically replace one bounded slot; never open an attacker-selected temporary file."""
    encoded = json.dumps(value, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_MESSAGE_BYTES:
        message = "Session message exceeds its byte limit."
        raise ProtocolError(message)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
