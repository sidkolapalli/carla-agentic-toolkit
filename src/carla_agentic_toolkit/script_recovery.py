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

from carla_agentic_toolkit.creation_health import add_creation_failures
from carla_agentic_toolkit.errors import OwnershipError
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.managed_process import parent_death_guard, terminate_tree
from carla_agentic_toolkit.managed_session import world_identity
from carla_agentic_toolkit.ownership import OWNERSHIP_FILENAME, RunOwnership, cleanup_report
from carla_agentic_toolkit.recovery_snapshots import fresh_cleanup_frame
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
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


def _journal_root(path: Path, *, filename: str = OWNERSHIP_FILENAME) -> Path:
    if path.name != filename or path != path.resolve():
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


def _validate_journal_file(path: Path, *, filename: str = OWNERSHIP_FILENAME) -> None:
    _journal_root(path, filename=filename)
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
    if (actor_ids or ownership.pending_creations()) and ownership.world_id() is None:
        message = "Nonempty or unresolved ownership journal requires an episode identity."
        raise ValueError(message)
    return ownership


def _settings_record(path: Path, *, required: bool) -> RunSettings | None:
    settings_path = path.with_name(SETTINGS_FILENAME)
    if not required and not settings_path.exists() and not settings_path.is_symlink():
        return None
    _validate_journal_file(settings_path, filename=SETTINGS_FILENAME)
    settings = RunSettings(settings_path, require_existing=True)
    settings.pending()
    return settings


def _bound_actor_ids(actor_ids: tuple[int, ...]) -> None:
    if len(actor_ids) > MAX_OWNED_ACTORS or any(actor_id > MAX_ACTOR_ID for actor_id in actor_ids):
        message = "Ownership journal exceeds the bounded actor ID limits."
        raise ValueError(message)


def _validate_deadline(timeout: float) -> None:
    if not 0 < timeout <= MAX_CLEANUP_SECONDS:
        message = "Cleanup deadline must be positive and at most 30 seconds."
        raise ValueError(message)


def cleanup_script_ownership(  # noqa: PLR0913 - retain the existing five positional arguments.
    host: str,
    port: int,
    ownership_path: Path,
    lease_descriptor: int,
    timeout_seconds: float,
    *,
    require_settings: bool = False,
    destroy_actors: bool = True,
) -> dict[str, object]:
    """Clean under the caller's inherited lease and a hard native-process deadline."""
    ownership = None
    try:
        _validate_deadline(timeout_seconds)
        ownership = _ownership(ownership_path)
        settings = _settings_record(ownership_path, required=require_settings)
        offline = _offline_cleanup(ownership, settings, destroy_actors=destroy_actors)
        if offline is not None:
            return offline
        return _bounded_cleanup(
            _cleanup_request(
                host,
                port,
                ownership_path,
                require_settings=settings is not None,
                destroy_actors=destroy_actors,
            ),
            lease_descriptor,
            timeout_seconds,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        report = _failure(str(error))
        return add_creation_failures(report, ownership) if ownership is not None else report


def _offline_cleanup(
    ownership: RunOwnership, settings: RunSettings | None, *, destroy_actors: bool
) -> dict[str, object] | None:
    if settings is not None and settings.pending():
        return None
    if destroy_actors and ownership.actor_ids():
        return None
    return add_creation_failures(_empty_cleanup_evidence(settings), ownership)


def _empty_cleanup_evidence(settings: RunSettings | None) -> dict[str, object]:
    report = cleanup_report()
    return report if settings is None else report | {"settings_restored": True}


def _cleanup_request(
    host: str,
    port: int,
    path: Path,
    *,
    require_settings: bool,
    destroy_actors: bool,
) -> dict[str, object]:
    return {
        "host": host,
        "port": port,
        "ownership_path": str(path),
        "require_settings": require_settings,
        "destroy_actors": destroy_actors,
    }


def _bounded_cleanup(
    request: dict[str, object], lease_descriptor: int, timeout: float
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="carla-script-cleanup-") as directory:
        job = Path(directory)
        write_control(job / "request.json", request)
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
    fresh_cleanup_frame(
        world, failure_message="Script cleanup could not verify a fresh actor snapshot."
    )


def _cleanup_connected(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership,
    settings: RunSettings | None = None,
    *,
    destroy_actors: bool = True,
) -> dict[str, object]:
    from carla_agentic_toolkit.ownership import cleanup_owned_actors  # noqa: PLC0415

    restored = _restore_settings(adapter, settings)
    if restored.get("failures"):
        return add_creation_failures(restored, ownership)
    if not _actor_cleanup_needed(ownership, settings, destroy_actors=destroy_actors):
        return add_creation_failures(restored, ownership)
    # Restore timing before publishing any actor snapshot on the same connection.
    world = adapter._client().get_world()  # noqa: SLF001
    original_world = world_identity(world)
    if original_world != ownership.world_id():
        return _clear_old_world(ownership, restored)
    _fresh_cleanup_snapshot(world)
    if world_identity(adapter._client().get_world()) != original_world:  # noqa: SLF001
        message = "World changed while publishing the script cleanup snapshot."
        raise RuntimeError(message)
    return restored | cleanup_owned_actors(adapter, ownership)


def _clear_old_world(ownership: RunOwnership, restored: dict[str, object]) -> dict[str, object]:
    try:
        ownership.clear(require_completed=True)
    except OwnershipError as exc:
        return restored | _failure(str(exc))
    return restored


def _restore_settings(
    adapter: PythonCarlaAdapter, settings: RunSettings | None
) -> dict[str, object]:
    return settings.restore(adapter) if settings is not None else cleanup_report()


def _actor_cleanup_needed(
    ownership: RunOwnership, settings: RunSettings | None, *, destroy_actors: bool
) -> bool:
    if not destroy_actors:
        return False
    if settings is None:
        return True
    return bool(ownership.actor_ids())


def _worker_cleanup(
    request: dict[str, object],
) -> tuple[dict[str, object], PythonCarlaAdapter | None]:
    """Return the result and retain its native client until the worker exits."""
    from carla_agentic_toolkit.adapter import PythonCarlaAdapter  # noqa: PLC0415

    adapter = None
    ownership = None
    try:
        path = Path(str(request["ownership_path"]))
        ownership = _ownership(path)
        settings = _settings_record(path, required=request.get("require_settings") is True)
        adapter = PythonCarlaAdapter(
            host=str(request["host"]), port=int(str(request["port"])), timeout=5.0
        )
        report = _cleanup_connected(
            adapter, ownership, settings, destroy_actors=request.get("destroy_actors") is not False
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        report = _failure(str(error))
        if ownership is not None:
            report = add_creation_failures(report, ownership)
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
        report = _recover_lease_settings(host, port, state, lease.descriptor, timeout_seconds)
        success = not report.get("failures")
        if success:
            lease.mark_clean()
        return {"ok": success, "recovery_required": not success, "cleanup": report}


def _recover_lease_settings(
    host: str, port: int, state: dict[str, object], descriptor: int, timeout: float
) -> dict[str, object]:
    try:
        path = Path(str(state.get("ownership_path", "")))
        require_settings = "settings_path" in state
        if require_settings and state["settings_path"] != str(path.with_name(SETTINGS_FILENAME)):
            return _failure("Settings journal must be beside the recorded ownership journal.")
        return cleanup_script_ownership(
            host, port, path, descriptor, timeout, require_settings=require_settings
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        return _failure(str(error))


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
