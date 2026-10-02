"""Prompt process-identity checks and single-owner scheduling of lost-supervisor recovery."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from importlib import import_module
from pathlib import Path

from carla_agentic_toolkit.managed_control_io import read_control, write_control

fcntl = import_module("fcntl") if sys.platform == "linux" else None
MAX_REPAIR_ATTEMPTS = 3


def process_record(pid: int) -> dict[str, object]:
    """Bind a PID to its kernel start instant and boot, preventing PID-reuse signals."""
    fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    return {
        "pid": pid,
        "start_ticks": int(fields[19]),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
    }


def process_matches(record: dict[str, object]) -> bool:
    """Treat absent/zombie processes as dead without mistaking a reused PID for its owner."""
    try:
        pid = int(str(record["pid"]))
        fields = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
        return fields[0] != "Z" and process_record(pid) == {
            key: record[key] for key in ("pid", "start_ticks", "boot_id")
        }
    except (FileNotFoundError, ProcessLookupError):
        return False


def record_worker(job: Path, pid: int) -> None:
    """Persist mutation-worker identity before the trusted engine can acquire its lease."""
    write_control(job / "worker_process.json", process_record(pid))


def supervisor_phase(job: Path, phase: str) -> None:
    """Declare whether worker launch has begun, including its otherwise ambiguous crash window."""
    write_control(job / "supervisor.json", {**process_record(os.getpid()), "phase": phase})


def reconcile_status(job: Path, status: dict[str, object]) -> dict[str, object]:
    """Queue recovery after proven supervisor death; do no simulator work in public status."""
    if status.get("terminated") is True and status.get("state") != "recovering":
        return status
    try:
        owner = read_control(job / "supervisor.json")
    except FileNotFoundError:
        return _repair_unavailable(job, status)
    if process_matches(owner):
        return status
    return _queue_repair(job, status)


def request_recovery(job: Path, status: dict[str, object]) -> dict[str, object]:
    """Explicitly retry the saved local job after its original supervisor has stopped."""
    if process_matches(read_control(job / "supervisor.json")):
        message = "An active supervisor still owns this run; request stop before recovery."
        raise RuntimeError(message)
    return _queue_repair(job, status, explicit=True)


def _queue_repair(
    job: Path, status: dict[str, object], *, explicit: bool = False
) -> dict[str, object]:
    descriptor = os.open(job / "repair.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return status
        return _launch_repair(job, descriptor, explicit=explicit)
    finally:
        os.close(descriptor)


def _launch_repair(job: Path, descriptor: int, *, explicit: bool) -> dict[str, object]:
    status = read_control(job / "status.json")
    if _repair_complete(status, explicit=explicit):
        return status
    attempts = 0 if explicit else int(str(status.get("repair_attempts", 0)))
    if attempts >= MAX_REPAIR_ATTEMPTS:
        return _repair_unavailable(job, status)
    status.update(
        state="recovering",
        supervisor_lost=True,
        ok=False,
        repair_attempts=attempts + 1,
        recovery_required=True,
    )
    write_control(job / "status.json", status)
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed trusted module and local job path.
            [sys.executable, "-m", "carla_agentic_toolkit.managed_orphan", str(job)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(descriptor,),
        )
    except OSError:
        return _repair_unavailable(job, status)
    threading.Thread(target=process.wait, daemon=True).start()
    return status


def _repair_complete(status: dict[str, object], *, explicit: bool) -> bool:
    terminal = status.get("terminated") is True and status.get("state") != "recovering"
    return terminal and (not explicit or _cleanup_verified(status))


def _cleanup_verified(status: dict[str, object]) -> bool:
    cleanup = status.get("cleanup")
    if not isinstance(cleanup, dict):
        return False
    return cleanup.get("ok") is True and status.get("recovery_required") is not True


def _repair_unavailable(job: Path, status: dict[str, object]) -> dict[str, object]:
    status.update(
        state="failed",
        ok=False,
        supervisor_lost=True,
        recovery_required=True,
        error="supervisor_recovery_unavailable",
        cleanup={"ok": False},
    )
    write_control(job / "status.json", status)
    return status
