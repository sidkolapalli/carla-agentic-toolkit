"""Ownership and lifecycle failures retain truthful state without leaking acquired leases."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Self, cast

import pytest

from carla_agentic_toolkit import sandbox_session_process
from carla_agentic_toolkit.sandbox import ScriptOutcome
from carla_agentic_toolkit.sandbox_session_monitor import SessionMonitor, SessionState
from carla_agentic_toolkit.sandbox_sessions import SandboxSessionManager
from carla_agentic_toolkit.session_protocol import SessionConfig


class Lease:
    """Observable lease whose disposition does not depend on platform locking."""

    active = False

    def __enter__(self) -> Self:
        """Record acquisition."""
        self.active = True
        return self

    def __exit__(self, *_args: object) -> None:
        """Record release."""
        self.active = False


def test_session_setup_failure_does_not_leak_simulator_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unavailable private work directory cannot strand a kernel lease."""
    lease = Lease()
    monkeypatch.setattr(sandbox_session_process, "SimulatorLease", lambda *_args: lease)

    def fail_directory(**_kwargs: object) -> None:
        message = "disk unavailable"
        raise OSError(message)

    monkeypatch.setattr(sandbox_session_process.tempfile, "mkdtemp", fail_directory)
    with pytest.raises(OSError, match="disk unavailable"):
        sandbox_session_process.SessionProcess("test", sandbox_session_process.SessionConfig())
    assert lease.active is False


def test_disconnected_manager_cannot_admit_a_late_open() -> None:
    """An open racing client disconnect cannot create an untracked surviving worker."""
    manager = SandboxSessionManager()
    manager.close_all()
    assert manager.open({})["error_type"] == "session_manager_closed"


def test_monitor_pipe_error_waits_for_child_exit_before_terminal_cleanup() -> None:
    """Broken wrapper pipes must not bypass cancellation, process death, or final cleanup."""
    events = []

    def fail_pipe(**_kwargs: object) -> None:
        message = "wrapper pipe closed"
        raise OSError(message)

    process = SimpleNamespace(communicate=fail_pipe, wait=lambda: events.append("exited"))
    owner = SimpleNamespace(
        process=process,
        cancel=lambda: events.append("cancelled"),
        finish=lambda *_args: ScriptOutcome(ok=False, result=None, stdout=""),
    )
    session = SessionState(
        "test", SessionConfig(), cast("sandbox_session_process.SessionProcess", owner)
    )
    SessionMonitor().run(session)
    assert events == ["cancelled", "exited"]
    assert session.snapshot()["active"] is False
    assert session.error_type == "session_monitor_error"
