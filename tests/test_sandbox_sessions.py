"""Real Linux sandbox sessions retain state and truthfully terminate on lifecycle failures."""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import sandbox_session_process
from carla_agentic_toolkit.sandbox_sessions import SandboxSessionManager
from carla_agentic_toolkit.session_protocol import write_message
from tests.sandbox_helpers import sandbox_runner_path

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


FIRST_RESULT = 7
SECOND_RESULT = 8

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Landlock requires Linux")


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SandboxSessionManager]:
    """Give each test private state and retain cleanup even when an assertion fails."""
    runner = sandbox_runner_path()
    if not runner.exists():
        pytest.skip("Rust sandbox runner is not built")
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "output"))
    instance = SandboxSessionManager()
    yield instance
    instance.close_all()


def _open(manager: SandboxSessionManager, **config: object) -> str:
    result = manager.open({"port": 29870, **config})
    assert result["ok"], result
    return cast("str", result["session_id"])


def _wait(
    manager: SandboxSessionManager, session_id: str, *, terminal: bool = False
) -> dict[str, object]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = manager.read(session_id)
        if (result["active"] is False) if terminal else (result.get("latest_result") is not None):
            return result
        time.sleep(0.02)
    pytest.fail(f"Session did not reach expected state: {result}")


def test_real_session_preserves_namespace_and_releases_lease(
    manager: SandboxSessionManager,
) -> None:
    """Follow-up code runs in one process; close releases the instance for another owner."""
    session_id = _open(manager)
    manager.execute(session_id, "vehicle = 7\nresult = vehicle")
    first = cast("dict[str, object]", _wait(manager, session_id)["latest_result"])
    assert first["result"] == FIRST_RESULT
    manager.execute(session_id, "result = vehicle + 1")
    second = cast("dict[str, object]", _wait(manager, session_id)["latest_result"])
    assert second["result"] == SECOND_RESULT
    closed = manager.close(session_id)
    assert (closed["active"], closed["ok"]) == (False, True)
    assert _open(manager) != session_id


def test_session_ids_are_private_to_each_manager(manager: SandboxSessionManager) -> None:
    """Knowing another client's ID does not confer access to its namespace or cancellation."""
    session_id = _open(manager)
    other = SandboxSessionManager()
    assert other.read(session_id)["error_type"] == "session_not_found"
    assert other.cancel(session_id)["error_type"] == "session_not_found"


def test_busy_request_backpressure_and_explicit_cancellation(
    manager: SandboxSessionManager,
) -> None:
    """Only one outstanding request is admitted; cancellation kills a running infinite script."""
    session_id = _open(manager)
    manager.execute(session_id, "while True:\n    pass")
    assert manager.execute(session_id, "result = 3")["error_type"] == "session_busy"
    closed = manager.cancel(session_id)
    assert closed["active"] is False
    assert closed["error_type"] == "session_cancelled"


@pytest.mark.parametrize("deadline", ["absolute_timeout_seconds", "request_timeout_seconds"])
def test_deadlines_terminate_active_script(manager: SandboxSessionManager, deadline: str) -> None:
    """Wall-clock caps remain effective when generated code never returns."""
    session_id = _open(manager, **{deadline: 3.0})
    manager.execute(session_id, "while True:\n    pass")
    result = _wait(manager, session_id, terminal=True)
    assert result["error_type"] in {"session_absolute_timeout", "session_request_timeout"}


def test_disconnect_cleanup_cancels_every_owned_worker(manager: SandboxSessionManager) -> None:
    """The MCP lifespan finalizer cannot leave a live worker or lease behind."""
    session_id = _open(manager)
    manager.execute(session_id, "while True:\n    pass")
    manager.close_all()
    assert manager.read(session_id)["active"] is False


def test_idle_deadline_is_not_refreshed_by_monitor_polling(manager: SandboxSessionManager) -> None:
    """Only client activity extends idle lifetime; the worker cannot keep itself alive."""
    session_id = _open(manager, idle_timeout_seconds=0.2)
    time.sleep(0.5)
    result = manager.read(session_id)
    assert result["active"] is False
    assert result["error_type"] == "session_idle_timeout"


def test_corrupt_child_response_terminates_session(
    manager: SandboxSessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Untrusted response identity must never be accepted as another client's result."""
    owners = []
    original = sandbox_session_process.SessionProcess.start

    def capture(owner: sandbox_session_process.SessionProcess) -> None:
        owners.append(owner)
        original(owner)

    monkeypatch.setattr(sandbox_session_process.SessionProcess, "start", capture)
    session_id = _open(manager)
    manager.execute(session_id, "while True:\n    pass")
    write_message(owners[0].work / "response.json", {"version": 1, "session_id": "other-client"})
    result = _wait(manager, session_id, terminal=True)
    assert result["error_type"] == "session_protocol_error"
    assert _open(manager) != session_id


def test_cleanup_failure_retains_recovery_barrier(
    manager: SandboxSessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminated child is insufficient to admit another owner when actor cleanup failed."""

    def failed_cleanup(**_kwargs: object) -> dict[str, object]:
        return {"failures": [{"actor_id": 7, "error": "RPC failure"}]}

    monkeypatch.setattr(
        sandbox_session_process, "cleanup_script_ownership", failed_cleanup, raising=False
    )
    session_id = _open(manager)
    result = manager.close(session_id)
    assert result["error_type"] == "session_cleanup_failed"
    assert manager.open({"port": 29870})["error_type"] == "simulator_recovery_required"


def test_monitor_cleanup_exception_still_reports_terminal_state(
    manager: SandboxSessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected cleanup error cannot strand a dead session in active status."""

    def fail_finish(*_args: object) -> None:
        message = "unexpected finalization error"
        raise RuntimeError(message)

    session_id = _open(manager)
    monkeypatch.setattr(sandbox_session_process.SessionProcess, "finish", fail_finish)
    result = manager.close(session_id)
    assert result["active"] is False
    assert result["error_type"] == "session_monitor_error"


def test_unwritable_cancel_marker_preserves_watchdog_and_terminal_truth(
    manager: SandboxSessionManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marker failure is explicit while the independent Rust deadline still reaps the tree."""

    def fail_cancel(*_args: object) -> None:
        message = "control directory unavailable"
        raise OSError(message)

    session_id = _open(manager, absolute_timeout_seconds=3.0)
    manager.execute(session_id, "while True:\n    pass")
    monkeypatch.setattr(sandbox_session_process.SessionProcess, "cancel", fail_cancel)
    result = manager.cancel(session_id)
    assert result["active"] is False
    assert result["error_type"] == "session_cancellation_failed"
