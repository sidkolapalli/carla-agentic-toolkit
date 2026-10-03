"""Recover one proven-dead supervisor without restarting the experiment."""

from __future__ import annotations

import os
import sys
import time
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.managed_liveness import process_matches, process_record
from carla_agentic_toolkit.managed_process import group_alive, kill_group
from carla_agentic_toolkit.managed_recovery import recover_job
from carla_agentic_toolkit.managed_spec import ExperimentSpec

if TYPE_CHECKING:
    from collections.abc import Callable

fcntl = import_module("fcntl") if sys.platform == "linux" else None
DEATH_CONFIRMATION_SECONDS = 3.0


def _group_members(group: int) -> list[Path]:
    members: list[Path] = []
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if fields[0] != "Z" and int(fields[2]) == group:
            members.append(path.parent)
    return members


def _verify_group(job: Path, group: int) -> bool:
    marker = f"CARLA_AGENTIC_TOOLKIT_JOB_ID={job.name}".encode()
    members = _group_members(group)
    for member in members:
        try:
            environment = (member / "environ").read_bytes().split(b"\0")
        except (FileNotFoundError, ProcessLookupError):
            continue
        if marker not in environment:
            message = "Worker process-group identity is no longer verifiable."
            raise RuntimeError(message)
    return bool(members)


def _stop_worker(job: Path, owner: dict[str, object]) -> None:
    group = _worker_group(job, owner)
    if group is None or not _verify_group(job, group):
        return
    kill_group(group)
    _confirm_death(group)


def _worker_group(job: Path, owner: dict[str, object]) -> int | None:
    try:
        worker = read_control(job / "worker_process.json")
    except FileNotFoundError:
        if owner.get("phase") == "starting":
            return None
        message = "Supervisor died during launch before worker identity was persisted."
        raise RuntimeError(message) from None
    group = int(str(worker["pid"]))
    if group <= 1:
        message = "Invalid recorded worker process group."
        raise ValueError(message)
    _validate_current_worker(worker)
    return group


def _validate_current_worker(worker: dict[str, object]) -> None:
    try:
        current = process_record(int(str(worker["pid"])))
    except (FileNotFoundError, ProcessLookupError):
        return
    if current != worker:
        message = "Recorded worker PID has been reused; termination is refused."
        raise RuntimeError(message)


def _confirm_death(group: int) -> None:
    deadline = time.monotonic() + DEATH_CONFIRMATION_SECONDS
    while group_alive(group):
        if time.monotonic() >= deadline:
            message = "Worker termination could not be confirmed within its recovery deadline."
            raise RuntimeError(message)
        time.sleep(0.01)


def repair_orphan(
    job: Path,
    *,
    recover: Callable[[Path, ExperimentSpec], dict[str, object]] = recover_job,
) -> dict[str, object]:
    """Hold job ownership through proven worker death and bounded simulator recovery."""
    descriptor = os.open(job / "supervisor.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _repair_locked(job, recover)
    finally:
        os.close(descriptor)


def _repair_locked(
    job: Path, recover: Callable[[Path, ExperimentSpec], dict[str, object]]
) -> dict[str, object]:
    owner = read_control(job / "supervisor.json")
    status = read_control(job / "status.json")
    if process_matches(owner):
        return status
    status.update(state="failed", ok=False, supervisor_lost=True, error="supervisor_lost")
    try:
        _stop_worker(job, owner)
        status["terminated"] = True
        spec = ExperimentSpec.model_validate(read_control(job / "spec.json"))
        cleanup = recover(job, spec)
        status["cleanup"] = cleanup
        status["recovery_required"] = cleanup.get("ok") is not True
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        status.update(
            cleanup={"ok": False},
            recovery_required=True,
            error=f"supervisor_recovery_failed:{type(error).__name__}",
        )
    write_control(job / "status.json", status)
    return status


def main() -> None:
    """Run only for a private job selected by the local lifecycle controller."""
    from carla_agentic_toolkit.managed_control import ManagedController  # noqa: PLC0415

    path = Path(sys.argv[1])
    job = ManagedController(path.parent.parent).job_path(path.name)
    repair_orphan(job)


if __name__ == "__main__":
    main()
