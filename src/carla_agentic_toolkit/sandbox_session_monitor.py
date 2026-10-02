"""Observe one sandbox worker through its protocol, deadlines, cancellation, and cleanup."""

from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.session_protocol import (
    POLL_SECONDS,
    PROTOCOL_VERSION,
    ProtocolError,
    read_message,
)

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.sandbox_session_process import SessionProcess
    from carla_agentic_toolkit.session_protocol import SessionConfig


@dataclass
class SessionState:
    """One lock-protected lifecycle and bounded latest-message state."""

    session_id: str
    config: SessionConfig
    owner: SessionProcess
    started: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    sequence: int = 0
    pending_since: float | None = None
    latest_result: dict[str, object] | None = None
    telemetry: dict[str, object] | None = None
    telemetry_sequence: int = 0
    replaced_telemetry: int = 0
    state: str = "starting"
    error_type: str | None = None
    error: str | None = None
    cleanup: dict[str, object] = field(default_factory=dict)
    sandbox: dict[str, object] = field(default_factory=dict)
    ready: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def snapshot(self) -> dict[str, object]:
        """Return truthful activity until the process and cleanup have finished."""
        return {
            "ok": self.error_type is None,
            "session_id": self.session_id,
            "state": self.state,
            "active": not self.done.is_set(),
            "sequence": self.sequence,
            "pending": self.pending_since is not None,
            "latest_result": self.latest_result,
            "telemetry": self.telemetry,
            "replaced_telemetry_samples": self.replaced_telemetry,
            "error_type": self.error_type,
            "error": self.error,
            "cleanup": self.cleanup,
            "sandbox": self.sandbox,
        }


class SessionMonitor:
    """Supervise the process independently of the client registry and request API."""

    def cancel(self, session: SessionState, kind: str, message: str) -> None:
        """Request cancellation while preserving the independent Rust watchdog."""
        with session.lock:
            if session.done.is_set() or session.state == "closing":
                return
            session.state, session.error_type, session.error = "closing", kind, message
            try:
                session.owner.cancel()
            except OSError as exc:
                session.error_type, session.error = "session_cancellation_failed", str(exc)

    def run(self, session: SessionState) -> None:
        """Drain the wrapper while checking IPC and deadlines without blocking the client."""
        process = session.owner.process
        if process is None:
            return
        while True:
            try:
                stdout, stderr = process.communicate(timeout=POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                self._poll(session)
            except (OSError, ValueError) as exc:
                self.cancel(session, "session_monitor_error", str(exc))
                process.wait()
                stdout, stderr = "", str(exc)
                break
        self._finish(session, stdout, stderr)

    def _finish(self, session: SessionState, stdout: str, stderr: str) -> None:
        try:
            outcome = session.owner.finish(stdout, stderr)
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            session.error_type, session.error = "session_monitor_error", str(exc)
            outcome = session.owner.failed_start(str(exc))
        self.complete(session, outcome.to_dict())

    def _poll(self, session: SessionState) -> None:
        with session.lock:
            try:
                self._response(session)
                self._telemetry(session)
                self._deadlines(session)
            except (OSError, ValueError, TypeError, RecursionError) as exc:
                self.cancel(session, "session_protocol_error", str(exc))

    def _response(self, session: SessionState) -> None:
        path = session.owner.work / "response.json"
        if not path.exists():
            return
        message = _message(path, session.session_id)
        path.unlink()
        if message.get("kind") == "ready" and session.state == "starting":
            session.state = "ready"
            session.last_activity = time.monotonic()
            session.ready.set()
            return
        self._result(session, message)

    def _result(self, session: SessionState, message: dict[str, object]) -> None:
        if message.get("kind") != "result" or message.get("sequence") != session.sequence:
            error = "Unexpected session response kind or sequence."
            raise ProtocolError(error)
        if session.pending_since is None:
            error = "Session returned a result without an outstanding request."
            raise ProtocolError(error)
        session.latest_result = _payload(message)
        session.pending_since = None

    def _telemetry(self, session: SessionState) -> None:
        path = session.owner.work / "telemetry.json"
        if not path.exists():
            return
        message = _message(path, session.session_id)
        sequence = message.get("sequence")
        if type(sequence) is not int or sequence < session.telemetry_sequence:
            error = "Telemetry sequence moved backwards."
            raise ProtocolError(error)
        if sequence != session.telemetry_sequence:
            session.replaced_telemetry += max(0, sequence - session.telemetry_sequence - 1)
            session.telemetry_sequence = sequence
            session.telemetry = _payload(message)

    def _deadlines(self, session: SessionState) -> None:
        now = time.monotonic()
        limits = ((session.started, session.config.absolute_timeout_seconds, "absolute"),)
        if session.state == "ready":
            limits += ((session.last_activity, session.config.idle_timeout_seconds, "idle"),)
        if session.pending_since is not None:
            limits += ((session.pending_since, session.config.request_timeout_seconds, "request"),)
        for start, limit, name in limits:
            if now - start >= limit:
                self.cancel(session, f"session_{name}_timeout", f"Session {name} deadline expired.")
                return

    def complete(self, session: SessionState, outcome: dict[str, object]) -> None:
        """Publish terminal state only after actor cleanup and lease disposition."""
        with session.lock:
            session.cleanup = cast("dict[str, object]", outcome.get("cleanup") or {})
            session.sandbox = cast("dict[str, object]", outcome.get("sandbox") or {})
            self._final_error(session, outcome)
            session.state = "closed"
            session.pending_since = None
            session.done.set()
            session.ready.set()

    def _final_error(self, session: SessionState, outcome: dict[str, object]) -> None:
        if session.cleanup.get("failures"):
            session.error_type, session.error = (
                "session_cleanup_failed",
                "Actor cleanup requires recovery.",
            )
        elif session.error_type == "session_closed":
            session.error_type, session.error = None, None
        elif session.error_type is None:
            session.error_type, session.error = _exit_error(outcome)


def _exit_error(outcome: dict[str, object]) -> tuple[str, str]:
    return (
        str(outcome.get("error_type") or "session_exited"),
        str(outcome.get("error") or "Session worker exited."),
    )


def _message(path: Path, session_id: str) -> dict[str, object]:
    message = read_message(path)
    if message.get("version") != PROTOCOL_VERSION or message.get("session_id") != session_id:
        error = "Session response identity mismatch."
        raise ProtocolError(error)
    return message


def _payload(message: dict[str, object]) -> dict[str, object]:
    value = message.get("payload")
    if not isinstance(value, dict):
        error = "Session response payload must be an object."
        raise ProtocolError(error)
    return cast("dict[str, object]", value)
