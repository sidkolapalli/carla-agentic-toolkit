"""Explicit runtime port layouts stay validated and narrowly reflected in Landlock."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock

import pytest
from mcp import Client

from carla_agentic_toolkit import sandbox, sandbox_session_process, server
from carla_agentic_toolkit.sandbox import RunnerCommandRequest, ScriptOutcome
from carla_agentic_toolkit.session_protocol import SessionConfig, read_message
from tests.sandbox_helpers import sandbox_runner_path

if TYPE_CHECKING:
    from mcp.server import MCPServer
    from mcp.types import CallToolResult

RPC_PORT = 23100
STREAMING_PORT = 23410
SECONDARY_PORT = 23520
TM_PORT = 8500
PORT_FIELDS = ("streaming_port", "secondary_port")
INVALID_PORTS = (False, True, 0, -1, 65536, 1.5, "23410", [], {}, float("nan"))
DEFAULT_PORTS = (8000, RPC_PORT, RPC_PORT + 1, RPC_PORT + 2)


def _command_ports(command: list[str]) -> tuple[int, ...]:
    return tuple(
        int(command[index + 1]) for index, option in enumerate(command) if option == "--tcp-connect"
    )


def _capture_launcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    runner = tmp_path / "runner"
    runner.write_text("fake runner", encoding="utf-8")
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))

    def execute(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '{"ok":true,"result":1,"stdout":""}', "")

    monkeypatch.setattr(sandbox.subprocess, "run", execute)
    return commands


@pytest.mark.parametrize(
    ("layout", "expected"),
    [
        ({}, DEFAULT_PORTS),
        ({"streaming_port": None, "secondary_port": None}, DEFAULT_PORTS),
        ({"streaming_port": STREAMING_PORT}, (8000, RPC_PORT, RPC_PORT + 2, STREAMING_PORT)),
        ({"secondary_port": SECONDARY_PORT}, (8000, RPC_PORT, RPC_PORT + 1, SECONDARY_PORT)),
        (
            {"streaming_port": STREAMING_PORT, "secondary_port": SECONDARY_PORT},
            (8000, RPC_PORT, STREAMING_PORT, SECONDARY_PORT),
        ),
        ({"streaming_port": RPC_PORT, "secondary_port": 8000}, (8000, RPC_PORT)),
        ({"streaming_port": 1, "secondary_port": 65535}, (1, 8000, RPC_PORT, 65535)),
    ],
)
def test_finite_launcher_forwards_only_the_selected_port_layout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    layout: dict[str, object],
    expected: tuple[int, ...],
) -> None:
    """Overrides replace adjacent defaults without granting bind or unrelated ports."""
    commands = _capture_launcher(monkeypatch, tmp_path)
    outcome = sandbox.execute_script("result = 1", port=RPC_PORT, **cast("Any", layout))
    assert outcome.ok is True
    assert _command_ports(commands[0]) == expected
    assert "--tcp-bind" not in commands[0]


def test_explicit_tm_ports_remain_in_the_custom_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only the streaming/secondary defaults change, explicit TM permissions are preserved."""
    commands = _capture_launcher(monkeypatch, tmp_path)
    outcome = sandbox.execute_script(
        "result = 1",
        port=RPC_PORT,
        traffic_manager_ports=(TM_PORT, TM_PORT),
        **cast("Any", {"streaming_port": STREAMING_PORT, "secondary_port": SECONDARY_PORT}),
    )
    assert outcome.ok is True
    assert _command_ports(commands[0]) == (8000, TM_PORT, RPC_PORT, STREAMING_PORT, SECONDARY_PORT)


@pytest.mark.parametrize("field", PORT_FIELDS)
@pytest.mark.parametrize("value", INVALID_PORTS)
def test_finite_port_validation_precedes_runner_lease_and_file_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, field: str, value: object
) -> None:
    """Invalid scalar permissions are rejected before acquiring or launching anything."""
    setup = Mock(side_effect=AssertionError("setup must not be reached"))
    monkeypatch.setattr(sandbox, "_sandbox_runner", setup)
    monkeypatch.setattr(sandbox, "SimulatorLease", setup)
    output = tmp_path / "output"
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(output))
    outcome = sandbox.execute_script("result = 1", **cast("Any", {field: value}))
    assert outcome.error_type == "invalid_request"
    assert field in (outcome.error or "")
    setup.assert_not_called()
    assert not output.exists()


@pytest.mark.parametrize("field", PORT_FIELDS)
@pytest.mark.parametrize("value", [None, 1, 65535, STREAMING_PORT])
def test_session_accepts_optional_strict_runtime_port_values(field: str, value: int | None) -> None:
    """Persistent configuration supports null defaults and the full TCP range."""
    config = SessionConfig.parse({field: value})
    assert getattr(config, field) == value


@pytest.mark.parametrize("field", PORT_FIELDS)
@pytest.mark.parametrize("value", INVALID_PORTS)
def test_session_rejects_invalid_runtime_port_values(field: str, value: object) -> None:
    """Malformed optional ports produce actionable validation without an owner lease."""
    with pytest.raises(ValueError, match=field):
        SessionConfig.parse({field: value})


@pytest.mark.parametrize("field", PORT_FIELDS)
def test_direct_session_validation_precedes_lease(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct config construction cannot bypass strict validation at process creation."""
    lease = Mock(side_effect=AssertionError("lease must not be acquired"))
    monkeypatch.setattr(sandbox_session_process, "SimulatorLease", lease)
    config = SessionConfig(**cast("Any", {field: True}))
    with pytest.raises(ValueError, match=field):
        sandbox_session_process.SessionProcess("invalid-layout", config)
    lease.assert_not_called()


@pytest.mark.skipif(sys.platform != "linux", reason="Simulator leases require Linux")
def test_session_preserves_layout_in_requests_command_and_worker_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One immutable layout determines parent permissions and the published worker config."""
    commands: list[list[str]] = []
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(sandbox_session_process, "_sandbox_runner", lambda: Path("runner"))
    root = tmp_path / "session"
    root.mkdir()
    monkeypatch.setattr(sandbox_session_process.tempfile, "mkdtemp", lambda **_kwargs: str(root))

    def launch(command: list[str], **_kwargs: object) -> subprocess.Popen[str]:
        commands.append(command)
        return cast("subprocess.Popen[str]", SimpleNamespace(args=command, returncode=0))

    monkeypatch.setattr(sandbox_session_process.subprocess, "Popen", launch)
    config = SessionConfig.parse(
        {
            "port": RPC_PORT,
            "streaming_port": STREAMING_PORT,
            "secondary_port": SECONDARY_PORT,
            "traffic_manager_ports": [TM_PORT],
        }
    )
    owner = sandbox_session_process.SessionProcess("custom-layout", config)
    try:
        owner.start()
        assert _command_ports(commands[0]) == (
            8000,
            TM_PORT,
            RPC_PORT,
            STREAMING_PORT,
            SECONDARY_PORT,
        )
        _assert_session_metadata(owner)
    finally:
        owner.lease.mark_clean()
        owner.lease.__exit__(None, None, None)


def _assert_session_metadata(owner: sandbox_session_process.SessionProcess) -> None:
    assert owner.request.streaming_port == STREAMING_PORT
    assert owner.request.secondary_port == SECONDARY_PORT
    metadata = read_message(owner.work / "session.json")
    assert metadata["streaming_port"] == STREAMING_PORT
    assert metadata["secondary_port"] == SECONDARY_PORT


async def _call_one_shot(mcp: MCPServer, layout: dict[str, object]) -> CallToolResult:
    async with Client(mcp) as client:
        return await client.call_tool("execute_carla_script", {"code": "result = 1", **layout})


def test_served_one_shot_tool_passes_explicit_port_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Actual negotiated MCP calls retain the new public options, not just a Python wrapper."""
    received: dict[str, object] = {}

    def execute(_code: str, **kwargs: object) -> ScriptOutcome:
        received.update(kwargs)
        return ScriptOutcome(ok=True, result=1, stdout="")

    monkeypatch.setattr(server, "execute_script", execute)
    result = asyncio.run(
        _call_one_shot(
            server.build_server(),
            {"streaming_port": STREAMING_PORT, "secondary_port": SECONDARY_PORT},
        )
    )
    assert result.is_error is False
    assert received["streaming_port"] == STREAMING_PORT
    assert received["secondary_port"] == SECONDARY_PORT


@pytest.mark.parametrize("field", PORT_FIELDS)
@pytest.mark.parametrize("value", [True, "23410", 1.5])
def test_served_one_shot_tool_does_not_coerce_port_permissions(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    """A public permission cannot become valid through transport type coercion."""
    execute = Mock(return_value=ScriptOutcome(ok=True, result=1, stdout=""))
    monkeypatch.setattr(server, "execute_script", execute)
    result = asyncio.run(_call_one_shot(server.build_server(), {field: value}))
    assert result.is_error is True
    execute.assert_not_called()


def _network_probe(tmp_path: Path, target: int, allowed: int) -> dict[str, object]:
    work = tmp_path / str(target)
    work.mkdir()
    probe = work / "probe.py"
    probe.write_text(
        "#!/usr/bin/python3\nimport socket\n"
        f"socket.create_connection(('127.0.0.1', {target}), 1).close()\n"
        'print(\'{"ok":true,"result":null,"stdout":""}\')\n',
        encoding="utf-8",
    )
    probe.chmod(0o755)
    request = RunnerCommandRequest(
        runner=sandbox_runner_path(),
        script_path=probe,
        work_dir=work,
        output_dir=work,
        host="127.0.0.1",
        port=RPC_PORT,
        timeout_seconds=5.0,
        traffic_manager_ports=(),
        recorder_dir=None,
        **cast("Any", {"streaming_port": allowed, "secondary_port": SECONDARY_PORT}),
    )
    command = sandbox._runner_command(request)  # noqa: SLF001 -- Exercise actual launch permissions.
    command[command.index("--python") + 1] = str(probe)
    completed = subprocess.run(command, check=False, capture_output=True, text=True)  # noqa: S603
    return cast("dict[str, object]", json.loads(completed.stdout))


@pytest.mark.skipif(sys.platform != "linux", reason="Landlock requires Linux")
def test_real_landlock_custom_streaming_port_does_not_allow_unlisted_connection(
    tmp_path: Path,
) -> None:
    """Custom client connects work while the actual kernel still blocks unrelated endpoints."""
    with socket.socket() as allowed, socket.socket() as blocked:
        for listener in (allowed, blocked):
            listener.bind(("127.0.0.1", 0))
            listener.listen()
        allowed_port = cast("int", allowed.getsockname()[1])
        blocked_port = cast("int", blocked.getsockname()[1])
        success = _network_probe(tmp_path, allowed_port, allowed_port)
        denied = _network_probe(tmp_path, blocked_port, allowed_port)
    assert success["ok"] is True
    assert denied["ok"] is False
    assert "PermissionError" in str(denied.get("error"))
