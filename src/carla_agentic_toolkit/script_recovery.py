"""Trusted, bounded cleanup of finite and persistent script ownership journals."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.managed_process import parent_death_guard, terminate_tree
from carla_agentic_toolkit.managed_session import world_identity
from carla_agentic_toolkit.ownership import OWNERSHIP_FILENAME, RunOwnership, cleanup_report
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.carla_protocols import CarlaWorld

MAX_JOURNAL_BYTES = 65_536
MAX_OWNED_ACTORS = 4096
MAX_ACTOR_ID = 2**32 - 1
MAX_CLEANUP_SECONDS = 30.0


def _failure(reason: str) -> dict[str, object]:
    failure: dict[str, object] = {"actor_id": None, "error": reason}
    return cleanup_report(failures=(failure,))


def _private_existing_directory(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
        message = "Ownership journal directory must belong to this local user."
        raise ValueError(message)
    if metadata.st_mode & 0o077:
        message = "Ownership journal directory must have private permissions 0700."
        raise ValueError(message)


def _journal_root(path: Path) -> Path:
    if path.name != OWNERSHIP_FILENAME or path != path.resolve():
        message = "Ownership journal must use a canonical path without symbolic links."
        raise ValueError(message)
    root = path.parent.parent if path.parent.name == "work" else path.parent
    if not _known_root(root):
        message = "Ownership journal must be inside a known script temporary root."
        raise ValueError(message)
    _private_existing_directory(root)
    _private_existing_directory(path.parent)
    return root


def _known_root(root: Path) -> bool:
    prefixes = ("carla-agentic-toolkit-script-", "carla-script-session-")
    return root.parent == Path(tempfile.gettempdir()).resolve() and root.name.startswith(prefixes)


def _validate_journal_file(path: Path) -> None:
    _journal_root(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
        ):
            message = "Ownership journal must be a regular file with one link, owned by this user."
            raise ValueError(message)
        payload = stream.read(MAX_JOURNAL_BYTES + 1)
    if len(payload) > MAX_JOURNAL_BYTES:
        message = "Ownership journal exceeds its 64 KiB recovery limit."
        raise ValueError(message)
    json.loads(payload)


def _ownership(path: Path) -> RunOwnership:
    _validate_journal_file(path)
    ownership = RunOwnership(path)
    actor_ids = ownership.actor_ids()
    _bound_actor_ids(actor_ids)
    if actor_ids and ownership.world_id() is None:
        message = "Nonempty ownership journal has no episode identity; manual recovery is required."
        raise ValueError(message)
    return ownership


def _bound_actor_ids(actor_ids: tuple[int, ...]) -> None:
    if len(actor_ids) > MAX_OWNED_ACTORS or any(actor_id > MAX_ACTOR_ID for actor_id in actor_ids):
        message = "Ownership journal exceeds the bounded actor ID limits."
        raise ValueError(message)


def _validate_deadline(timeout: float) -> None:
    if not 0 < timeout <= MAX_CLEANUP_SECONDS:
        message = "Cleanup deadline must be positive and at most 30 seconds."
        raise ValueError(message)


def cleanup_script_ownership(
    host: str,
    port: int,
    ownership_path: Path,
    lease_descriptor: int,
    timeout_seconds: float,
) -> dict[str, object]:
    """Clean under the caller's inherited lease and a hard native-process deadline."""
    try:
        _validate_deadline(timeout_seconds)
        ownership = _ownership(ownership_path)
        if not ownership.actor_ids():
            return cleanup_report()
        return _bounded_cleanup(host, port, ownership_path, lease_descriptor, timeout_seconds)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        return _failure(str(error))


def _bounded_cleanup(
    host: str, port: int, path: Path, lease_descriptor: int, timeout: float
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="carla-script-cleanup-") as directory:
        job = Path(directory)
        write_control(
            job / "request.json", {"host": host, "port": port, "ownership_path": str(path)}
        )
        process = _spawn_cleanup(job, lease_descriptor)
        try:
            exit_code = process.wait(timeout=timeout)
            return _cleanup_result(job, exit_code)
        except subprocess.TimeoutExpired:
            return _failure("Script actor cleanup exceeded its native-process deadline.")
        finally:
            terminate_tree(process)


def _cleanup_result(job: Path, exit_code: int) -> dict[str, object]:
    path = job / "result.json"
    worker = {"exit_code": exit_code, "result_available": path.exists()}
    if exit_code != 0:
        reason = (
            f"Script actor cleanup worker exited with status {exit_code}; "
            "a negative status identifies the terminating signal. Recovery is required."
        )
        return _failure(reason) | {"worker": worker}
    try:
        return read_control(path)
    except (OSError, TypeError, ValueError):
        reason = "Script actor cleanup worker did not publish a readable bounded cleanup report."
        return _failure(reason) | {"worker": worker}


def _spawn_cleanup(job: Path, lease_descriptor: int) -> subprocess.Popen[bytes]:
    return subprocess.Popen(  # noqa: S603 - fixed trusted module with separate validated arguments.
        [sys.executable, "-m", "carla_agentic_toolkit.script_recovery", str(job)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        pass_fds=(lease_descriptor,),
        env={**os.environ, "CARLA_AGENTIC_TOOLKIT_SUPERVISOR_PID": str(os.getpid())},
    )


def _fresh_cleanup_snapshot(world: CarlaWorld) -> None:
    before = world.get_snapshot().frame
    if world.get_settings().synchronous_mode:
        frame = world.tick()
    else:
        frame = world.wait_for_tick(5.0).frame
    if frame <= before or world.get_snapshot().frame < frame:
        message = "Script cleanup could not verify a fresh actor snapshot."
        raise RuntimeError(message)


def _cleanup_connected(adapter: PythonCarlaAdapter, ownership: RunOwnership) -> dict[str, object]:
    from carla_agentic_toolkit.ownership import cleanup_owned_actors  # noqa: PLC0415

    # Trusted cleanup retains the same adapter connection through snapshot and destruction.
    world = adapter._client().get_world()  # noqa: SLF001
    original_world = world_identity(world)
    if original_world != ownership.world_id():
        ownership.clear()
        return cleanup_report()
    _fresh_cleanup_snapshot(world)
    if world_identity(adapter._client().get_world()) != original_world:  # noqa: SLF001
        message = "World changed while publishing the script cleanup snapshot."
        raise RuntimeError(message)
    return cleanup_owned_actors(adapter, ownership)


def _worker_cleanup(
    request: dict[str, object],
) -> tuple[dict[str, object], PythonCarlaAdapter | None]:
    """Return the result and retain its native client until the worker exits."""
    from carla_agentic_toolkit.adapter import PythonCarlaAdapter  # noqa: PLC0415

    adapter = None
    try:
        ownership = _ownership(Path(str(request["ownership_path"])))
        adapter = PythonCarlaAdapter(
            host=str(request["host"]), port=int(str(request["port"])), timeout=5.0
        )
        report = _cleanup_connected(adapter, ownership)
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        report = _failure(str(error))
    return report, adapter


def recover_script_ownership(
    host: str, port: int, *, state_root: Path | None = None, timeout_seconds: float = 10.0
) -> dict[str, object]:
    """Explicitly recover a dead script owner; missing or ambiguous journals remain dirty."""
    with SimulatorLease(host, port, state_root=state_root, recovering=True) as lease:
        state = lease.recovery_state
        if not state:
            return {"ok": True, "recovery_required": False, "cleanup": cleanup_report()}
        if state.get("kind") not in {"script", "script_session"}:
            return {
                "ok": False,
                "recovery_required": True,
                "cleanup": _failure("Not a script lease."),
            }
        report = cleanup_script_ownership(
            host,
            port,
            Path(str(state.get("ownership_path", ""))),
            lease.descriptor,
            timeout_seconds,
        )
        success = not report.get("failures")
        if success:
            lease.mark_clean()
        return {"ok": success, "recovery_required": not success, "cleanup": report}


def main() -> None:
    """Run trusted cleanup in a parent-bound process with a borrowed simulator lease."""
    parent_death_guard()
    job = Path(sys.argv[1])
    request = read_control(job / "request.json")
    report, _native_client_owner = _worker_cleanup(request)
    write_control(job / "result.json", report)
    # The report is flushed, fsynced and atomically published. Native streaming
    # finalizers can crash during client teardown; this disposable process owns
    # no further application work. Kernel exit closes its sockets and borrowed
    # lease descriptor; the supervisor still reaps its entire process group.
    os._exit(0)


if __name__ == "__main__":
    main()
