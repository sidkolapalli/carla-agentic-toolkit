"""The trusted parent resolves the CARLA address so the sandboxed child needs no DNS."""

from __future__ import annotations

import socket
import sys
from typing import TYPE_CHECKING
from unittest.mock import Mock

from carla_agentic_toolkit import adapter_connection, sandbox, script_runner, simulator_lease
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.sandbox import RunnerCommandRequest
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _resolver(*addresses: str) -> Mock:
    family = {True: socket.AF_INET6, False: socket.AF_INET}
    return Mock(
        return_value=[
            (family[":" in address], socket.SOCK_STREAM, 6, "", (address, 2000))
            for address in addresses
        ]
    )


def _command(host: str, tmp_path: Path) -> list[str]:
    request = RunnerCommandRequest(
        runner=tmp_path / "runner",
        script_path=tmp_path / "script.py",
        work_dir=tmp_path,
        output_dir=tmp_path,
        host=host,
        port=2000,
        timeout_seconds=5.0,
        traffic_manager_ports=(),
        recorder_dir=None,
    )
    return sandbox._runner_command(request)  # noqa: SLF001 -- The launch arguments are the contract.


def test_runner_command_passes_the_resolved_address_and_keeps_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hostname is resolved by the parent; the child keeps it only as a label."""
    monkeypatch.setattr(simulator_lease.socket, "getaddrinfo", _resolver("10.0.0.5"))
    command = _command("carla.example", tmp_path)
    assert command[command.index("--connect-host") + 1] == "10.0.0.5"
    assert command[command.index("--host") + 1] == "carla.example"


def test_loopback_aliases_connect_over_ipv4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A localhost alias resolves to both loopback families; IPv4 is chosen."""
    monkeypatch.setattr(simulator_lease.socket, "getaddrinfo", _resolver("::1", "127.0.0.1"))
    command = _command("localhost", tmp_path)
    assert command[command.index("--connect-host") + 1] == "127.0.0.1"


def test_adapter_dials_the_connect_host_but_reports_the_configured_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Labels and registry paths stay keyed by the host the user configured."""
    factory = Mock(side_effect=RuntimeError("stop after the dial"))
    monkeypatch.setattr(adapter_connection, "_carla_client_factory", Mock(return_value=factory))
    adapter = PythonCarlaAdapter(host="carla.example", connect_host="10.0.0.5")
    CarlaScriptApi(adapter, RunSnapshots()).health_check()
    factory.assert_called_once_with("10.0.0.5", 2000)
    assert adapter.host == "carla.example"


def test_child_arguments_accept_a_connect_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both the finite and session children read the parent's resolved address."""
    argv = ["script_runner", "--script", "s.py", "--connect-host", "10.0.0.5"]
    monkeypatch.setattr(sys, "argv", argv)
    assert script_runner._parse_args().connect_host == "10.0.0.5"  # noqa: SLF001 -- CLI contract.
