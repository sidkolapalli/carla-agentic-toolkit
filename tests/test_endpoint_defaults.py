"""Trusted endpoint defaults apply only to omitted inputs without bypassing quarantine."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock

import pytest
from mcp import Client

from carla_agentic_toolkit import sandbox, server, windows_launcher
from carla_agentic_toolkit.sandbox import ExecutionRequest, ScriptOutcome
from carla_agentic_toolkit.session_protocol import SessionConfig
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from mcp.server import MCPServer
    from mcp.types import CallToolResult

HOST_ENV = "CARLA_AGENTIC_TOOLKIT_HOST"
PORT_ENV = "CARLA_AGENTIC_TOOLKIT_PORT"
CONFIGURED_HOST = "192.0.2.10"
CONFIGURED_PORT = 23400
EXPLICIT_HOST = "192.0.2.20"
EXPLICIT_PORT = 24500
LAYOUTS = [
    ({}, (CONFIGURED_HOST, CONFIGURED_PORT)),
    ({"host": EXPLICIT_HOST}, (EXPLICIT_HOST, CONFIGURED_PORT)),
    ({"port": EXPLICIT_PORT}, (CONFIGURED_HOST, EXPLICIT_PORT)),
    ({"host": EXPLICIT_HOST, "port": EXPLICIT_PORT}, (EXPLICIT_HOST, EXPLICIT_PORT)),
]
INVALID_CONFIG = [
    (HOST_ENV, ""),
    (HOST_ENV, " \t"),
    (PORT_ENV, ""),
    (PORT_ENV, "2000.0"),
    (PORT_ENV, "no-port"),
    (PORT_ENV, "0"),
    (PORT_ENV, "65534"),
    (PORT_ENV, "+2000"),
    (PORT_ENV, "\uff12\uff1000"),
]
INVALID_INPUTS = [
    {"host": None},
    {"host": True},
    {"host": ""},
    {"port": None},
    {"port": True},
    {"port": "2000"},
    {"port": 1.5},
]


@pytest.fixture(autouse=True)
def _clean_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(HOST_ENV, raising=False)
    monkeypatch.delenv(PORT_ENV, raising=False)


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HOST_ENV, CONFIGURED_HOST)
    monkeypatch.setenv(PORT_ENV, str(CONFIGURED_PORT))


def _capture_execution(monkeypatch: pytest.MonkeyPatch) -> list[ExecutionRequest]:
    requests: list[ExecutionRequest] = []
    monkeypatch.setattr(sandbox, "_sandbox_runner", lambda: Path("runner"))

    def execute(_code: str, request: ExecutionRequest, _runner: Path) -> ScriptOutcome:
        requests.append(request)
        return ScriptOutcome(ok=True, result=1, stdout="")

    monkeypatch.setattr(sandbox, "_execute_leased", execute)
    return requests


@pytest.mark.parametrize(("arguments", "expected"), LAYOUTS)
def test_finite_defaults_and_independent_explicit_overrides(
    monkeypatch: pytest.MonkeyPatch,
    arguments: dict[str, object],
    expected: tuple[str, int],
) -> None:
    """Only omitted fields read server-owned defaults before constructing the launch request."""
    _configure(monkeypatch)
    requests = _capture_execution(monkeypatch)
    outcome = sandbox.execute_script("result = 1", **cast("Any", arguments))
    assert outcome.ok is True
    assert (requests[0].host, requests[0].port) == expected


def test_unconfigured_finite_call_keeps_existing_loopback_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absence of trusted configuration preserves the old endpoint."""
    requests = _capture_execution(monkeypatch)
    assert sandbox.execute_script("result = 1").ok is True
    assert (requests[0].host, requests[0].port) == ("127.0.0.1", 2000)


@pytest.mark.parametrize("construction", ["parse", "direct"])
@pytest.mark.parametrize(("arguments", "expected"), LAYOUTS)
def test_session_defaults_apply_at_each_construction(
    monkeypatch: pytest.MonkeyPatch,
    construction: str,
    arguments: dict[str, object],
    expected: tuple[str, int],
) -> None:
    """Direct dataclass construction and parsed open config resolve the same defaults."""
    _configure(monkeypatch)
    config = _session_config(construction, arguments)
    assert (config.host, config.port) == expected


def _session_config(construction: str, arguments: dict[str, object]) -> SessionConfig:
    if construction == "parse":
        return SessionConfig.parse(arguments)
    config = SessionConfig(**cast("Any", arguments))
    config.validate()
    return config


async def _call_script(mcp: MCPServer, arguments: dict[str, object]) -> CallToolResult:
    async with Client(mcp) as client:
        return await client.call_tool("execute_carla_script", {"code": "result = 1", **arguments})


def _capture_tool(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    values: dict[str, object] = {}

    def execute(_code: str, **kwargs: object) -> ScriptOutcome:
        values.update(kwargs)
        return ScriptOutcome(ok=True, result=1, stdout="")

    monkeypatch.setattr(server, "execute_script", execute)
    return values


@pytest.mark.parametrize(("arguments", "expected"), LAYOUTS)
def test_served_mcp_omission_uses_configured_endpoint(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, object], expected: tuple[str, int]
) -> None:
    """The real MCP transport distinguishes omitted fields from explicit overrides."""
    _configure(monkeypatch)
    values = _capture_tool(monkeypatch)
    response = asyncio.run(_call_script(server.build_server(), arguments))
    assert response.is_error is False
    assert (values["host"], values["port"]) == expected


def test_served_defaults_are_selected_at_request_not_server_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Registration does not freeze the environment's earlier endpoint."""
    values = _capture_tool(monkeypatch)
    mcp = server.build_server()
    _configure(monkeypatch)
    response = asyncio.run(_call_script(mcp, {}))
    assert response.is_error is False
    assert (values["host"], values["port"]) == (CONFIGURED_HOST, CONFIGURED_PORT)


@pytest.mark.parametrize(("name", "value"), INVALID_CONFIG)
def test_malformed_configured_finite_endpoint_is_rejected_before_setup(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """Selected invalid configuration fails before the runner or lease is consulted."""
    setup = Mock(side_effect=AssertionError("resource setup must not run"))
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(sandbox, "_sandbox_runner", setup)
    monkeypatch.setattr(sandbox, "SimulatorLease", setup)
    outcome = sandbox.execute_script("result = 1")
    assert outcome.error_type == "invalid_request"
    assert name in (outcome.error or "")
    setup.assert_not_called()


@pytest.mark.parametrize("construction", ["parse", "direct"])
@pytest.mark.parametrize(("name", "value"), INVALID_CONFIG)
def test_malformed_configured_session_endpoint_is_not_silently_defaulted(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, construction: str
) -> None:
    """Neither direct nor parsed session creation can hide malformed configuration."""
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        _session_config(construction, {})


@pytest.mark.parametrize(("name", "value"), INVALID_CONFIG)
def test_malformed_mcp_defaults_never_reach_execution(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """A failed default factory cannot fall through to a native launcher."""
    _configure(monkeypatch)
    monkeypatch.setenv(name, value)
    execute = Mock(return_value=ScriptOutcome(ok=True, result=1, stdout=""))
    monkeypatch.setattr(server, "execute_script", execute)
    response = asyncio.run(_call_script(server.build_server(), {}))
    assert response.is_error is True
    execute.assert_not_called()


@pytest.mark.parametrize("arguments", INVALID_INPUTS)
def test_explicit_invalid_finite_inputs_never_fall_back_to_configuration(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, object]
) -> None:
    """Explicit invalid values remain invalid despite a usable configured fallback."""
    _configure(monkeypatch)
    requests = _capture_execution(monkeypatch)
    assert (
        sandbox.execute_script("result = 1", **cast("Any", arguments)).error_type
        == "invalid_request"
    )
    assert requests == []


@pytest.mark.parametrize("arguments", INVALID_INPUTS)
def test_explicit_invalid_session_inputs_never_fall_back_to_configuration(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, object]
) -> None:
    """An explicit session endpoint must pass existing strict validation."""
    _configure(monkeypatch)
    with pytest.raises(ValueError, match=r"host|port"):
        SessionConfig.parse(arguments)


@pytest.mark.parametrize("arguments", INVALID_INPUTS)
def test_explicit_invalid_mcp_endpoints_are_not_coerced_or_defaulted(
    monkeypatch: pytest.MonkeyPatch, arguments: dict[str, object]
) -> None:
    """Transport coercion cannot turn invalid endpoint permissions into a valid request."""
    _configure(monkeypatch)
    requests = _capture_execution(monkeypatch)
    monkeypatch.setattr(server, "execute_script", sandbox.execute_script)
    response = asyncio.run(_call_script(server.build_server(), arguments))
    assert response.is_error is True
    assert requests == []


@pytest.mark.parametrize("entrypoint", ["finite", "session", "mcp"])
def test_explicit_endpoint_overrides_unused_malformed_configuration(
    monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    """Unused configured values do not invalidate an explicit safe endpoint."""
    monkeypatch.setenv(HOST_ENV, "")
    monkeypatch.setenv(PORT_ENV, "not-a-port")
    arguments: dict[str, object] = {"host": EXPLICIT_HOST, "port": EXPLICIT_PORT}
    _assert_explicit_endpoint(monkeypatch, entrypoint, arguments)


def _assert_explicit_endpoint(
    monkeypatch: pytest.MonkeyPatch, entrypoint: str, arguments: dict[str, object]
) -> None:
    checks = {
        "session": _assert_explicit_session,
        "finite": _assert_explicit_finite,
        "mcp": _assert_explicit_mcp,
    }
    checks[entrypoint](monkeypatch, arguments)


def _assert_explicit_session(
    _monkeypatch: pytest.MonkeyPatch, arguments: dict[str, object]
) -> None:
    config = SessionConfig.parse(arguments)
    assert (config.host, config.port) == (EXPLICIT_HOST, EXPLICIT_PORT)


def _assert_explicit_finite(monkeypatch: pytest.MonkeyPatch, arguments: dict[str, object]) -> None:
    requests = _capture_execution(monkeypatch)
    assert sandbox.execute_script("result = 1", **cast("Any", arguments)).ok is True
    assert (requests[0].host, requests[0].port) == (EXPLICIT_HOST, EXPLICIT_PORT)


def _assert_explicit_mcp(monkeypatch: pytest.MonkeyPatch, arguments: dict[str, object]) -> None:
    values = _capture_tool(monkeypatch)
    assert asyncio.run(_call_script(server.build_server(), arguments)).is_error is False
    assert (values["host"], values["port"]) == (EXPLICIT_HOST, EXPLICIT_PORT)


@pytest.mark.skipif(sys.platform != "linux", reason="Kernel leases require Linux")
def test_configured_default_cannot_bypass_existing_alias_quarantine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A configured loopback alias finds the same durable dirty evidence, not a new owner."""
    monkeypatch.setenv(HOST_ENV, "localhost")
    monkeypatch.setenv(PORT_ENV, str(CONFIGURED_PORT))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(sandbox, "_sandbox_runner", lambda: Path("runner"))
    execution = Mock(return_value=ScriptOutcome(ok=True, result=1, stdout=""))
    monkeypatch.setattr(sandbox, "_execute_with_runner", execution)
    with SimulatorLease("127.0.0.1", CONFIGURED_PORT) as lease:
        lease.mark_dirty({"kind": "test-unresolved"})
    outcome = sandbox.execute_script("result = 1")
    assert outcome.error_type == "simulator_recovery_required"
    execution.assert_not_called()


@pytest.mark.parametrize(
    ("name", "value"),
    [(HOST_ENV, CONFIGURED_HOST), (PORT_ENV, str(CONFIGURED_PORT)), (HOST_ENV, ""), (PORT_ENV, "")],
)
def test_windows_launcher_forwards_endpoint_defaults_as_individual_env_arguments(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """Windows MCP-entry settings reach WSL unchanged, including malformed empty values."""
    _configure_launcher(monkeypatch)
    monkeypatch.setenv(name, value)
    launch = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(windows_launcher.subprocess, "run", launch)
    assert windows_launcher.run([]) == 0
    command = launch.call_args.args[0]
    assert f"{name}={value}" in command
    assert command[command.index("--exec") + 1] == "/usr/bin/env"


def _configure_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_WSL_DISTRO", "Ubuntu")
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_WSL_PROJECT", "/home/carla/toolkit")
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_WSL_UV", "/home/carla/uv")


def test_windows_launcher_does_not_invent_endpoint_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfigured launcher preserves the server's existing default selection."""
    _configure_launcher(monkeypatch)
    command = windows_launcher.WslLaunchConfig.from_environment().command(["carla-agentic-toolkit"])
    assert not any(item.startswith((f"{HOST_ENV}=", f"{PORT_ENV}=")) for item in command)
