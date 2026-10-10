"""Trusted server endpoint defaults without treating invalid explicit values as omission."""

from __future__ import annotations

import os
import re
from enum import Enum

HOST_ENV = "CARLA_AGENTIC_TOOLKIT_HOST"
PORT_ENV = "CARLA_AGENTIC_TOOLKIT_PORT"
MAX_CARLA_PORT = 65533


class EndpointOmitted(Enum):
    """A Python-call omission distinct from every user-supplied endpoint value."""

    VALUE = "omitted"


OMITTED = EndpointOmitted.VALUE


def default_host() -> str:
    """Read a nonblank configured host only when the caller omitted that field."""
    value = os.environ.get(HOST_ENV, "127.0.0.1")
    if not value.strip():
        message = f"{HOST_ENV} must be a non-empty host string."
        raise ValueError(message)
    return value


def default_port() -> int:
    """Read a bounded ASCII decimal RPC port without silently replacing malformed config."""
    value = os.environ.get(PORT_ENV, "2000")
    if re.fullmatch(r"[0-9]{1,5}", value) is None or not 1 <= int(value) <= MAX_CARLA_PORT:
        message = f"{PORT_ENV} must be an ASCII decimal integer in 1..{MAX_CARLA_PORT}."
        raise ValueError(message)
    return int(value)


def resolve_endpoint(host: str | EndpointOmitted, port: int | EndpointOmitted) -> tuple[str, int]:
    """Resolve omissions; leave explicit values for the existing strict request validation."""
    return (
        default_host() if host is OMITTED else host,
        default_port() if port is OMITTED else port,
    )


def host_error(host: object) -> str | None:
    """Validate an explicit host without changing or resolving it."""
    if not isinstance(host, str) or not host.strip():
        return "host must be a non-empty string."
    return None


def port_error(port: object) -> str | None:
    """Preserve the launcher's strict RPC port range and rejection of booleans."""
    if isinstance(port, bool) or not isinstance(port, int):
        return f"port must be an integer in 1..{MAX_CARLA_PORT}."
    if not 1 <= port <= MAX_CARLA_PORT:
        return f"port must be an integer in 1..{MAX_CARLA_PORT}."
    return None
