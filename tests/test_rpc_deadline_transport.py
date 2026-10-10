"""Parent-issued monotonic deadlines survive worker startup and request pickup delays."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

import carla_agentic_toolkit.script_runner as runner
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.persistent_runner import SessionLoop
from carla_agentic_toolkit.rpc_timeouts import (
    NORMAL_RPC_SECONDS,
    RUN_DEADLINE_FILENAME,
    RpcTimeoutPolicy,
)
from carla_agentic_toolkit.sandbox_session_monitor import SessionState
from carla_agentic_toolkit.sandbox_sessions import SandboxSessionManager
from carla_agentic_toolkit.session_protocol import (
    PROTOCOL_VERSION,
    ProtocolError,
    SessionConfig,
    read_message,
    write_message,
)

PUBLISH_TIME = 80.0
REQUEST_DEADLINE = 110.0
ABSOLUTE_DEADLINE = 100.0
REMAINING_SECONDS = 5.0

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.persistent_namespace import PersistentNamespace
    from carla_agentic_toolkit.persistent_runner import TelemetryApi
    from carla_agentic_toolkit.sandbox_session_process import SessionProcess


def test_finite_worker_uses_rpc_cap_not_script_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 300-second user execution budget starts with a ten-second native RPC cap."""
    script = tmp_path / "script.py"
    script.write_text("result = True\n", encoding="utf-8")
    arguments: dict[str, object] = {}

    def make_adapter(**kwargs: object) -> PythonCarlaAdapter:
        arguments.update(kwargs)
        return PythonCarlaAdapter()

    monkeypatch.setattr(runner, "PythonCarlaAdapter", make_adapter)

    outcome = runner.run_script_file(
        script_path=script, host="127.0.0.1", port=2000, timeout_seconds=300.0
    )

    assert outcome["ok"] is True
    assert arguments["timeout"] == NORMAL_RPC_SECONDS


def test_published_request_preserves_parent_request_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delayed pickup cannot start a fresh thirty-second execution window."""
    config = SessionConfig("127.0.0.1", 2000, request_timeout_seconds=30.0)
    owner = cast("SessionProcess", SimpleNamespace(work=tmp_path))
    session = SessionState("test-session", config, owner, started=50.0, state="ready")
    manager = SandboxSessionManager()
    monkeypatch.setattr("carla_agentic_toolkit.sandbox_sessions.time.monotonic", lambda: 80.0)

    manager._publish_request(  # noqa: SLF001
        session,
        {"action": "execute", "code": "result = True", "request_deadline_monotonic": 9999.0},
    )
    message = read_message(tmp_path / "request.json")

    assert message["request_deadline_monotonic"] == REQUEST_DEADLINE
    assert session.pending_since == PUBLISH_TIME


def test_delayed_request_pickup_uses_publish_deadline_and_keeps_absolute_budget(
    tmp_path: Path,
) -> None:
    """Each request narrows the original lifetime, even near absolute exhaustion."""
    now = [90.0]
    policy = RpcTimeoutPolicy(absolute_deadline=100.0, clock=lambda: now[0])
    loop = SessionLoop(
        tmp_path,
        "test-session",
        cast("TelemetryApi", SimpleNamespace()),
        RunOwnership(tmp_path / "owned-actors.json"),
        rpc_timeout_policy=policy,
        require_deadlines=True,
    )
    seen: list[float] = []

    def execute(_code: str) -> dict[str, object]:
        seen.append(policy.timeout_seconds())
        return {"ok": True}

    loop.namespace = cast("PersistentNamespace", SimpleNamespace(execute=execute))
    _execute(loop, 1, 92.0)
    assert (seen, policy.request_deadline, policy.absolute_deadline) == ([2.0], None, 100.0)
    now[0] = 95.0
    _execute(loop, 2, 125.0)
    assert seen == [2.0, 5.0]


def test_published_request_is_capped_by_parent_absolute_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request accepted near shutdown carries only the original remaining session budget."""
    config = SessionConfig(absolute_timeout_seconds=60.0, request_timeout_seconds=30.0)
    owner = cast("SessionProcess", SimpleNamespace(work=tmp_path))
    session = SessionState("test-session", config, owner, started=50.0, state="ready")
    manager = SandboxSessionManager()
    monkeypatch.setattr("carla_agentic_toolkit.sandbox_sessions.time.monotonic", lambda: 108.0)

    manager._publish_request(session, {"action": "execute", "code": "result = True"})  # noqa: SLF001

    assert read_message(tmp_path / "request.json")["request_deadline_monotonic"] == REQUEST_DEADLINE


def test_request_failure_clears_only_request_deadline(tmp_path: Path) -> None:
    """Cleanup of request state must not reset the remaining session lifetime."""
    policy = RpcTimeoutPolicy(absolute_deadline=100.0, clock=lambda: 90.0)
    loop = SessionLoop(
        tmp_path,
        "test-session",
        cast("TelemetryApi", SimpleNamespace()),
        RunOwnership(tmp_path / "owned-actors.json"),
        rpc_timeout_policy=policy,
    )

    def fail(_code: str) -> dict[str, object]:
        message = "namespace failed"
        raise RuntimeError(message)

    loop.namespace = cast("PersistentNamespace", SimpleNamespace(execute=fail))
    with pytest.raises(RuntimeError, match="namespace failed"):
        _execute(loop, 1, 92.0)

    assert (policy.request_deadline, policy.absolute_deadline) == (None, 100.0)


@pytest.mark.parametrize("deadline", [None, True, 0.0, -1.0, float("inf"), "later"])
def test_worker_rejects_malformed_required_request_deadline(
    tmp_path: Path, deadline: object
) -> None:
    """The trusted worker cannot replace absent or invalid parent evidence with fresh time."""
    loop = SessionLoop(
        tmp_path,
        "test-session",
        cast("TelemetryApi", SimpleNamespace()),
        RunOwnership(tmp_path / "owned-actors.json"),
        rpc_timeout_policy=RpcTimeoutPolicy(absolute_deadline=100.0, clock=lambda: 90.0),
        require_deadlines=True,
    )

    with pytest.raises(ProtocolError, match="deadline"):
        _execute(loop, 1, deadline)


def test_expired_published_request_does_not_enter_namespace(tmp_path: Path) -> None:
    """Delayed pickup of an already expired request cannot begin native mutations."""
    policy = RpcTimeoutPolicy(absolute_deadline=100.0, clock=lambda: 90.0)
    loop = SessionLoop(
        tmp_path,
        "test-session",
        cast("TelemetryApi", SimpleNamespace()),
        RunOwnership(tmp_path / "owned-actors.json"),
        rpc_timeout_policy=policy,
        require_deadlines=True,
    )
    with pytest.raises(CarlaAdapterError, match="deadline expired"):
        _execute(loop, 1, 89.0)
    assert policy.request_deadline is None


def _execute(loop: SessionLoop, sequence: int, deadline: object) -> None:
    loop._request(  # noqa: SLF001
        {
            "version": PROTOCOL_VERSION,
            "session_id": "test-session",
            "sequence": sequence,
            "action": "execute",
            "code": "result = True",
            "request_deadline_monotonic": deadline,
        }
    )


@pytest.mark.parametrize("deadline", [None, True, 0.0, -1.0, float("inf"), "later"])
def test_finite_required_deadline_fails_closed_before_adapter_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deadline: object
) -> None:
    """A parent-launched script cannot invent a fresh budget from bad deadline metadata."""
    script = tmp_path / "script.py"
    script.write_text("result = True\n", encoding="utf-8")
    if deadline is not None:
        # The protocol encoder rejects infinity before it can reach the worker.
        (tmp_path / RUN_DEADLINE_FILENAME).write_text(
            json.dumps({"absolute_deadline_monotonic": deadline}), encoding="utf-8"
        )
    created: list[bool] = []
    monkeypatch.setattr(runner, "PythonCarlaAdapter", lambda **_kwargs: created.append(True))

    outcome = runner.run_script_file(
        script_path=script,
        host="127.0.0.1",
        port=2000,
        timeout_seconds=300.0,
        require_rpc_deadline=True,
    )

    assert (outcome["ok"], outcome["error_type"], created) == (
        False,
        "execution_deadline_invalid",
        [],
    )


def test_finite_worker_keeps_parent_deadline_after_startup_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The launcher deadline, not script pickup, defines the remaining lifetime."""
    script = tmp_path / "script.py"
    script.write_text("result = True\n", encoding="utf-8")
    write_message(tmp_path / RUN_DEADLINE_FILENAME, {"absolute_deadline_monotonic": 100.0})
    monkeypatch.setattr("carla_agentic_toolkit.rpc_timeouts.time.monotonic", lambda: 95.0)
    policies: list[RpcTimeoutPolicy] = []

    def make_adapter(**kwargs: object) -> PythonCarlaAdapter:
        policies.append(cast("RpcTimeoutPolicy", kwargs["rpc_timeout_policy"]))
        return PythonCarlaAdapter()

    monkeypatch.setattr(runner, "PythonCarlaAdapter", make_adapter)
    outcome = runner.run_script_file(
        script_path=script,
        host="127.0.0.1",
        port=2000,
        timeout_seconds=300.0,
        require_rpc_deadline=True,
    )

    assert outcome["ok"] is True
    assert policies[0].absolute_deadline == ABSOLUTE_DEADLINE
    assert policies[0].timeout_seconds() == REMAINING_SECONDS


@pytest.mark.skipif(sys.platform != "linux", reason="Sandbox file metadata is enforced on Linux")
@pytest.mark.parametrize("kind", ["symlink", "oversized"])
def test_required_deadline_transport_rejects_unsafe_file(tmp_path: Path, kind: str) -> None:
    """Required budget metadata uses the same bounded no-symlink reader as session IPC."""
    script = tmp_path / "script.py"
    script.write_text("result = True\n", encoding="utf-8")
    path = tmp_path / RUN_DEADLINE_FILENAME
    if kind == "symlink":
        target = tmp_path / "target.json"
        write_message(target, {"absolute_deadline_monotonic": 100.0})
        path.symlink_to(target)
    else:
        path.write_bytes(b" " * (1024 * 1024 + 1))

    outcome = runner.run_script_file(
        script_path=script,
        host="127.0.0.1",
        port=2000,
        timeout_seconds=300.0,
        require_rpc_deadline=True,
    )

    assert (outcome["ok"], outcome["error_type"]) == (False, "execution_deadline_invalid")
