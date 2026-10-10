"""The parent verifies durable settings evidence before releasing a script lease."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit import sandbox, script_recovery
from carla_agentic_toolkit.ownership import OWNERSHIP_FILENAME, RunOwnership
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_settings_recovery import SettingsWorld, _adapter

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaWorld

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Simulator leases require Linux")


@pytest.mark.parametrize("script_ok", [True, False])
def test_parent_restores_pending_settings_after_success_or_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, script_ok: bool
) -> None:
    """An uncompleted child restore requires trusted cleanup, including settings-only timeouts."""
    from typing import cast  # noqa: PLC0415

    runner = tmp_path / "runner"
    runner.touch()
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "output"))
    world = SettingsWorld()
    calls: list[bool] = []

    def run(command: list[str], **_kwargs: object) -> sandbox.ScriptOutcome:
        work = Path(command[command.index("--work-dir") + 1])
        path = work / SETTINGS_FILENAME
        assert path.is_file(), "Settings evidence must exist before the child starts."
        settings = RunSettings(path, require_existing=True)
        settings.capture_world(cast("CarlaWorld", world))
        world.settings.update(synchronous_mode=True, fixed_delta_seconds=0.05)
        return sandbox.ScriptOutcome(
            ok=script_ok,
            result=1,
            stdout="",
            error_type=None if script_ok else "script_timeout",
        )

    def cleanup(
        _host: str,
        _port: int,
        path: Path,
        _descriptor: int,
        _timeout: float,
        *,
        require_settings: bool = False,
        destroy_actors: bool = True,
    ) -> dict[str, object]:
        assert require_settings is True
        calls.append(destroy_actors)
        return script_recovery._cleanup_connected(  # noqa: SLF001
            _adapter(world),
            RunOwnership(path),
            RunSettings(path.with_name(SETTINGS_FILENAME), require_existing=True),
            destroy_actors=destroy_actors,
        )

    monkeypatch.setattr(sandbox, "_run_sandbox", run)
    monkeypatch.setattr(sandbox, "cleanup_script_ownership", cleanup)
    outcome = sandbox.execute_script("result = 1", port=3000)
    assert world.settings["synchronous_mode"] is False
    _assert_parent_outcome(outcome, script_ok=script_ok)
    assert calls == [not script_ok]
    with SimulatorLease("localhost", 3000) as lease:
        assert lease.recovery_state == {}


def _assert_parent_outcome(outcome: sandbox.ScriptOutcome, *, script_ok: bool) -> None:
    assert outcome.cleanup is not None
    assert outcome.cleanup["settings_restored"] is True
    assert outcome.error_type == (None if script_ok else "script_timeout")


def test_parent_rejects_missing_journal_despite_child_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child report cannot substitute for intact durable restoration evidence."""
    runner = tmp_path / "runner"
    runner.touch()
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "output"))

    def run(command: list[str], **_kwargs: object) -> sandbox.ScriptOutcome:
        work = Path(command[command.index("--work-dir") + 1])
        (work / SETTINGS_FILENAME).unlink(missing_ok=True)
        return sandbox.ScriptOutcome(
            ok=True, result=1, stdout="", cleanup={"settings_restored": True, "failures": []}
        )

    monkeypatch.setattr(sandbox, "_run_sandbox", run)
    outcome = sandbox.execute_script("result = 1", port=3000)
    assert outcome.ok is False
    with SimulatorLease("localhost", 3000, recovering=True) as lease:
        assert lease.recovery_state["settings_path"]
        assert Path(str(lease.recovery_state["ownership_path"])).name == OWNERSHIP_FILENAME
