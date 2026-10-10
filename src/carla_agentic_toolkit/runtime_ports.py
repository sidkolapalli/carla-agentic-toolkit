"""Validated CARLA TCP layout used by finite and persistent sandbox launchers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_TRAFFIC_MANAGER_PORT = 8000
MAX_TCP_PORT = 65535


def optional_port_error(value: object, name: str) -> str | None:
    """Reject optional permissions other than null or an exact TCP port integer."""
    if value is not None and (type(value) is not int or not 1 <= value <= MAX_TCP_PORT):
        return f"{name} must be null or an integer in 1..{MAX_TCP_PORT}."
    return None


def validate_optional_port(value: object, name: str) -> None:
    """Validate one optional port before acquiring resources or creating permissions."""
    error = optional_port_error(value, name)
    if error is not None:
        raise ValueError(error)


def allowed_tcp_ports(
    *,
    port: int,
    traffic_manager_ports: Sequence[int],
    streaming_port: int | None = None,
    secondary_port: int | None = None,
) -> tuple[int, ...]:
    """Replace each adjacent default only when that endpoint is explicitly supplied."""
    validate_optional_port(streaming_port, "streaming_port")
    validate_optional_port(secondary_port, "secondary_port")
    ports = {
        port,
        _configured_port(streaming_port, port + 1),
        _configured_port(secondary_port, port + 2),
        DEFAULT_TRAFFIC_MANAGER_PORT,
    }
    ports.update(int(item) for item in traffic_manager_ports)
    return tuple(sorted(item for item in ports if 0 < item <= MAX_TCP_PORT))


def _configured_port(value: int | None, default: int) -> int:
    """Use an adjacent port only when no endpoint override was supplied."""
    return default if value is None else value
