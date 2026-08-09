"""Behavior specs for the Windows-to-WSL2 launcher."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _fake_wsl_command(tmp_path: Path, script: str) -> Path:
    """Write an executable fake WSL command for the current host."""
    fake = tmp_path / "recording-wsl"
    fake.write_text(script, encoding="utf-8")
    if os.name == "nt":
        wrapper = tmp_path / "recording-wsl.cmd"
        wrapper.write_text(
            f'@echo off\n"{sys.executable}" "%~dp0recording-wsl" %*\n',
            encoding="utf-8",
        )
        return wrapper
    fake.chmod(0o755)
    return fake


def test_windows_launcher_proxies_stdio_and_child_exit_status(tmp_path: Path) -> None:
    """The public launcher should transparently proxy the WSL-hosted MCP server."""
    arguments_log = tmp_path / "arguments.json"
    wsl_command = _fake_wsl_command(
        tmp_path,
        f"""#!{sys.executable}
import json
import os
import sys

with open(os.environ["WSL_ARGUMENTS_LOG"], "w", encoding="utf-8") as stream:
    json.dump(sys.argv[1:], stream)
sys.stdout.write(sys.stdin.read())
sys.stderr.write("WSL diagnostic\\n")
raise SystemExit(23)
""",
    )
    request = '{"jsonrpc":"2.0","method":"initialize"}\n'
    env = {
        **os.environ,
        "CARLA_AGENTIC_TOOLKIT_WSL_COMMAND": str(wsl_command),
        "CARLA_AGENTIC_TOOLKIT_WSL_DISTRO": "Ubuntu-24.04",
        "CARLA_AGENTIC_TOOLKIT_WSL_PROJECT": "/home/carla/carla-agentic-toolkit",
        "CARLA_AGENTIC_TOOLKIT_WSL_UV": "/home/carla/.local/bin/uv",
        "CARLA_AGENTIC_TOOLKIT_RECORDER_DIR": "E:/CARLA_0.9.16",
        "WSL_ARGUMENTS_LOG": str(arguments_log),
    }
    launcher_name = (
        "carla-agentic-toolkit-windows.exe" if os.name == "nt" else "carla-agentic-toolkit-windows"
    )
    launcher = Path(sys.executable).with_name(launcher_name)

    completed = subprocess.run(
        [launcher],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        input=request,
        text=True,
        capture_output=True,
        check=False,
    )

    assert {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "arguments": json.loads(arguments_log.read_text(encoding="utf-8")),
    } == {
        "exit_code": 23,
        "stdout": request,
        "stderr": "WSL diagnostic\n",
        "arguments": [
            "--distribution",
            "Ubuntu-24.04",
            "--cd",
            "/home/carla/carla-agentic-toolkit",
            "--exec",
            "/usr/bin/env",
            "CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR=/home/carla/carla-agentic-toolkit/carla-agentic-toolkit-output",
            "CARLA_AGENTIC_TOOLKIT_RECORDER_DIR=E:/CARLA_0.9.16",
            "/home/carla/.local/bin/uv",
            "--directory",
            "/home/carla/carla-agentic-toolkit",
            "run",
            "carla-agentic-toolkit",
        ],
    }


def test_windows_launcher_check_runs_wsl2_preflight(tmp_path: Path) -> None:
    """The public check mode should require WSL2 and exercise the real preflight."""
    arguments_log = tmp_path / "arguments.json"
    wsl_command = _fake_wsl_command(
        tmp_path,
        f"""#!{sys.executable}
import json
import os
import sys

with open(os.environ["WSL_ARGUMENTS_LOG"], "w", encoding="utf-8") as stream:
    json.dump(sys.argv[1:], stream)
""",
    )
    env = {
        **os.environ,
        "CARLA_AGENTIC_TOOLKIT_WSL_COMMAND": str(wsl_command),
        "CARLA_AGENTIC_TOOLKIT_WSL_DISTRO": "Ubuntu-24.04",
        "CARLA_AGENTIC_TOOLKIT_WSL_PROJECT": "/home/carla/carla-agentic-toolkit",
        "CARLA_AGENTIC_TOOLKIT_WSL_UV": "/home/carla/.local/bin/uv",
        "CARLA_AGENTIC_TOOLKIT_WSL_OUTPUT_DIR": "/home/carla/output",
        "WSL_ARGUMENTS_LOG": str(arguments_log),
    }
    launcher_name = (
        "carla-agentic-toolkit-windows.exe" if os.name == "nt" else "carla-agentic-toolkit-windows"
    )
    launcher = Path(sys.executable).with_name(launcher_name)

    completed = subprocess.run(
        [launcher, "--check"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "arguments": json.loads(arguments_log.read_text(encoding="utf-8")),
    } == {
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "arguments": [
            "--distribution",
            "Ubuntu-24.04",
            "--cd",
            "/home/carla/carla-agentic-toolkit",
            "--exec",
            "/usr/bin/env",
            "CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR=/home/carla/output",
            "/home/carla/.local/bin/uv",
            "--directory",
            "/home/carla/carla-agentic-toolkit",
            "run",
            "carla-agentic-toolkit-preflight",
            "--expect-wsl2",
        ],
    }


def test_windows_launcher_reports_all_missing_configuration() -> None:
    """Missing Windows-to-WSL configuration should fail before process launch."""
    env = dict(os.environ)
    required_names = (
        "CARLA_AGENTIC_TOOLKIT_WSL_DISTRO",
        "CARLA_AGENTIC_TOOLKIT_WSL_PROJECT",
        "CARLA_AGENTIC_TOOLKIT_WSL_UV",
    )
    for name in required_names:
        env.pop(name, None)
    launcher_name = (
        "carla-agentic-toolkit-windows.exe" if os.name == "nt" else "carla-agentic-toolkit-windows"
    )
    launcher = Path(sys.executable).with_name(launcher_name)

    completed = subprocess.run(
        [launcher],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    } == {
        "exit_code": 2,
        "stdout": "",
        "stderr": (
            "carla-agentic-toolkit-windows: missing required configuration: "
            "CARLA_AGENTIC_TOOLKIT_WSL_DISTRO, "
            "CARLA_AGENTIC_TOOLKIT_WSL_PROJECT, "
            "CARLA_AGENTIC_TOOLKIT_WSL_UV\n"
        ),
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CARLA_AGENTIC_TOOLKIT_WSL_PROJECT", "relative/project"),
        ("CARLA_AGENTIC_TOOLKIT_WSL_UV", "uv"),
        ("CARLA_AGENTIC_TOOLKIT_WSL_OUTPUT_DIR", "relative/output"),
    ],
)
def test_windows_launcher_rejects_non_absolute_linux_paths(name: str, value: str) -> None:
    """Paths crossing into WSL should be explicit absolute Linux paths."""
    env = {
        **os.environ,
        "CARLA_AGENTIC_TOOLKIT_WSL_DISTRO": "Ubuntu-24.04",
        "CARLA_AGENTIC_TOOLKIT_WSL_PROJECT": "/home/carla/carla-agentic-toolkit",
        "CARLA_AGENTIC_TOOLKIT_WSL_UV": "/home/carla/.local/bin/uv",
        "CARLA_AGENTIC_TOOLKIT_WSL_OUTPUT_DIR": "/home/carla/output",
        name: value,
    }
    launcher_name = (
        "carla-agentic-toolkit-windows.exe" if os.name == "nt" else "carla-agentic-toolkit-windows"
    )
    launcher = Path(sys.executable).with_name(launcher_name)

    completed = subprocess.run(
        [launcher],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    } == {
        "exit_code": 2,
        "stdout": "",
        "stderr": f"carla-agentic-toolkit-windows: {name} must be an absolute Linux path.\n",
    }


def test_windows_launcher_reports_missing_wsl_command() -> None:
    """An unavailable WSL executable should fail closed without a traceback."""
    missing_wsl = "/missing/wsl.exe"
    env = {
        **os.environ,
        "CARLA_AGENTIC_TOOLKIT_WSL_COMMAND": missing_wsl,
        "CARLA_AGENTIC_TOOLKIT_WSL_DISTRO": "Ubuntu-24.04",
        "CARLA_AGENTIC_TOOLKIT_WSL_PROJECT": "/home/carla/carla-agentic-toolkit",
        "CARLA_AGENTIC_TOOLKIT_WSL_UV": "/home/carla/.local/bin/uv",
    }
    launcher_name = (
        "carla-agentic-toolkit-windows.exe" if os.name == "nt" else "carla-agentic-toolkit-windows"
    )
    launcher = Path(sys.executable).with_name(launcher_name)

    completed = subprocess.run(
        [launcher],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert {
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    } == {
        "exit_code": 1,
        "stdout": "",
        "stderr": f"carla-agentic-toolkit-windows: WSL command not found: {missing_wsl}\n",
    }
