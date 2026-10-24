"""Persistent sessions retain settings until close and keep failed restores quarantined."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import persistent_runner, sandbox_session_process
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.ownership import OWNERSHIP_FILENAME
from carla_agentic_toolkit.sandbox_session_monitor import SessionMonitor, SessionState
from carla_agentic_toolkit.sandbox_session_process import SessionProcess
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
from carla_agentic_toolkit.session_protocol import SessionConfig
from carla_agentic_toolkit.simulator_lease import RecoveryRequiredError, SimulatorLease
from tests.test_sync_settings import FakeWorld

if TYPE_CHECKING:
    import subprocess
    from collections.abc import Iterator

    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.sandbox import ScriptOutcome

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Simulator leases require Linux")


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SessionProcess]:
    """Acquire a real lease without launching a child process or native CARLA client."""
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(sandbox_session_process, "_sandbox_runner", lambda: Path("runner"))
    monkeypatch.setattr(
        sandbox_session_process,
        "_runner_command",
        lambda _request: ["runner", "--module", "carla_agentic_toolkit.script_runner"],
    )
    monkeypatch.setattr(
        sandbox_session_process.subprocess,
        "Popen",
        lambda command, **_kwargs: cast(
            "subprocess.Popen[str]", SimpleNamespace(args=command, returncode=0)
        ),
    )
    instance = SessionProcess("settings-session", SessionConfig(port=29871))
    yield instance
    instance.lease.__exit__(None, None, None)


def test_session_initializes_settings_recovery_before_starting_worker(
    owner: SessionProcess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker cannot mutate settings until their recovery file is durably advertised."""

    def launch(command: list[str], **_kwargs: object) -> subprocess.Popen[str]:
        recovery = owner.lease.recovery_state
        assert "settings_path" in recovery
        settings_path = Path(cast("str", recovery["settings_path"]))
        assert settings_path.parent == owner.work
        assert settings_path.is_file()
        return cast("subprocess.Popen[str]", SimpleNamespace(args=command, returncode=0))

    monkeypatch.setattr(sandbox_session_process.subprocess, "Popen", launch)

    owner.start()

    assert owner.process is not None


def test_session_close_keeps_lease_dirty_when_settings_restore_is_unverified(
    owner: SessionProcess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful actor cleanup alone cannot admit the next simulator mutator."""
    report = {"failures": [], "settings_restored": False}
    monkeypatch.setattr(
        sandbox_session_process, "cleanup_script_ownership", lambda **_kwargs: report
    )
    owner.start()

    outcome = owner.finish('{"ok": true}', "")

    assert outcome.cleanup == report
    assert owner.root.exists()
    with (
        pytest.raises(RecoveryRequiredError) as caught,
        SimulatorLease(owner.config.host, owner.config.port),
    ):
        pass
    assert caught.value.state["cleanup"] == report


def test_session_close_releases_lease_after_verified_settings_restore(
    owner: SessionProcess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verified settings and actor cleanup allow another session to acquire ownership."""
    report = {"failures": [], "settings_restored": True}
    monkeypatch.setattr(
        sandbox_session_process, "cleanup_script_ownership", lambda **_kwargs: report
    )
    owner.start()

    outcome = owner.finish('{"ok": true}', "")

    assert outcome.cleanup == report
    assert not owner.root.exists()
    with SimulatorLease(owner.config.host, owner.config.port):
        pass


def test_session_close_reports_failed_settings_restoration(owner: SessionProcess) -> None:
    """A requested close must report restoration failure even with no actor failures."""
    state = SessionState(owner.session_id, owner.config, owner, error_type="session_closed")

    SessionMonitor().complete(
        state, {"ok": True, "cleanup": {"failures": [], "settings_restored": False}}
    )

    assert state.snapshot()["ok"] is False
    assert state.error_type == "session_cleanup_failed"


def _assert_retained_settings(world: FakeWorld, journal: RunSettings) -> None:
    assert world.settings.synchronous_mode is True
    assert journal.pending()


def _exercise_settings_requests(
    loop: persistent_runner.SessionLoop, world: FakeWorld, journal: RunSettings
) -> None:
    first = loop.namespace.execute(
        "result = api.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)"
    )
    assert first["ok"] is True
    _assert_retained_settings(world, journal)
    second = loop.namespace.execute("result = api.get_world_state()")
    assert second["ok"] is True
    _assert_retained_settings(world, journal)
    message = "End the test worker without closing its retained session state."
    raise RuntimeError(message)


def _assert_session_restored(
    owner: SessionProcess, closed: ScriptOutcome, world: FakeWorld
) -> None:
    assert closed.cleanup is not None
    assert closed.cleanup["settings_restored"] is True
    assert world.settings.synchronous_mode is False
    with SimulatorLease(owner.config.host, owner.config.port):
        pass


def test_worker_keeps_sync_across_requests_and_restores_settings_on_session_close(
    owner: SessionProcess,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child shares a durable journal across calls; only its parent restores on close."""
    owner.start()
    path = owner.work / SETTINGS_FILENAME
    journal = RunSettings(path, require_existing=True)
    ownership_path = owner.work / OWNERSHIP_FILENAME
    config_path = owner.work / "session.json"
    args = SimpleNamespace(
        script=config_path,
        ownership_file=ownership_path,
        host=owner.config.host,
        port=owner.config.port,
        timeout_seconds=5.0,
    )
    monkeypatch.setattr(persistent_runner, "_parse_args", lambda: args)
    world = FakeWorld()
    adapters: list[PythonCarlaAdapter] = []

    def connect(**values: object) -> PythonCarlaAdapter:
        settings = values.get("settings_journal")
        assert isinstance(settings, RunSettings)
        adapter = PythonCarlaAdapter(settings_journal=settings)
        adapter._connected_client = cast(  # noqa: SLF001
            "CarlaClient",
            SimpleNamespace(
                get_world=lambda: world,
                get_client_version=lambda: "0.9.16-client",
                get_server_version=lambda: "0.9.16-server",
            ),
        )
        adapters.append(adapter)
        return adapter

    def run(loop: persistent_runner.SessionLoop) -> None:
        _exercise_settings_requests(loop, world, journal)

    monkeypatch.setattr(persistent_runner, "PythonCarlaAdapter", connect)
    monkeypatch.setattr(persistent_runner.SessionLoop, "run", run)

    persistent_runner.main()

    _assert_retained_settings(world, journal)
    monkeypatch.setattr(
        sandbox_session_process,
        "cleanup_script_ownership",
        lambda **_kwargs: journal.restore(adapters[0]),
    )
    closed = owner.finish('{"ok": true}', "")
    _assert_session_restored(owner, closed, world)
