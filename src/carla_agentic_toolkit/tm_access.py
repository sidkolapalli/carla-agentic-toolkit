"""Traffic Manager connection diagnostics under the runner's actual network policy."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import CarlaAdapterError, TrafficManagerUnavailableError

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaTrafficManager

TCP_CONNECT_PORTS_ENV = "CARLA_AGENTIC_TOOLKIT_TCP_CONNECT_PORTS"
MAX_TCP_PORT = 65535


def require_traffic_manager_port(port: object) -> None:
    """Reject excluded ports locally; this evidence cannot grant kernel capabilities."""
    if not _valid_port(port):
        message = f"traffic_manager_port must be an integer in 1..{MAX_TCP_PORT}."
        raise CarlaAdapterError(message)
    policy = os.environ.get(TCP_CONNECT_PORTS_ENV)
    if policy is None:
        return
    ports = _read_port_policy(policy)
    if port not in ports:
        message = (
            f"Traffic Manager port {port} is not allowed by the sandbox connect policy. "
            "Include this port in traffic_manager_ports when creating the execution or session."
        )
        raise CarlaAdapterError(
            message,
            details={"error_type": "traffic_manager_port_not_allowed", "retryable": False},
        )


def _read_port_policy(policy: str) -> list[int]:
    try:
        ports = json.loads(policy)
    except ValueError as exc:
        raise _policy_error() from exc
    if not isinstance(ports, list) or not all(_valid_port(port) for port in ports):
        raise _policy_error()
    return ports


def _valid_port(port: object) -> bool:
    return type(port) is int and 1 <= port <= MAX_TCP_PORT


def _policy_error() -> CarlaAdapterError:
    return CarlaAdapterError(
        "Sandbox connect-port evidence is invalid; Traffic Manager access refused.",
        details={"error_type": "traffic_manager_network_policy_error", "retryable": False},
    )


def get_traffic_manager(client: CarlaClient, port: int) -> CarlaTrafficManager:
    """Normalize only recognizable bind failures, retaining unrelated native diagnostics."""
    require_traffic_manager_port(port)
    try:
        return client.get_trafficmanager(port)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        if "bind error" in str(exc).lower():
            raise TrafficManagerUnavailableError(port, str(exc)) from exc
        raise CarlaAdapterError(str(exc)) from exc
