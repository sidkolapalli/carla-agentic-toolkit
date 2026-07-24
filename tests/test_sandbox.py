"""Behavior specs for the Rust-backed sandbox launcher."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from carla_mcp import sandbox
from carla_mcp.script_runner import run_script_file

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_execute_script_reports_missing_rust_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scripts should fail closed when the Rust sandbox binary is unavailable."""
    monkeypatch.setenv("CARLA_MCP_SANDBOX", "/missing/carla-mcp-sandbox")

    outcome = sandbox.execute_script("result = {'ok': True}")

    assert outcome.ok is False
    assert outcome.error_type == "sandbox_runner_missing"


def test_execute_script_supports_shallow_system_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """System Python paths should not crash or grant the filesystem root."""
    runner = tmp_path / "carla-mcp-sandbox"
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

    monkeypatch.setenv("CARLA_MCP_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_MCP_OUTPUT_DIR", str(tmp_path / "outputs"))
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
    runner = tmp_path / "carla-mcp-sandbox"
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

    monkeypatch.setenv("CARLA_MCP_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_MCP_OUTPUT_DIR", str(output_dir))
    monkeypatch.setattr(sandbox.subprocess, "run", run_command)

    outcome = sandbox.execute_script(
        "result = 1",
        port=2000,
        traffic_manager_ports=(8050,),
    )

    resolved_output_dir = str(output_dir.resolve())
    assert {
        "ok": outcome.ok,
        "result": outcome.result,
        "output_dir_created": output_dir.is_dir(),
        "output_dir_arguments": commands[0].count(resolved_output_dir),
        "has_output_dir_option": "--output-dir" in commands[0],
        "has_tcp_rule": "--tcp-connect" in commands[0],
        "allowed_ports": {"2000", "2001", "2002", "8050"} <= set(commands[0]),
    } == {
        "ok": True,
        "result": 1,
        "output_dir_created": True,
        "output_dir_arguments": 2,
        "has_output_dir_option": True,
        "has_tcp_rule": True,
        "allowed_ports": True,
    }


def test_execute_script_preserves_resources_from_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The MCP layer should surface resources created during script execution."""
    runner = tmp_path / "carla-mcp-sandbox"
    runner.write_text("#!/bin/sh\n", encoding="utf-8")
    runner.chmod(0o755)

    def run_command(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                '{"ok": true, "result": null, "stdout": "", '
                '"resources": {"carla://world/current": {"frame": 1}}}'
            ),
            stderr="",
        )

    monkeypatch.setenv("CARLA_MCP_SANDBOX", str(runner))
    monkeypatch.setattr(sandbox.subprocess, "run", run_command)

    outcome = sandbox.execute_script("result = None")

    assert outcome.resources == {"carla://world/current": {"frame": 1}}
    assert outcome.to_dict()["resources"] == {"carla://world/current": {"frame": 1}}


def test_execute_script_does_not_grant_proc_read_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The wrapper should not expose broad /proc reads to scenario scripts."""
    runner = tmp_path / "carla-mcp-sandbox"
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

    monkeypatch.setenv("CARLA_MCP_SANDBOX", str(runner))
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
