"""Behavior specs for the Rust-backed sandbox launcher."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from carla_agentic_toolkit import sandbox
from carla_agentic_toolkit.script_runner import MAX_SCRIPT_STDOUT_BYTES, run_script_file
from carla_agentic_toolkit.tool_inputs import (
    parse_traffic_population_request,
    parse_transform,
)
from tests.sandbox_helpers import sandbox_runner_path


@pytest.mark.parametrize(
    "execution_input",
    [
        {"host": " "},
        {"host": None},
        {"port": True},
        {"port": 1.5},
        {"port": 0},
        {"port": 65534},
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": float("nan")},
        {"timeout_seconds": 3601},
        {"traffic_manager_ports": (False,)},
        {"traffic_manager_ports": (0,)},
        {"traffic_manager_ports": (1.5,)},
        {"traffic_manager_ports": (65536,)},
    ],
)
def test_execute_script_rejects_invalid_input_before_side_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    execution_input: dict[str, Any],
) -> None:
    """Every public execution bound should fail before output setup or launch."""
    output_dir = tmp_path / "outputs"
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(output_dir))

    outcome = sandbox.execute_script("result = 1", **execution_input)

    assert {"error_type": outcome.error_type, "output_created": output_dir.exists()} == {
        "error_type": "invalid_request",
        "output_created": False,
    }


def test_execute_script_reports_missing_rust_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scripts should fail closed when the Rust sandbox binary is unavailable."""
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", "/missing/carla-agentic-toolkit-sandbox")

    outcome = sandbox.execute_script("result = {'ok': True}")

    assert outcome.ok is False
    assert outcome.error_type == "sandbox_runner_missing"


@pytest.mark.parametrize(
    ("exception", "error_type"),
    [
        (subprocess.TimeoutExpired(["runner"], 1), "sandbox_watchdog_error"),
        (PermissionError("denied"), "sandbox_launcher_error"),
    ],
)
def test_execute_script_normalizes_launcher_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exception: Exception,
    error_type: str,
) -> None:
    """Expected outer watchdog and spawn failures should not escape the MCP server."""
    runner = tmp_path / "carla-agentic-toolkit-sandbox"
    runner.write_text("runner", encoding="utf-8")
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "outputs"))

    def fail(*_args: object, **_kwargs: object) -> None:
        raise exception

    monkeypatch.setattr(sandbox.subprocess, "run", fail)

    outcome = sandbox.execute_script("result = 1", timeout_seconds=1)

    assert outcome.error_type == error_type
    assert outcome.sandbox == {"runner": str(runner)}


def test_execute_script_normalizes_output_directory_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Output setup errors should become structured outcomes."""
    runner = tmp_path / "carla-agentic-toolkit-sandbox"
    runner.write_text("runner", encoding="utf-8")
    output_file = tmp_path / "not-a-directory"
    output_file.write_text("file", encoding="utf-8")
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(output_file))

    outcome = sandbox.execute_script("result = 1")

    assert outcome.error_type == "sandbox_setup_error"


def test_execute_script_normalizes_temporary_directory_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Temporary-script setup failures should become structured outcomes."""
    runner = tmp_path / "carla-agentic-toolkit-sandbox"
    runner.write_text("runner", encoding="utf-8")
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "outputs"))

    def fail(*_args: object, **_kwargs: object) -> None:
        message = "temporary directory unavailable"
        raise OSError(message)

    monkeypatch.setattr(sandbox.tempfile, "mkdtemp", fail)

    outcome = sandbox.execute_script("result = 1")

    assert outcome.error_type == "sandbox_setup_error"


def test_execute_script_supports_shallow_system_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """System Python paths should not crash or grant the filesystem root."""
    runner = tmp_path / "carla-agentic-toolkit-sandbox"
    runner.write_text("#!/bin/sh\n", encoding="utf-8")
    runner.chmod(0o755)
    commands: list[list[str]] = []

    def run_command(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='{"ok": true, "result": null, "stdout": ""}',
            stderr="",
        )

    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "outputs"))
    monkeypatch.setattr(sandbox.sys, "executable", "/usr/bin/python3.12")
    monkeypatch.setattr(sandbox.subprocess, "run", run_command)

    outcome = sandbox.execute_script("result = None")

    assert outcome.ok is True
    assert "/" not in commands[0]


def test_execute_script_passes_carla_ports_to_rust_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The wrapper should allow only CARLA-related TCP ports by default."""
    runner = tmp_path / "carla-agentic-toolkit-sandbox"
    runner.write_text("#!/bin/sh\n", encoding="utf-8")
    runner.chmod(0o755)
    output_dir = tmp_path / "outputs"
    commands: list[list[str]] = []

    def run_command(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='{"ok": true, "result": 1, "stdout": ""}',
            stderr="",
        )

    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_RECORDER_DIR", "E:/CARLA_0.9.16/recordings")
    monkeypatch.setattr(sandbox.subprocess, "run", run_command)

    outcome = sandbox.execute_script(
        "result = 1",
        port=2000,
        traffic_manager_ports=(8050,),
    )

    resolved_output_dir = str(output_dir.resolve())
    resolver_paths = {
        str(path)
        for path in (
            Path("/etc/nsswitch.conf"),
            Path("/etc/host.conf"),
            Path("/etc/hosts"),
            Path("/etc/resolv.conf"),
            Path("/etc/gai.conf"),
        )
        if path.exists()
    }
    assert {
        "ok": outcome.ok,
        "result": outcome.result,
        "output_dir_created": output_dir.is_dir(),
        "output_dir_arguments": commands[0].count(resolved_output_dir),
        "has_output_dir_option": "--output-dir" in commands[0],
        "has_tcp_rule": "--tcp-connect" in commands[0],
        "has_tcp_bind": "--tcp-bind" in commands[0],
        "grants_dev_or_sys": bool({"/dev", "/sys"} & set(commands[0])),
        "grants_resolver_files": resolver_paths <= set(commands[0]),
        "grants_etc": "/etc" in commands[0],
        "recorder_dir": commands[0][commands[0].index("--recorder-dir") + 1],
        "allowed_ports": {"2000", "2001", "2002", "8050"} <= set(commands[0]),
    } == {
        "ok": True,
        "result": 1,
        "output_dir_created": True,
        "output_dir_arguments": 2,
        "has_output_dir_option": True,
        "has_tcp_rule": True,
        "has_tcp_bind": False,
        "grants_dev_or_sys": False,
        "grants_resolver_files": True,
        "grants_etc": False,
        "recorder_dir": "E:/CARLA_0.9.16/recordings",
        "allowed_ports": True,
    }


def test_execute_script_preserves_snapshots_from_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The MCP layer should surface snapshots created during script execution."""
    runner = tmp_path / "carla-agentic-toolkit-sandbox"
    runner.write_text("#!/bin/sh\n", encoding="utf-8")
    runner.chmod(0o755)

    def run_command(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                '{"ok": true, "result": null, "stdout": "", '
                '"snapshots": {"carla-snapshot://world/current": {"frame": 1}}}'
            ),
            stderr="",
        )

    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setattr(sandbox.subprocess, "run", run_command)

    outcome = sandbox.execute_script("result = None")

    payload = outcome.to_dict()
    assert outcome.snapshots == {"carla-snapshot://world/current": {"frame": 1}}
    assert payload["snapshots"] == {"carla-snapshot://world/current": {"frame": 1}}
    assert "resources" not in payload


@pytest.mark.skipif(sys.platform != "linux", reason="requires the Linux sandbox runner")
def test_large_script_output_is_bounded_without_a_false_timeout() -> None:
    """A full pipe should become output_too_large rather than script_timeout."""
    outcome = sandbox.execute_script('result = "x" * 1_500_000', timeout_seconds=5)

    assert outcome.ok is False
    assert outcome.error_type == "output_too_large"
    assert outcome.sandbox is not None
    assert outcome.sandbox["timed_out"] is False


@pytest.mark.skipif(sys.platform != "linux", reason="requires the Linux sandbox runner")
def test_genuine_script_timeout_still_kills_the_child() -> None:
    """The watchdog margin must not turn script budget exhaustion into success."""
    outcome = sandbox.execute_script("while True: pass", timeout_seconds=0.1)

    assert outcome.ok is False
    assert outcome.error_type == "script_timeout"
    assert outcome.sandbox is not None
    assert outcome.sandbox["timed_out"] is True


@pytest.mark.skipif(sys.platform != "linux", reason="requires the Linux sandbox runner")
def test_real_landlock_blocks_read_outside_allowlist(tmp_path: Path) -> None:
    """The real runner should permit an allowed read and deny an unlisted sibling."""
    project_root = Path(__file__).resolve().parents[1]
    runner = sandbox_runner_path()
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    blocked_file = tmp_path / "blocked.txt"
    work_dir.mkdir()
    output_dir.mkdir()
    blocked_file.write_text("blocked", encoding="utf-8")

    def run_probe(path: Path) -> dict[str, object]:
        probe = work_dir / "probe.py"
        probe.write_text(
            "#!/usr/bin/python3\n"
            f"open({str(path)!r}, encoding='utf-8').read()\n"
            'print(\'{"ok":true,"result":null,"stdout":"","snapshots":{}}\')\n',
            encoding="utf-8",
        )
        probe.chmod(0o755)
        command = [
            str(runner),
            "--python",
            str(probe),
            "--module",
            "ignored",
            "--script",
            str(probe),
            "--work-dir",
            str(work_dir),
            "--output-dir",
            str(output_dir),
            "--read-only",
            str(project_root),
            "--read-only",
            "/usr",
            "--read-only",
            "/lib",
            "--read-write",
            str(work_dir),
            "--read-write",
            str(output_dir),
        ]
        completed = subprocess.run(  # noqa: S603 - fixed local runner and probe
            command,
            check=False,
            text=True,
            capture_output=True,
        )
        return cast("dict[str, object]", json.loads(completed.stdout))

    allowed = run_probe(project_root / "README.md")
    blocked = run_probe(blocked_file)

    assert {
        "allowed": allowed.get("ok"),
        "blocked_type": blocked.get("error_type"),
        "permission_denied": "PermissionError" in str(blocked.get("error")),
    } == {
        "allowed": True,
        "blocked_type": "python_runner_failed",
        "permission_denied": True,
    }


@pytest.mark.skipif(sys.platform != "linux", reason="requires the Linux sandbox runner")
def test_real_landlock_allows_requested_port_and_blocks_unlisted_port(tmp_path: Path) -> None:
    """Network rules should grant connect only to explicitly listed ports."""
    runner = sandbox_runner_path()
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    work_dir.mkdir()
    output_dir.mkdir()

    def run_probe(port: int, allowed_port: int) -> dict[str, object]:
        probe = work_dir / "network_probe.py"
        probe.write_text(
            "#!/usr/bin/python3\n"
            "import socket\n"
            f"socket.create_connection(('127.0.0.1', {port}), 1).close()\n"
            'print(\'{"ok":true,"result":null,"stdout":"","snapshots":{}}\')\n',
            encoding="utf-8",
        )
        probe.chmod(0o755)
        command = [
            str(runner),
            "--python",
            str(probe),
            "--module",
            "ignored",
            "--script",
            str(probe),
            "--work-dir",
            str(work_dir),
            "--output-dir",
            str(output_dir),
            "--read-only",
            "/usr",
            "--read-only",
            "/lib",
            "--read-write",
            str(work_dir),
            "--read-write",
            str(output_dir),
            "--tcp-connect",
            str(allowed_port),
        ]
        completed = subprocess.run(  # noqa: S603 - fixed local runner and probe
            command,
            check=False,
            text=True,
            capture_output=True,
        )
        return cast("dict[str, object]", json.loads(completed.stdout))

    with socket.socket() as allowed_listener, socket.socket() as blocked_listener:
        allowed_listener.bind(("127.0.0.1", 0))
        blocked_listener.bind(("127.0.0.1", 0))
        allowed_listener.listen()
        blocked_listener.listen()
        allowed_port = int(allowed_listener.getsockname()[1])
        blocked_port = int(blocked_listener.getsockname()[1])
        allowed = run_probe(allowed_port, allowed_port)
        blocked = run_probe(blocked_port, allowed_port)

    assert {
        "allowed": allowed.get("ok"),
        "blocked_type": blocked.get("error_type"),
        "permission_denied": "PermissionError" in str(blocked.get("error")),
    } == {
        "allowed": True,
        "blocked_type": "python_runner_failed",
        "permission_denied": True,
    }


def test_execute_script_does_not_grant_proc_read_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The wrapper should not expose broad /proc reads to scenario scripts."""
    runner = tmp_path / "carla-agentic-toolkit-sandbox"
    runner.write_text("#!/bin/sh\n", encoding="utf-8")
    runner.chmod(0o755)
    commands: list[list[str]] = []

    def run_command(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout='{"ok": true, "result": null, "stdout": ""}',
            stderr="",
        )

    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setattr(sandbox.subprocess, "run", run_command)

    outcome = sandbox.execute_script("result = None")

    assert outcome.ok is True
    assert "/proc" not in commands[0]


def test_script_runner_rejects_import_builtin(tmp_path: Path) -> None:
    """Scripts should not recover imports through the builtins namespace."""
    script = tmp_path / "script.py"
    script.write_text('result = __import__("os").getpid()\n', encoding="utf-8")

    outcome = run_script_file(
        script_path=script,
        host="127.0.0.1",
        port=2000,
        timeout_seconds=1.0,
    )

    assert outcome["ok"] is False
    assert outcome["error_type"] == "script_rejected"
    assert "__import__" in str(outcome["error"])


def test_script_runner_rejects_private_api_access(tmp_path: Path) -> None:
    """Scripts should not bypass the curated API through private attributes."""
    script = tmp_path / "script.py"
    script.write_text("result = api._adapter.host\n", encoding="utf-8")

    outcome = run_script_file(
        script_path=script,
        host="127.0.0.1",
        port=2000,
        timeout_seconds=1.0,
    )

    assert outcome["ok"] is False
    assert outcome["error_type"] == "script_rejected"
    assert "private" in str(outcome["error"])


def test_script_runner_rejects_format_attribute_traversal(tmp_path: Path) -> None:
    """Format fields should not bypass private-attribute validation."""
    script = tmp_path / "script.py"
    script.write_text('result = "{0._adapter}".format(api)\n', encoding="utf-8")

    outcome = run_script_file(
        script_path=script,
        host="127.0.0.1",
        port=2000,
        timeout_seconds=1.0,
    )

    assert outcome["ok"] is False
    assert outcome["error_type"] == "script_rejected"
    assert "format" in str(outcome["error"])


def test_script_runner_rejects_open_builtin(tmp_path: Path) -> None:
    """Scripts should not receive host file access through Python open."""
    script = tmp_path / "script.py"
    script.write_text("result = open\n", encoding="utf-8")

    outcome = run_script_file(
        script_path=script,
        host="127.0.0.1",
        port=2000,
        timeout_seconds=1.0,
    )

    assert outcome["ok"] is False
    assert outcome["error_type"] == "script_rejected"
    assert "open" in str(outcome["error"])


def test_script_runner_stdout_is_bounded(tmp_path: Path) -> None:
    """Script stdout should be silently truncated instead of OOM-ing."""
    script = tmp_path / "script.py"
    size = MAX_SCRIPT_STDOUT_BYTES + 1024
    script.write_text(
        f"print('x' * {size})",
        encoding="utf-8",
    )
    outcome = run_script_file(script_path=script, host="127.0.0.1", port=2000, timeout_seconds=1.0)
    assert outcome["ok"] is False
    assert outcome["error_type"] == "output_too_large"
    stdout = outcome["stdout"]
    assert isinstance(stdout, str)
    assert len(stdout.encode("utf-8")) <= MAX_SCRIPT_STDOUT_BYTES


def test_bool_rejected_as_int_in_tool_inputs() -> None:
    """True/False must not be accepted where integers are expected."""
    payload: dict[str, object] = {
        "vehicle_count": True,
        "traffic_manager_port": 8000,
        "seed": 0,
    }
    with pytest.raises(TypeError, match="integer"):
        parse_traffic_population_request(payload)


def test_bool_rejected_as_float_in_tool_inputs() -> None:
    """True/False must not be accepted where floats are expected."""
    payload: dict[str, object] = {
        "location": {"x": False, "y": 0, "z": 0},
        "rotation": {"pitch": 0, "yaw": 0, "roll": 0},
    }
    with pytest.raises(TypeError, match="finite number"):
        parse_transform(payload)
