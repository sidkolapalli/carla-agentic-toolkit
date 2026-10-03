"""Linux trusted-worker process-group ownership and termination primitives."""

import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def spawn_trusted(module: str, job: Path) -> subprocess.Popen[bytes]:
    """Run a reviewed package module in a separate group, without inherited output pipes."""
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", module, str(job)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={
            **os.environ,
            "CARLA_AGENTIC_TOOLKIT_SUPERVISOR_PID": str(os.getpid()),
            "CARLA_AGENTIC_TOOLKIT_JOB_ID": job.name,
        },
    )


def kill_group(process_group: int) -> None:
    """Signal only a trusted worker group created by this supervisor."""
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return


def group_alive(process_group: int) -> bool:
    """Check live group members; orphan zombies can no longer mutate the simulator."""
    return any(_live_member(path, process_group) for path in Path("/proc").glob("[0-9]*/stat"))


def _live_member(path: Path, process_group: int) -> bool:
    try:
        fields = path.read_text().rsplit(")", 1)[1].split()
    except (FileNotFoundError, ProcessLookupError):
        return False
    return fields[0] != "Z" and int(fields[2]) == process_group


def terminate_tree(process: subprocess.Popen[bytes]) -> None:
    """Retain supervision until the killed tree has no surviving mutators."""
    kill_group(process.pid)
    process.wait()
    while group_alive(process.pid):
        time.sleep(0.01)


def parent_death_guard() -> None:
    """Have Linux kill a trusted worker if its detached supervisor unexpectedly dies."""
    parent = int(os.environ.get("CARLA_AGENTIC_TOOLKIT_SUPERVISOR_PID", str(os.getppid())))
    if os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGKILL)
    libc = ctypes.CDLL(None, use_errno=True)
    pr_set_pdeathsig = 1
    if libc.prctl(pr_set_pdeathsig, signal.SIGKILL, 0, 0, 0) != 0:
        message = "Cannot arm the trusted worker parent-death guard."
        raise OSError(ctypes.get_errno(), message)
    if os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGKILL)
