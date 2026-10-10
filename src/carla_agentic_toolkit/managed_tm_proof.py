"""Linux ownership proofs and strict, restrict-only managed TM recovery evidence."""

from __future__ import annotations

import os
import stat
import sys
from importlib import import_module
from pathlib import Path
from typing import cast

from carla_agentic_toolkit.managed_control_io import private_directory

fcntl = import_module("fcntl") if sys.platform == "linux" else None
EVIDENCE_KEY = "managed_traffic_manager"
MAX_PORT = 65_535
MAX_EPISODE = 2**64 - 1
_SCHEMA_FIELDS = {
    "schema_version",
    "run_id",
    "world_id",
    "host_pid",
    "host_start_time",
    "host_boot_id",
    "port",
    "phase",
    "listener_inodes",
    "sync_attempted",
    "async_restored",
    "shutdown_acknowledged",
    "listener_closed",
    "failures",
}


def listening_inodes(port: int) -> set[int]:
    """Observe exact-port LISTEN sockets, not a remote client's established socket."""
    result: set[int] = set()
    for name in ("tcp", "tcp6"):
        for row in (Path("/proc/net") / name).read_text().splitlines()[1:]:
            fields = row.split()
            if fields[3] == "0A" and int(fields[1].rsplit(":", 1)[1], 16) == port:
                result.add(int(fields[9]))
    return result


def process_socket_inodes() -> set[int]:
    """Intersect listening evidence with sockets actually retained by this process."""
    result: set[int] = set()
    for entry in Path("/proc/self/fd").iterdir():
        try:
            target = entry.readlink().as_posix()
        except FileNotFoundError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            result.add(int(target[8:-1]))
    return result


def port_lock(root: Path, port: int) -> int:
    """Retain a private cooperative lock until verified shutdown or process exit."""
    if fcntl is None:
        message = "Managed Traffic Manager ownership requires Linux procfs and flock."
        raise RuntimeError(message)
    directory = private_directory(private_directory(root) / "managed-tm")
    descriptor = os.open(directory / f"{port}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        _validate_lock(descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, RuntimeError):
        os.close(descriptor)
        raise
    return descriptor


def _validate_lock(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        message = "Managed Traffic Manager lock requires an owned regular file."
        raise RuntimeError(message)
    if metadata.st_mode & 0o077:
        message = "Managed Traffic Manager lock requires private permissions."
        raise RuntimeError(message)


def failures_from_state(state: dict[str, object]) -> list[str]:
    """Recovery cannot reconstruct a host or equate worker death with deregistration."""
    if not _requires_host_proof(state):
        return []
    value = state.get(EVIDENCE_KEY)
    if not isinstance(value, dict) or not _verified_closed(cast("dict[str, object]", value), state):
        return ["Managed Traffic Manager ownership is unresolved; manual recovery is required."]
    return []


def _requires_host_proof(state: dict[str, object]) -> bool:
    return any(key in state for key in (EVIDENCE_KEY, "background_density")) or _density_records(
        state
    )


def _density_records(state: dict[str, object]) -> bool:
    return any(_declares_density(state.get(key)) for key in ("actors", "spawn_intents"))


def _declares_density(value: object) -> bool:
    return isinstance(value, list) and any(_density_record(item) for item in value)


def _density_record(value: object) -> bool:
    return isinstance(value, dict) and value.get("controller") == "managed-density"


def _verified_closed(value: dict[str, object], enclosing: dict[str, object]) -> bool:
    return (
        _closed_schema(value)
        and _closed_provenance(value)
        and _same_run(value, enclosing)
        and _closed_flags(value)
    )


def _closed_schema(value: dict[str, object]) -> bool:
    return (
        set(value) == _SCHEMA_FIELDS
        and type(value.get("schema_version")) is int
        and value["schema_version"] == 1
        and value.get("phase") == "closed"
    )


def _bounded_integer(value: object, minimum: int, maximum: int = sys.maxsize) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _closed_provenance(value: dict[str, object]) -> bool:
    numbers = (
        ("world_id", 0, MAX_EPISODE),
        ("host_pid", 1, sys.maxsize),
        ("host_start_time", 1, sys.maxsize),
        ("port", 1, MAX_PORT),
    )
    return (
        all(_bounded_integer(value.get(key), minimum, maximum) for key, minimum, maximum in numbers)
        and _closed_labels(value)
        and _closed_inodes(value.get("listener_inodes"))
    )


def _closed_labels(value: dict[str, object]) -> bool:
    return all(
        isinstance(value.get(key), str) and bool(value[key]) for key in ("run_id", "host_boot_id")
    )


def _closed_inodes(value: object) -> bool:
    return isinstance(value, list) and len(value) == 1 and _bounded_integer(value[0], 1)


def _same_run(value: dict[str, object], enclosing: dict[str, object]) -> bool:
    return (
        enclosing.get("kind") == "managed"
        and _bounded_integer(enclosing.get("world_id"), 0, MAX_EPISODE)
        and all(value.get(key) == enclosing.get(key) for key in ("world_id", "run_id"))
    )


def _closed_flags(value: dict[str, object]) -> bool:
    acknowledged = ("async_restored", "shutdown_acknowledged", "listener_closed")
    return (
        all(value.get(key) is True for key in acknowledged)
        and type(value.get("sync_attempted")) is bool
        and value.get("failures") == []
    )
