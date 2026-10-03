"""Behavior specs for host sandbox readiness checks."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

from tests.sandbox_helpers import sandbox_runner_path

IS_WSL2 = "microsoft" in platform.release().casefold() and "wsl2" in platform.release().casefold()


@pytest.mark.skipif(sys.platform != "linux", reason="requires the Linux Landlock sandbox runner")
def test_preflight_proves_landlock_and_persistent_output(tmp_path: Path) -> None:
    """The public preflight should exercise the real sandbox without CARLA."""
    project_root = Path(__file__).resolve().parents[1]
    runner = sandbox_runner_path()
    output_dir = tmp_path / "outputs"
    command_name = (
        "carla-agentic-toolkit-preflight.exe"
        if os.name == "nt"
        else "carla-agentic-toolkit-preflight"
    )
    preflight = Path(sys.executable).with_name(command_name)
    env = {
        **os.environ,
        "CARLA_AGENTIC_TOOLKIT_SANDBOX": str(runner),
        "CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR": str(output_dir),
    }

    completed = subprocess.run(
        [preflight],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    report = json.loads(completed.stdout)
    assert {
        "exit_code": completed.returncode,
        "stderr": completed.stderr,
        "ok": report["ok"],
        "system": report["environment"]["system"],
        "wsl2": report["environment"]["wsl2"],
        "ruleset_enforced": report["sandbox"]["ruleset_enforced"],
        "evidence_persisted": report["output"]["evidence_persisted"],
        "artifacts_cleaned": not (output_dir / ".carla-agentic-toolkit-preflight").exists(),
    } == {
        "exit_code": 0,
        "stderr": "",
        "ok": True,
        "system": "Linux",
        "wsl2": IS_WSL2,
        "ruleset_enforced": True,
        "evidence_persisted": True,
        "artifacts_cleaned": True,
    }


@pytest.mark.skipif(IS_WSL2, reason="requires a non-WSL host")
def test_preflight_rejects_non_wsl2_when_windows_support_is_requested(
    tmp_path: Path,
) -> None:
    """Windows readiness should fail before execution outside WSL2."""
    project_root = Path(__file__).resolve().parents[1]
    output_dir = tmp_path / "outputs"
    command_name = (
        "carla-agentic-toolkit-preflight.exe"
        if os.name == "nt"
        else "carla-agentic-toolkit-preflight"
    )
    preflight = Path(sys.executable).with_name(command_name)
    env = {
        **os.environ,
        "CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR": str(output_dir),
    }

    completed = subprocess.run(
        [preflight, "--expect-wsl2"],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    report = json.loads(completed.stdout)
    assert {
        "exit_code": completed.returncode,
        "stderr": completed.stderr,
        "ok": report["ok"],
        "wsl2": report["environment"]["wsl2"],
        "error_type": report["error_type"],
        "sandbox_started": output_dir.exists(),
    } == {
        "exit_code": 1,
        "stderr": "",
        "ok": False,
        "wsl2": False,
        "error_type": "wsl2_required",
        "sandbox_started": False,
    }
