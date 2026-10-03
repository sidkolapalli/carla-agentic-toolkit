"""Per-client bounded persistent script sessions with one outstanding request per worker."""

from __future__ import annotations

import threading
import time
import uuid

from carla_agentic_toolkit.sandbox_session_monitor import SessionMonitor, SessionState
from carla_agentic_toolkit.sandbox_session_process import SessionProcess
from carla_agentic_toolkit.session_protocol import (
    PROTOCOL_VERSION,
    SessionConfig,
    validate_code,
    write_message,
)
from carla_agentic_toolkit.simulator_lease import LeaseBusyError, RecoveryRequiredError

MAX_RETAINED_SESSIONS = 32
STARTUP_TIMEOUT_SECONDS = 10.0
CLOSE_TIMEOUT_SECONDS = 10.0


class SandboxSessionManager:
    """One stdio client's unguessable session IDs; no globally addressable session registry."""

    def __init__(self) -> None:
        """Retain only a bounded set of local workers and their latest results."""
        self._sessions: dict[str, SessionState] = {}
        self._lock = threading.Lock()
        self._monitor = SessionMonitor()
        self._closed = False

    def open(self, config: dict[str, object]) -> dict[str, object]:
        """Validate and acquire the simulator before exposing a ready persistent namespace."""
        try:
            return self._open(SessionConfig.parse(config))
        except LeaseBusyError as exc:
            return _failure("simulator_busy", str(exc))
        except RecoveryRequiredError as exc:
            return _failure("simulator_recovery_required", str(exc))
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            return _failure("session_setup_error", str(exc))

    def _open(self, config: SessionConfig) -> dict[str, object]:
        with self._lock:
            if self._closed:
                return _failure("session_manager_closed", "The owning client disconnected.")
            if len(self._sessions) >= MAX_RETAINED_SESSIONS:
                return _failure("session_limit", "This client reached its retained-session limit.")
            session_id = uuid.uuid4().hex
            owner = SessionProcess(session_id, config)
            session = SessionState(session_id, config, owner)
            self._sessions[session_id] = session
        try:
            owner.start()
        except (OSError, ValueError, RuntimeError) as exc:
            outcome = owner.failed_start(str(exc))
            self._monitor.complete(session, outcome.to_dict())
            return session.snapshot()
        threading.Thread(target=self._monitor.run, args=(session,), daemon=True).start()
        if not session.ready.wait(STARTUP_TIMEOUT_SECONDS):
            self._monitor.cancel(session, "session_startup_timeout", "Session readiness timed out.")
            session.done.wait(CLOSE_TIMEOUT_SECONDS)
        with session.lock:
            return session.snapshot()

    def execute(self, session_id: str, code: str) -> dict[str, object]:
        """Submit one bounded request; completion is read without buffering an unbounded queue."""
        try:
            return self._submit(session_id, {"action": "execute", "code": validate_code(code)})
        except ValueError as exc:
            return _failure("invalid_request", str(exc))

    def telemetry(self, session_id: str, config: dict[str, object]) -> dict[str, object]:
        """Select only an explicitly owned actor and a bounded latest-only sampling interval."""
        if set(config) - {"actor_id", "interval_seconds"}:
            return _failure("invalid_request", "Unsupported telemetry configuration.")
        return self._submit(session_id, {**config, "action": "telemetry"})

    def _submit(self, session_id: str, request: dict[str, object]) -> dict[str, object]:
        session = self._get(session_id)
        if session is None:
            return _missing()
        with session.lock:
            if session.state != "ready":
                return _failure("session_closed", "Session is not accepting requests.")
            if session.pending_since is not None:
                return _failure(
                    "session_busy", "Wait for the outstanding request before submitting another."
                )
            return self._publish_request(session, request)

    def _publish_request(
        self, session: SessionState, request: dict[str, object]
    ) -> dict[str, object]:
        session.sequence += 1
        session.last_activity = time.monotonic()
        session.pending_since = session.last_activity
        session.latest_result = None
        try:
            write_message(
                session.owner.work / "request.json",
                {
                    "version": PROTOCOL_VERSION,
                    "session_id": session.session_id,
                    "sequence": session.sequence,
                    **request,
                },
            )
        except (OSError, ValueError) as exc:
            self._monitor.cancel(session, "session_protocol_error", str(exc))
        return session.snapshot()

    def read(self, session_id: str) -> dict[str, object]:
        """Read one current status/result/telemetry snapshot and count as client activity."""
        session = self._get(session_id)
        if session is None:
            return _missing()
        with session.lock:
            session.last_activity = time.monotonic()
            return session.snapshot()

    def cancel(self, session_id: str) -> dict[str, object]:
        """Force process-group cancellation while retaining truthful cleanup state."""
        return self._close(session_id, "session_cancelled", "Client cancelled the session.")

    def close(self, session_id: str) -> dict[str, object]:
        """Terminate the complete sandbox tree and clean every created actor."""
        return self._close(session_id, "session_closed", "Client closed the session.")

    def _close(self, session_id: str, kind: str, message: str) -> dict[str, object]:
        session = self._get(session_id)
        if session is None:
            return _missing()
        self._monitor.cancel(session, kind, message)
        session.done.wait(CLOSE_TIMEOUT_SECONDS)
        with session.lock:
            return session.snapshot()

    def close_all(self) -> None:
        """On client disconnect, cancel all trees first, then wait for bounded cleanup."""
        with self._lock:
            self._closed = True
            sessions = tuple(self._sessions.values())
        for session in sessions:
            self._monitor.cancel(session, "session_disconnected", "Client disconnected.")
        deadline = time.monotonic() + CLOSE_TIMEOUT_SECONDS
        for session in sessions:
            session.done.wait(max(0.0, deadline - time.monotonic()))

    def _get(self, session_id: str) -> SessionState | None:
        with self._lock:
            return self._sessions.get(session_id)


def _failure(kind: str, error: str) -> dict[str, object]:
    return {"ok": False, "active": False, "error_type": kind, "error": error}


def _missing() -> dict[str, object]:
    return _failure("session_not_found", "Session does not belong to this client.")
