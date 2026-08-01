"""Behavior specs for host sandbox readiness checks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def test_preflight_proves_landlock_and_persistent_output(tmp_path: Path) -> None:
    """The public preflight should exercise the real sandbox without CARLA."""
    project_root = Path(__file__).resolve().parents[1]
    runner = project_root / "sandbox-runner" / "target" / "debug" / "carla-mcp-sandbox"
    output_dir = tmp_path / "outputs"
    command_name = "carla-mcp-preflight.exe" if os.name == "nt" else "carla-mcp-preflight"
    preflight = Path(sys.executable).with_name(command_name)
    env = {
        **os.environ,
        "CARLA_MCP_SANDBOX": str(runner),
        "CARLA_MCP_OUTPUT_DIR": str(output_dir),
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
        "artifacts_cleaned": not (output_dir / ".carla-mcp-preflight").exists(),
    } == {
        "exit_code": 0,
        "stderr": "",
        "ok": True,
        "system": "Linux",
        "wsl2": False,
        "ruleset_enforced": True,
        "evidence_persisted": True,
        "artifacts_cleaned": True,
    }


def test_preflight_rejects_non_wsl2_when_windows_support_is_requested(
    tmp_path: Path,
) -> None:
    """Windows readiness should fail before execution outside WSL2."""
    project_root = Path(__file__).resolve().parents[1]
    output_dir = tmp_path / "outputs"
    command_name = "carla-mcp-preflight.exe" if os.name == "nt" else "carla-mcp-preflight"
    preflight = Path(sys.executable).with_name(command_name)
    env = {
        **os.environ,
        "CARLA_MCP_OUTPUT_DIR": str(output_dir),
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
