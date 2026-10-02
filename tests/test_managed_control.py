"""Prompt, private lifecycle operations do not wait for simulator or inference work."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit import managed_control
from carla_agentic_toolkit.managed_control import ManagedController
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.server import build_server

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Supported Linux/WSL2 runtime")


@pytest.fixture
def controller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ManagedController:
    """Keep process execution separate from the local control-plane tests."""

    def retain_owner(job: Path) -> None:
        managed_control.write_control(
            job / "supervisor.json", managed_control.process_record(os.getpid())
        )

    monkeypatch.setattr(managed_control, "launch_supervisor", retain_owner)
    return ManagedController(tmp_path)


def test_start_status_and_idempotent_stop(controller: ManagedController) -> None:
    """Start publishes an identity immediately; stop signals without claiming death."""
    started = controller.start(ExperimentSpec())
    run_id = str(started["run_id"])
    assert len(run_id) == len("0" * 32)
    assert (started["state"], controller.status(run_id)["terminated"] is False) == (
        "starting",
        True,
    )
    first = controller.stop(run_id)
    second = controller.stop(run_id)
    assert (
        first["cancellation_requested"] is second["cancellation_requested"] is True,
        second["terminated"] is False,
        second["cleanup"] is None,
    ) == (True, True, True)


@pytest.mark.parametrize("run_id", ["../outside", "/outside/job", "a" * 33, "", "not-a-run"])
def test_rejects_nonlocal_run_ids(controller: ManagedController, run_id: str) -> None:
    """Opaque IDs cannot become arbitrary file reads or stop-file writes."""
    with pytest.raises(ValueError, match="run ID"):
        controller.status(run_id)


def test_backpressure_retains_active_runs(controller: ManagedController) -> None:
    """A local caller cannot accumulate unbounded trusted worker processes."""
    for _ in range(managed_control.MAX_ACTIVE_JOBS):
        controller.start(ExperimentSpec())
    with pytest.raises(RuntimeError, match="active"):
        controller.start(ExperimentSpec())


def test_oversized_or_symlink_status_fails_closed(controller: ManagedController) -> None:
    """A malformed status cannot cause unbounded output or cross-root file access."""
    run_id = str(controller.start(ExperimentSpec())["run_id"])
    path = controller.job_path(run_id) / "status.json"
    path.write_text(" " * (managed_control.MAX_CONTROL_BYTES + 1))
    with pytest.raises(ValueError, match="bounded"):
        controller.status(run_id)
    path.unlink()
    path.symlink_to(controller.job_path(run_id) / "spec.json")
    with pytest.raises(ValueError, match="symbolic"):
        controller.status(run_id)


def test_final_status_separates_termination_and_cleanup(controller: ManagedController) -> None:
    """Result reports completed engine evidence only after supervisor-confirmed death."""
    run_id = str(controller.start(ExperimentSpec())["run_id"])
    path = controller.job_path(run_id) / "status.json"
    status = {
        "run_id": run_id,
        "state": "failed",
        "terminated": True,
        "cancellation_requested": True,
        "cleanup": {"ok": False},
    }
    path.write_text(json.dumps(status))
    assert controller.result(run_id) == status
    assert controller.stop(run_id) == status


def test_managed_mcp_surface_is_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default users retain the existing single-tool surface."""
    monkeypatch.delenv("CARLA_AGENTIC_TOOLKIT_MANAGED_EXPERIMENTS", raising=False)
    assert [tool.name for tool in asyncio.run(build_server().list_tools())] == [
        "execute_carla_script",
    ]
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_MANAGED_EXPERIMENTS", "1")
    assert {tool.name for tool in asyncio.run(build_server().list_tools())} == {
        "execute_carla_script",
        "managed_experiment",
    }
