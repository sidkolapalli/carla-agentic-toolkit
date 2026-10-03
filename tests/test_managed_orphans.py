"""A lost supervisor must not strand public lifecycle clients or release live workers."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING

from carla_agentic_toolkit import managed_control, managed_liveness, managed_supervisor
from carla_agentic_toolkit.managed_control import ManagedController
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.managed_liveness import process_matches, process_record
from carla_agentic_toolkit.managed_orphan import repair_orphan
from carla_agentic_toolkit.managed_process import group_alive, kill_group
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

PROMPT_SECONDS = 0.5


def wait_for_marker(path: Path) -> None:
    """Wait for the fixture's explicit readiness barrier with a bounded deadline."""
    deadline = time.monotonic() + 5
    while not path.exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_supervisor_startup_exit_becomes_prompt_terminal_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed supervisor import cannot leave CLI run polling starting forever."""
    children: list[subprocess.Popen[bytes]] = []

    def failed(job: Path) -> None:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.1)"])
        write_control(job / "supervisor.json", {**process_record(child.pid), "phase": "starting"})
        children.append(child)

    monkeypatch.setattr(managed_control, "launch_supervisor", failed)
    controller = ManagedController(tmp_path)
    run_id = str(controller.start(ExperimentSpec())["run_id"])
    children[0].wait(timeout=2)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        before = time.monotonic()
        result = controller.status(run_id)
        assert time.monotonic() - before < PROMPT_SECONDS
        if result.get("state") == "failed":
            break
        time.sleep(0.02)
    assert (result["state"], result["terminated"], result["ok"]) == ("failed", True, False)


def test_killed_supervisor_recovers_only_after_its_live_descendant_dies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real SIGKILL leaves a descendant; recovery verifies group death before clearing lease."""
    worker = """
import os,sys,time,subprocess
from pathlib import Path
from carla_agentic_toolkit.managed_liveness import record_worker
from carla_agentic_toolkit.managed_process import parent_death_guard
from carla_agentic_toolkit.simulator_lease import SimulatorLease
parent_death_guard()
job=Path(sys.argv[1])
record_worker(job,os.getpid())
with SimulatorLease('localhost',3000,state_root=job.parent.parent) as lease:
 lease.mark_dirty({'kind':'managed','run_id':job.name})
 child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
 (job/'ready').write_text(str(child.pid))
 time.sleep(60)
"""
    parent = """
import os,sys,time,subprocess
from pathlib import Path
from carla_agentic_toolkit.managed_liveness import supervisor_phase
job=Path(sys.argv[1])
while not (job/'supervisor.json').exists(): time.sleep(.01)
supervisor_phase(job,'launching')
subprocess.Popen([sys.executable,'-c',sys.argv[2],str(job)],start_new_session=True,
 env={**os.environ,'CARLA_AGENTIC_TOOLKIT_JOB_ID':job.name,
 'CARLA_AGENTIC_TOOLKIT_SUPERVISOR_PID':str(os.getpid())})
time.sleep(60)
"""
    owners: list[subprocess.Popen[bytes]] = []

    def launch(job: Path) -> None:
        process = subprocess.Popen(  # noqa: S603 - trusted process-lifetime fixture.
            [sys.executable, "-c", parent, str(job), worker]
        )
        write_control(job / "supervisor.json", {**process_record(process.pid), "phase": "starting"})
        owners.append(process)

    monkeypatch.setattr(managed_control, "launch_supervisor", launch)
    controller = ManagedController(tmp_path)
    job = controller.job_path(str(controller.start(ExperimentSpec(port=3000))["run_id"]))
    wait_for_marker(job / "ready")
    record = read_control(job / "worker_process.json")
    group = int(str(record["pid"]))
    assert process_matches(record)
    owners[0].kill()
    owners[0].wait(timeout=2)
    assert group_alive(group)

    def recover(_job: Path, _spec: ExperimentSpec) -> dict[str, object]:
        assert not group_alive(group)
        with SimulatorLease("localhost", 3000, state_root=tmp_path, recovering=True) as lease:
            assert lease.recovery_state["run_id"] == job.name
            lease.mark_clean()
        return {"ok": True}

    try:
        result = repair_orphan(job, recover=recover)
        assert (result["terminated"], result["cleanup"], result["state"]) == (
            True,
            {"ok": True},
            "failed",
        )
    finally:
        kill_group(group)


def test_failed_registration_does_not_claim_worker_termination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure after Popen is not proof that no worker can exist."""

    def failed(_job: Path) -> None:
        message = "Identity persistence failed after launch."
        raise OSError(message)

    monkeypatch.setattr(managed_control, "launch_supervisor", failed)
    controller = ManagedController(tmp_path)
    result = controller.start(ExperimentSpec())
    assert (result["state"], result["terminated"], result["cleanup"]) == (
        "failed",
        False,
        {"ok": False},
    )
    assert controller.status(str(result["run_id"]))["supervisor_lost"] is True


def test_explicit_retry_recovers_saved_job_after_automatic_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operators can retry a failed cleanup after restoring CARLA, without deleting evidence."""
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(.1)"])
    owner = process_record(process.pid)
    process.wait(timeout=2)
    monkeypatch.setattr(managed_control, "launch_supervisor", lambda _job: None)
    controller = ManagedController(tmp_path)
    run_id = str(controller.start(ExperimentSpec())["run_id"])
    job = controller.job_path(run_id)
    write_control(job / "supervisor.json", {**owner, "phase": "starting"})
    write_control(
        job / "status.json",
        {
            "run_id": run_id,
            "state": "failed",
            "terminated": True,
            "ok": False,
            "cleanup": {"ok": False},
            "recovery_required": True,
            "repair_attempts": 3,
        },
    )
    requested = controller.recover(run_id)
    assert (requested["state"], requested["repair_attempts"]) == ("recovering", 1)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = controller.result(run_id)
        if result["state"] == "failed":
            break
        time.sleep(0.02)
    assert (result["run_id"], result["cleanup"], result["recovery_required"]) == (
        run_id,
        {"ok": True, "recovery_required": False},
        False,
    )


def test_supervisor_waits_for_launch_identity_before_spawning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launcher's initial phase can never overwrite a child that already began mutation."""
    monkeypatch.setattr(managed_control, "launch_supervisor", lambda _job: None)
    controller = ManagedController(tmp_path)
    job = controller.job_path(str(controller.start(ExperimentSpec())["run_id"]))
    events: list[str] = []

    def publish(_seconds: float) -> None:
        events.append("identity")
        write_control(job / "supervisor.json", {**process_record(os.getpid()), "phase": "starting"})

    monkeypatch.setattr(
        managed_supervisor, "time", SimpleNamespace(monotonic=lambda: 0, sleep=publish)
    )
    monkeypatch.setattr(managed_supervisor, "supervise", lambda _job: events.append("spawn"))
    monkeypatch.setattr(sys, "argv", ["managed_supervisor", str(job)])
    managed_supervisor.main()
    assert events == ["identity", "spawn"]


def test_stale_status_does_not_repeat_verified_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delayed reader and explicit retry both preserve an already verified cleanup result."""
    monkeypatch.setattr(managed_control, "launch_supervisor", lambda _job: None)
    controller = ManagedController(tmp_path)
    stale = controller.start(ExperimentSpec())
    job = controller.job_path(str(stale["run_id"]))
    write_control(job / "supervisor.json", {"pid": os.getpid(), "start_ticks": 0, "boot_id": "old"})
    terminal = {
        **stale,
        "state": "failed",
        "terminated": True,
        "cleanup": {"ok": True},
        "recovery_required": False,
    }
    write_control(job / "status.json", terminal)
    assert managed_liveness.reconcile_status(job, stale) == terminal
    assert controller.recover(job.name) == terminal
