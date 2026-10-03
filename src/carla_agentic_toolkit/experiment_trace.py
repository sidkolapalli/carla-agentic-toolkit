"""Authoritative private experiment event traces, metrics, and recorded replay."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self

from carla_agentic_toolkit.experiment_metrics import summarize_trace, write_trace_report
from carla_agentic_toolkit.recorded_policy import RecordedPolicy
from carla_agentic_toolkit.sandbox_paths import output_dir_path, read_only_paths
from carla_agentic_toolkit.simulator_lease import fcntl, private_state_root
from carla_agentic_toolkit.trace_schema import (
    SCHEMA_VERSION,
    identity_digest,
)
from carla_agentic_toolkit.trace_schema import (
    canonical_json as _canonical_json,
)
from carla_agentic_toolkit.trace_schema import (
    validate_event as _validate_event,
)
from carla_agentic_toolkit.trace_schema import (
    validate_identifier as _validate_identifier,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

__all__ = [
    "RecordedDecisionReplay",
    "RecordedPolicy",
    "TraceLimitError",
    "TraceRead",
    "TraceStore",
    "identity_digest",
    "load_trace",
    "summarize_trace",
    "write_trace_report",
]

DEFAULT_MAX_BYTES = 16 * 1024 * 1024
MAX_TRACE_BYTES = 64 * 1024 * 1024
MAX_EVENT_BYTES = 256 * 1024


class TraceLimitError(ValueError):
    """A run exhausted its explicit retained-evidence budget."""


@dataclass(frozen=True)
class TraceRead:
    """Validated complete records plus evidence of damaged or interrupted records."""

    events: tuple[dict[str, Any], ...]
    complete: bool
    errors: tuple[str, ...] = ()


class TraceStore:
    """Append durable events to a unique private run, with one trusted writer."""

    def __init__(
        self, run_id: str, *, root: Path | None = None, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> None:
        """Create a new run exclusively; existing runs require explicit resume."""
        self._configure(run_id, root, max_bytes)
        self.directory.mkdir(mode=0o700)
        self._fd = _open_writer(self.path, create=True)

    def _configure(self, run_id: str, root: Path | None, max_bytes: int) -> None:
        """Validate identity and retention before touching run evidence."""
        _validate_identifier(run_id, "run_id")
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_TRACE_BYTES:
            message = f"max_bytes must be an integer in 1..{MAX_TRACE_BYTES}."
            raise ValueError(message)
        self.run_id = run_id
        self.directory = _trace_root(root) / "runs" / run_id
        self.directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = self.directory / "events.jsonl"
        self.max_bytes = max_bytes
        self._bytes = 0
        self._sequence = 0
        self._fd: int | None = None
        self._failed = False
        self._lock = threading.Lock()

    @classmethod
    def resume(
        cls, run_id: str, *, root: Path | None = None, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> Self:
        """Resume only after the previous writer released its OS lock."""
        self = cls.__new__(cls)
        self._configure(run_id, root, max_bytes)
        self._fd = _open_writer(self.path, create=False)
        try:
            self._resume_records()
        except BaseException:
            self.close()
            raise
        return self

    def _resume_records(self) -> None:
        """Preserve torn bytes while linking subsequent recovery events explicitly."""
        read = load_trace(self.path)
        _require_resumable(read, self.run_id)
        self._sequence = len(read.events)
        self._bytes = self.path.stat().st_size
        _finish_partial_line(self)
        if not read.complete:
            self.append(
                "trace_interrupted",
                world_generation=_last_world(read),
                frame=None,
                data={"reason": "incomplete JSON record preserved during verified recovery"},
            )

    def append(
        self,
        kind: str,
        *,
        world_generation: str,
        frame: int | None,
        actor_id: int | None = None,
        data: dict[str, object],
    ) -> dict[str, Any]:
        """Persist one validated event before acknowledging it to the coordinator."""
        with self._lock:
            event = self._event(kind, world_generation, frame, actor_id, data)
            encoded = (_canonical_json(event) + "\n").encode("utf-8")
            self._append_bytes(encoded)
            self._sequence += 1
            return event

    def _event(
        self,
        kind: str,
        world_generation: str,
        frame: int | None,
        actor_id: int | None,
        data: dict[str, object],
    ) -> dict[str, Any]:
        """Copy numerical evidence into the one versioned event envelope."""
        event = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "world_generation": world_generation,
            "actor_id": actor_id,
            "frame": frame,
            "sequence": self._sequence + 1,
            "kind": kind,
            "monotonic_seconds": time.monotonic(),
            "wall_time": datetime.now(UTC).isoformat(),
            "data": data,
        }
        _validate_event(event)
        return json.loads(_canonical_json(event))

    def _append_bytes(self, encoded: bytes) -> None:
        """Fail closed after interrupted output; never overwrite retained bytes."""
        descriptor = self._writable_descriptor()
        if len(encoded) > MAX_EVENT_BYTES or self._bytes + len(encoded) > self.max_bytes:
            message = "Trace byte limit exceeded; the experiment must stop with invalid evidence."
            raise TraceLimitError(message)
        try:
            _write_all(descriptor, encoded)
            os.fsync(descriptor)
        except OSError:
            self._failed = True
            raise
        self._bytes += len(encoded)

    def _writable_descriptor(self) -> int:
        """Disallow new evidence after close or a failed write."""
        if self._fd is None or self._failed:
            message = "Trace writer is closed or failed; verified recovery is required."
            raise RuntimeError(message)
        return self._fd

    def close(self) -> None:
        """Release the exclusive writer reference without deleting any evidence."""
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def __enter__(self) -> Self:
        """Return this live writer."""
        return self

    def __exit__(
        self,
        _kind: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close even when an experiment operation fails."""
        self.close()


def _trace_root(root: Path | None) -> Path:
    """Keep custom trusted-state roots outside every generated-script allowlist."""
    if root is None:
        return private_state_root()
    if root.is_symlink():
        message = "Trace root cannot be a symbolic link."
        raise ValueError(message)
    resolved = root.expanduser().resolve()
    exposed = (*read_only_paths(), output_dir_path())
    if any(resolved.is_relative_to(path.resolve()) for path in exposed):
        message = "Trace root must be outside sandbox-readable and output directories."
        raise ValueError(message)
    resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_private_directory(resolved)
    return resolved


def _check_private_directory(path: Path) -> None:
    """Require owner-only storage on supported Linux/WSL runtimes."""
    if os.name != "posix":
        return
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        message = "Trace state must be owned by this user with permissions 0700."
        raise PermissionError(message)


def _open_writer(path: Path, *, create: bool) -> int:
    """Use no-follow append descriptors and refuse a second concurrent writer."""
    if fcntl is None:
        message = "Trusted trace writers require Linux/WSL2 file locking."
        raise RuntimeError(message)
    flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _write_all(descriptor: int, data: bytes) -> None:
    """Handle short writes without accepting a partial record as durable."""
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            message = "Trace write made no progress."
            raise OSError(message)
        offset += written


def _finish_partial_line(store: TraceStore) -> None:
    """Separate an interrupted final record before append-only recovery evidence."""
    if not store.path.stat().st_size:
        return
    with store.path.open("rb") as stream:
        stream.seek(-1, os.SEEK_END)
        trailing = stream.read(1)
    if trailing != b"\n":
        store._append_bytes(b"\n")  # noqa: SLF001


def load_trace(path: Path) -> TraceRead:
    """Read bounded complete records, retaining corruption as explicit invalid evidence."""
    if path.stat().st_size > MAX_TRACE_BYTES:
        message = "Trace file exceeds the supported read limit."
        raise TraceLimitError(message)
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            _read_record(line, line_number, events, errors)
    return TraceRead(tuple(events), not errors, tuple(errors))


def _read_record(
    line: bytes, line_number: int, events: list[dict[str, Any]], errors: list[str]
) -> None:
    """Keep valid recovery events following a preserved interrupted JSON record."""
    try:
        event = json.loads(line)
    except (UnicodeError, ValueError):
        errors.append(f"invalid_json:{line_number}")
        return
    try:
        _validate_event(event)
        _validate_event_order(event, events)
    except (KeyError, TypeError, ValueError):
        errors.append(f"invalid_event:{line_number}")
        return
    if not line.endswith(b"\n"):
        errors.append(f"unterminated_record:{line_number}")
    events.append(event)


def _validate_event_order(event: dict[str, Any], events: list[dict[str, Any]]) -> None:
    """Reject duplicated, skipped, or mixed-run event sequences."""
    if event["sequence"] != len(events) + 1:
        message = "Trace event sequence is not contiguous."
        raise ValueError(message)
    if events and event["run_id"] != events[0]["run_id"]:
        message = "Trace contains mixed run identities."
        raise ValueError(message)


def _require_resumable(read: TraceRead, run_id: str) -> None:
    """Allow interrupted JSON bytes, but never ambiguous run or sequence histories."""
    if any(error.startswith("invalid_event:") for error in read.errors):
        message = "Invalid event identity or ordering prevents trace recovery."
        raise ValueError(message)
    if read.events and read.events[0]["run_id"] != run_id:
        message = "Trace run identity does not match the requested recovery."
        raise ValueError(message)


def _last_world(read: TraceRead) -> str:
    """Use the last known generation when recording infrastructure recovery."""
    return str(read.events[-1]["world_generation"]) if read.events else "unknown"


class RecordedDecisionReplay:
    """Replay provider responses only against identical recorded decision evidence."""

    def __init__(self, read: TraceRead) -> None:
        """Reject corrupt traces and retain only explicitly recorded decisions."""
        if not read.complete:
            message = "Recorded replay requires a complete trace."
            raise ValueError(message)
        self._events = tuple(event for event in read.events if event["kind"] == "decision_received")

    def decision_for(
        self,
        *,
        world_generation: str,
        frame: int,
        actor_id: int,
        observation: object,
        candidates: object,
    ) -> dict[str, Any]:
        """Fail closed when scene, candidate, actor, generation, or frame differs."""
        identity = (
            world_generation,
            frame,
            actor_id,
            identity_digest(observation),
            identity_digest(candidates),
        )
        return _matching_recorded_decision(self._events, identity)


def _matching_recorded_decision(
    events: tuple[dict[str, Any], ...], identity: tuple[object, ...]
) -> dict[str, Any]:
    matches = [event for event in events if _decision_identity(event) == identity]
    if len(matches) != 1:
        message = "Recorded-response replay identity mismatch or ambiguous decision."
        raise ValueError(message)
    decision = matches[0]["data"].get("decision", matches[0]["data"])
    if not isinstance(decision, dict):
        message = "Recorded response has no decision object."
        raise TypeError(message)
    return json.loads(_canonical_json(decision))


def _decision_identity(event: dict[str, Any]) -> tuple[object, ...]:
    data = event["data"]
    return (
        event["world_generation"],
        event["frame"],
        event["actor_id"],
        data.get("observation_id"),
        data.get("candidates_id", data.get("candidate_set_id")),
    )
