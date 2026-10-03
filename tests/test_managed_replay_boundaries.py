"""Saved-context replay cannot be launched into a fresh live CARLA scene."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import managed_control, managed_engine
from carla_agentic_toolkit.errors import UnsupportedFeatureError
from carla_agentic_toolkit.managed_control import ManagedController
from carla_agentic_toolkit.managed_spec import ExperimentSpec

if TYPE_CHECKING:
    from pathlib import Path

RUN_ID = "1" * 32


def test_public_start_rejects_live_replay_before_job_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI and MCP start share the guard before any job, supervisor, or simulator work."""
    launch = Mock()
    monkeypatch.setattr(managed_control, "launch_supervisor", launch)
    controller = ManagedController(tmp_path)
    spec = ExperimentSpec(policy="replay", replay_run_id="0" * 32)

    with pytest.raises(UnsupportedFeatureError, match="offline saved-context replay"):
        controller.start(spec)

    launch.assert_not_called()
    assert list((tmp_path / "jobs").iterdir()) == []


@pytest.mark.parametrize("entrypoint", ["sync", "async"])
def test_direct_run_rejects_live_replay_before_simulator_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    """Bypassing public control still cannot acquire a lease, connect, or create a trace."""
    connect = Mock(side_effect=AssertionError("No simulator access is allowed."))
    lease = Mock(side_effect=AssertionError("No lease acquisition is allowed."))
    monkeypatch.setattr(managed_engine, "connect_client", connect)
    monkeypatch.setattr(managed_engine, "SimulatorLease", lease)
    spec = ExperimentSpec(policy="replay", replay_run_id="0" * 32)
    with pytest.raises(UnsupportedFeatureError, match="offline saved-context replay"):
        _run_direct(spec, tmp_path, entrypoint)

    connect.assert_not_called()
    lease.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def _run_direct(spec: ExperimentSpec, root: Path, entrypoint: str) -> dict[str, object]:
    """Exercise both public engine entrypoints with equivalent trusted inputs."""
    if entrypoint == "sync":
        return managed_engine.run_experiment(
            spec,
            RUN_ID,
            state_root=root,
            cancelled=lambda: False,
            publish_status=lambda _value: None,
        )
    return asyncio.run(
        managed_engine.run_experiment_async(
            spec,
            RUN_ID,
            state_root=root,
            cancelled=lambda: False,
            publish_status=lambda _value: None,
        )
    )
