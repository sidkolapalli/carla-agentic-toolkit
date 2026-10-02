"""Prompt local start/status/stop/result operations for managed experiments."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import uuid
from typing import TYPE_CHECKING

from carla_agentic_toolkit.managed_control_io import (
    MAX_CONTROL_BYTES,
    create_stop_marker,
    private_directory,
    read_control,
    write_control,
)
from carla_agentic_toolkit.managed_liveness import (
    process_record,
    reconcile_status,
    request_recovery,
)
from carla_agentic_toolkit.simulator_lease import private_state_root

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from carla_agentic_toolkit.managed_spec import ExperimentSpec

if sys.platform == "linux":
    import fcntl

__all__ = ["MAX_ACTIVE_JOBS", "MAX_CONTROL_BYTES", "ManagedController", "launch_supervisor"]
MAX_ACTIVE_JOBS = 4
MAX_RETAINED_JOBS = 100
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")


class ManagedController:
    """Own run IDs within private state belonging to the current local OS user."""

    def __init__(self, state_root: Path | None = None) -> None:
        """Keep controls independent of simulator calls and provider latency."""
        if sys.platform != "linux":
            message = "Managed experiments require Linux or WSL2; run this CLI inside WSL2."
            raise RuntimeError(message)
        self.root = private_directory(state_root) if state_root else private_state_root()
        self._jobs = private_directory(self.root / "jobs")

    def start(self, spec: ExperimentSpec) -> dict[str, object]:
        """Reserve a bounded job and launch a detached trusted supervisor promptly."""
        spec.require_live_policy()
        descriptor = os.open(
            self._jobs / "control.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._check_capacity()
            return self._start_reserved(spec)
        finally:
            os.close(descriptor)

    def _start_reserved(self, spec: ExperimentSpec) -> dict[str, object]:
        run_id = uuid.uuid4().hex
        job = private_directory(self._jobs / run_id)
        write_control(job / "spec.json", spec.model_dump())
        status: dict[str, object] = {
            "run_id": run_id,
            "state": "starting",
            "terminated": False,
            "cancellation_requested": False,
            "cleanup": None,
        }
        write_control(job / "status.json", status)
        try:
            launch_supervisor(job)
        except OSError:
            status.update(
                state="failed",
                terminated=False,
                ok=False,
                supervisor_lost=True,
                recovery_required=True,
                cleanup={"ok": False},
                error="supervisor_launch_failed",
            )
            write_control(job / "status.json", status)
        return status

    def _check_capacity(self) -> None:
        jobs = list(self._existing_jobs())
        if len(jobs) >= MAX_RETAINED_JOBS:
            message = "Managed retained-job limit reached; archive local evidence before new runs."
            raise RuntimeError(message)
        active = sum(_active_job(job) for job in jobs)
        if active >= MAX_ACTIVE_JOBS:
            message = "Managed active-job limit reached; stop or finish existing runs."
            raise RuntimeError(message)

    def _existing_jobs(self) -> Iterator[Path]:
        for path in self._jobs.iterdir():
            if _RUN_ID.fullmatch(path.name):
                yield private_directory(path)

    def job_path(self, run_id: str) -> Path:
        """Resolve only an existing locally owned opaque run identifier."""
        if not _RUN_ID.fullmatch(run_id):
            message = "Invalid managed run ID."
            raise ValueError(message)
        job = self._jobs / run_id
        if not job.is_dir():
            message = "Unknown local managed run ID."
            raise FileNotFoundError(message)
        return private_directory(job)

    def status(self, run_id: str) -> dict[str, object]:
        """Read a bounded snapshot; never wait for a simulator, provider, or cleanup."""
        job = self.job_path(run_id)
        status = read_control(job / "status.json")
        status = reconcile_status(job, status)
        status["cancellation_requested"] = (
            bool(status.get("cancellation_requested")) or (job / "stop").exists()
        )
        return status

    def stop(self, run_id: str) -> dict[str, object]:
        """Idempotently request cancellation, distinguishing request from termination."""
        status = self.status(run_id)
        if status.get("terminated") is not True:
            create_stop_marker(self.job_path(run_id))
            status["cancellation_requested"] = True
        return status

    def result(self, run_id: str) -> dict[str, object]:
        """Return the bounded supervisor report with explicit termination/cleanup evidence."""
        return self.status(run_id)

    def recover(self, run_id: str) -> dict[str, object]:
        """Retry verified cleanup of this saved run without restarting its experiment."""
        job = self.job_path(run_id)
        return request_recovery(job, read_control(job / "status.json"))


def launch_supervisor(job: Path) -> None:
    """Detach the trusted supervisor from the CLI/MCP connection lifetime."""
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "carla_agentic_toolkit.managed_supervisor", str(job)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    write_control(job / "supervisor.json", {**process_record(process.pid), "phase": "starting"})
    threading.Thread(target=process.wait, daemon=True).start()


def _active_job(job: Path) -> bool:
    status = read_control(job / "status.json")
    if status.get("supervisor_lost") is True and status.get("state") == "failed":
        return False
    return status.get("terminated") is not True or status.get("state") == "recovering"
