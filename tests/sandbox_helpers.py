"""Shared runner selection for tests that exercise the real Linux sandbox."""

from __future__ import annotations

import os
from pathlib import Path


def sandbox_runner_path() -> Path:
    """Use an explicit test runner or the default local Cargo debug build."""
    configured = os.environ.get("CARLA_AGENTIC_TOOLKIT_SANDBOX")
    if configured:
        return Path(configured).expanduser().resolve()
    project_root = Path(__file__).resolve().parents[1]
    return project_root / "sandbox-runner" / "target" / "debug" / "carla-agentic-toolkit-sandbox"
