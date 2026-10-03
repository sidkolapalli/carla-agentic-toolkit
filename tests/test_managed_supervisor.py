"""Real trusted-process supervision bounds stop and verifies death before recovery."""

from __future__ import annotations

import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit import managed_supervisor
from carla_agentic_toolkit.managed_control_io import create_stop_marker, read_control, write_control
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.managed_supervisor import SupervisorLimits, supervise
from carla_agentic_toolkit.managed_worker import execute_worker

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Supported Linux/WSL2 runtime")


@pytest.fixture
def job(tmp_path: Path) -> Path:
    """Create one owned job without launching the experiment engine."""
    path = tmp_path / "jobs" / ("a" * 32)
    path.mkdir(mode=0o700, parents=True)
    write_control(path / "spec.json", ExperimentSpec(max_wall_seconds=1).model_dump())
    write_control(
        path / "status.json",
        {"run_id": path.name, "state": "starting", "terminated": False, "cleanup": None},
    )
    return path


def test_stop_kills_descendants_before_recovery(job: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even an unresponsive worker tree must die before lease-based recovery begins."""
    process: subprocess.Popen[bytes] | None = None

    def spawn(_job: Path) -> subprocess.Popen[bytes]:
        nonlocal process
        code = (
            "import subprocess,sys,time; from pathlib import Path; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "Path(sys.argv[1]).write_text(str(child.pid)); "
            "time.sleep(60)"
        )
        marker = job / "descendant.pid"
        process = subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", code, str(marker)],
            start_new_session=True,
        )
        ready_deadline = time.monotonic() + 2
        while not marker.exists() and time.monotonic() < ready_deadline:
            time.sleep(0.01)
        assert marker.exists()
        return process

    def recover(_job: Path, _spec: ExperimentSpec) -> dict[str, object]:
        assert process is not None
        assert process.poll() is not None
        assert not managed_supervisor.group_alive(process.pid)
        return {"ok": True}

    monkeypatch.setattr(managed_supervisor, "spawn_worker", spawn)
    monkeypatch.setattr(managed_supervisor, "recover_job", recover)
    create_stop_marker(job)
    start = time.monotonic()
    result = supervise(job, SupervisorLimits(poll_seconds=0.01, grace_seconds=0.1))
    elapsed_limit = 3
    assert time.monotonic() - start < elapsed_limit
    assert (
        result["terminated"] is True,
        result["state"],
        result["cleanup"],
        result["forced_termination"] is True,
    ) == (True, "cancelled", {"ok": True}, True)


def test_worker_never_claims_termination(job: Path) -> None:
    """Engine result is intermediate evidence until the supervising process reaps it."""

    def engine(
        _spec: ExperimentSpec,
        _run_id: str,
        *,
        state_root: Path,
        cancelled: Callable[[], bool],
        publish_status: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        assert state_root == job.parent.parent
        assert not cancelled()
        publish_status({"state": "running", "frame": 42})
        return {"state": "completed", "cleanup": {"ok": True}}

    execute_worker(job, engine=engine)
    result = read_control(job / "worker.json")
    assert result["state"] == "completed"
    assert result["terminated"] is False
    assert read_control(job / "status.json")["state"] == "starting"


def test_worker_spawn_failure_finishes_without_simulator_recovery(
    job: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A detached launch error must not strand a run permanently in starting."""

    def unavailable(_job: Path) -> subprocess.Popen[bytes]:
        message = "launch unavailable"
        raise OSError(message)

    monkeypatch.setattr(managed_supervisor, "spawn_worker", unavailable)
    result = supervise(job)
    assert result["state"] == "failed"
    assert result["terminated"] is True
    assert result["cleanup"] == {"ok": True, "worker_started": False}


def test_unexpected_engine_failure_is_bounded_and_private(job: Path) -> None:
    """A Python exception cannot dump credentials or leave a claimed-success result."""

    def engine(*_args: object, **_kwargs: object) -> dict[str, object]:
        message = "private-secret-value"
        raise RuntimeError(message)

    execute_worker(job, engine=engine)
    result = read_control(job / "worker.json")
    assert result["state"] == "failed"
    assert result["cleanup"] is None
    assert "private-secret-value" not in str(result)


def test_recovery_timeout_preserves_dirty_evidence(
    job: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stalled native recovery cannot reset dirty state or block status forever."""
    from carla_agentic_toolkit import managed_recovery  # noqa: PLC0415

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    monkeypatch.setattr(managed_recovery, "spawn_recovery", lambda _job: process)
    monkeypatch.setattr(managed_recovery, "RECOVERY_TIMEOUT_SECONDS", 0.05)
    marker = job.parent.parent / "dirty-evidence.json"
    marker.write_text('{"run_id":"original"}')
    result = managed_recovery.recover_job(job, ExperimentSpec())
    assert result["ok"] is False
    assert result["error"] == "recovery_timeout"
    assert process.poll() is not None
    assert marker.exists()


def test_worker_detects_supervisor_death_before_guard_arms(tmp_path: Path) -> None:
    """The exec-to-prctl race cannot leave a worker alive under PID1 ownership."""
    marker = tmp_path / "unexpected-survival"
    worker = (
        "import time; from pathlib import Path; import sys; time.sleep(.2); "
        "from carla_agentic_toolkit.managed_process import parent_death_guard; "
        "parent_death_guard(); Path(sys.argv[1]).write_text('survived')"
    )
    parent = (
        "import os,subprocess,sys; "
        "env={**os.environ,'CARLA_AGENTIC_TOOLKIT_SUPERVISOR_PID':str(os.getpid())}; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[2]],env=env, "
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    )
    subprocess.run(  # noqa: S603 - trusted Python fixture with no shell interpolation.
        [sys.executable, "-c", parent, worker, str(marker)],
        check=True,
        timeout=2,
    )
    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.parametrize("exit_code", [0, 1])
def test_recovery_retry_cannot_reuse_previous_success(
    job: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    """Each attempt must publish fresh cleanup evidence, even if its process exits normally."""
    from carla_agentic_toolkit import managed_recovery  # noqa: PLC0415

    write_control(job / "recovery.json", {"ok": True, "recovery_required": False})

    def spawn(_job: Path) -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 - fixed trusted process-exit fixture.
            [sys.executable, "-c", f"raise SystemExit({exit_code})"], start_new_session=True
        )

    monkeypatch.setattr(managed_recovery, "spawn_recovery", spawn)
    result = managed_recovery.recover_job(job, ExperimentSpec())
    assert (result["ok"], result["recovery_required"]) == (False, True)
