"""A cleanly terminated experiment can still fail its physical success criterion."""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit import managed_supervisor
from carla_agentic_toolkit.managed_control_io import write_control
from carla_agentic_toolkit.managed_spec import ExperimentSpec

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux process supervision")


@pytest.mark.parametrize("experiment_ok", [True, False, None])
def test_supervisor_preserves_experiment_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    experiment_ok: bool | None,
) -> None:
    """Termination and successful cleanup cannot turn a failed merge into a successful run."""
    job = tmp_path / "jobs" / ("f" * 32)
    job.mkdir(parents=True, mode=0o700)
    write_control(job / "spec.json", ExperimentSpec().model_dump())
    write_control(
        job / "worker.json",
        {
            "state": "completed",
            "ok": experiment_ok,
            "cleanup": {"ok": True},
            "outcome": {"completed": experiment_ok is True, "reason": "frame_limit"},
        },
    )

    def spawn(_job: Path) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [sys.executable, "-c", "pass"],
            start_new_session=True,
        )

    monkeypatch.setattr(managed_supervisor, "spawn_worker", spawn)
    result = managed_supervisor.supervise(job)
    assert result["terminated"] is True
    assert result["cleanup"] == {"ok": True}
    assert result["state"] == "completed"
    assert result["ok"] is (experiment_ok is True)
