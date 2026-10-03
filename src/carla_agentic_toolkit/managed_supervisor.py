"""Detached bounded worker supervision, followed by verified cleanup recovery."""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from carla_agentic_toolkit.managed_control import ManagedController
from carla_agentic_toolkit.managed_control_io import create_stop_marker, read_control, write_control
from carla_agentic_toolkit.managed_liveness import record_worker, supervisor_phase
from carla_agentic_toolkit.managed_process import (
    group_alive,
    kill_group,
    spawn_trusted,
    terminate_tree,
)
from carla_agentic_toolkit.managed_recovery import recover_job
from carla_agentic_toolkit.managed_spec import ExperimentSpec

if sys.platform == "linux":
    import fcntl

if TYPE_CHECKING:
    import subprocess

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class SupervisorLimits:
    """Local cleanup grace is independent of untrusted provider response timing."""

    poll_seconds: float = 0.05
    grace_seconds: float = 10.0


def spawn_worker(job: Path) -> subprocess.Popen[bytes]:
    """Start one trusted worker in a new process group with no output pipes."""
    return spawn_trusted("carla_agentic_toolkit.managed_worker", job)


def supervise(job: Path, limits: SupervisorLimits | None = None) -> dict[str, object]:
    """Reap the worker and its descendants before final status or simulator recovery."""
    spec = ExperimentSpec.model_validate(read_control(job / "spec.json"))
    supervisor_phase(job, "launching")
    try:
        process = spawn_worker(job)
    except OSError:
        return _launch_failed(job)
    with contextlib.suppress(FileNotFoundError):
        record_worker(job, process.pid)
    monitor = _Monitor(job, spec, process, limits or SupervisorLimits())
    try:
        monitor.wait_for_death()
    except Exception as error:  # noqa: BLE001
        monitor.failure = type(error).__name__
    finally:
        terminate_tree(process)
    return monitor.finish()


def _launch_failed(job: Path) -> dict[str, object]:
    status: dict[str, object] = {
        "run_id": job.name,
        "state": "failed",
        "terminated": True,
        "ok": False,
        "cleanup": {"ok": True, "worker_started": False},
        "error": "worker_launch_failed",
        "cancellation_requested": (job / "stop").exists(),
    }
    write_control(job / "status.json", status)
    return status


@dataclass
class _Monitor:
    job: Path
    spec: ExperimentSpec
    process: subprocess.Popen[bytes]
    limits: SupervisorLimits
    stopping_since: float | None = None
    forced: bool = False
    timed_out: bool = False
    failure: str | None = None

    def wait_for_death(self) -> None:
        deadline = time.monotonic() + self.spec.max_wall_seconds
        while self.process.poll() is None:
            self._tick(deadline)
            time.sleep(self.limits.poll_seconds)
        kill_group(self.process.pid)
        while group_alive(self.process.pid):
            self._publish()
            time.sleep(self.limits.poll_seconds)

    def _tick(self, deadline: float) -> None:
        if time.monotonic() >= deadline:
            self.timed_out = True
            create_stop_marker(self.job)
        if (self.job / "stop").exists():
            self._stop_tick()
        self._publish()

    def _stop_tick(self) -> None:
        if self.stopping_since is None:
            self.stopping_since = time.monotonic()
        if time.monotonic() - self.stopping_since >= self.limits.grace_seconds:
            self.forced = True
            kill_group(self.process.pid)

    def _worker_status(self) -> dict[str, object]:
        try:
            return read_control(self.job / "worker.json")
        except (OSError, ValueError, TypeError):
            return {"state": "starting", "cleanup": None}

    def _publish(self) -> None:
        worker = self._worker_status()
        state = worker.get("state", "starting")
        if self.stopping_since is not None or state in TERMINAL_STATES:
            state = "stopping"
        worker.update(
            run_id=self.job.name,
            state=state,
            terminated=False,
            cancellation_requested=(self.job / "stop").exists(),
            worker_pid=self.process.pid,
        )
        write_control(self.job / "status.json", worker)

    def finish(self) -> dict[str, object]:
        worker = self._worker_status()
        cleanup = worker.get("cleanup")
        if not _cleanup_ok(cleanup):
            interim = {
                **worker,
                "state": "recovering",
                "terminated": True,
                "cleanup": None,
                "run_id": self.job.name,
            }
            write_control(self.job / "status.json", interim)
            cleanup = recover_job(self.job, self.spec)
        state = self._final_state(worker)
        worker.update(
            run_id=self.job.name,
            state=state,
            terminated=True,
            cleanup=cleanup,
            cancellation_requested=(self.job / "stop").exists(),
            forced_termination=self.forced,
            exit_code=self.process.returncode,
            ok=all((state == "completed", worker.get("ok") is True, _cleanup_ok(cleanup))),
            supervisor_error=self.failure,
            deadline_exceeded=self.timed_out,
        )
        write_control(self.job / "status.json", worker)
        return worker

    def _final_state(self, worker: dict[str, object]) -> str:
        if self.timed_out or self.failure:
            return "failed"
        if (self.job / "stop").exists():
            return "cancelled"
        return "completed" if _completed(worker, self.process.returncode) else "failed"


def _completed(worker: dict[str, object], returncode: int | None) -> bool:
    return returncode == 0 and worker.get("state") == "completed"


def _cleanup_ok(value: object) -> bool:
    return isinstance(value, dict) and value.get("ok") is True


def main() -> None:
    """Acquire single-supervisor ownership for one prevalidated local job."""
    path = Path(sys.argv[1])
    job = ManagedController(path.parent.parent).job_path(path.name)
    descriptor = os.open(job / "supervisor.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        signal.signal(signal.SIGTERM, lambda *_args: create_stop_marker(job))
        if not _await_registration(job):
            supervisor_phase(job, "starting")
            _launch_failed(job)
            return
        supervise(job)
    finally:
        os.close(descriptor)


def _await_registration(job: Path) -> bool:
    deadline = time.monotonic() + 2.0
    while not (job / "supervisor.json").exists():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


if __name__ == "__main__":
    main()
