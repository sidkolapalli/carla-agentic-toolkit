"""Finite scripts participate in the same endpoint lease as managed workers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit import sandbox
from carla_agentic_toolkit.ownership import OWNERSHIP_FILENAME, RunOwnership
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from collections.abc import Sequence


def test_script_rejects_an_owned_endpoint_before_spawning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different interfaces cannot race mutations against the same CARLA server."""
    runner = tmp_path / "runner"
    runner.touch()
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))
    with SimulatorLease("localhost", 3000):
        result = sandbox.execute_script("result = 1", host="127.0.0.1", port=3000)
    assert result.error_type == "simulator_busy"


def test_script_rejects_unverified_crash_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An available kernel lock is not evidence that the simulator was cleaned."""
    runner = tmp_path / "runner"
    runner.touch()
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))
    with SimulatorLease("localhost", 3000) as lease:
        lease.mark_dirty({"kind": "managed", "actor_ids": [3]})
    result = sandbox.execute_script("result = 1", host="localhost", port=3000)
    assert result.error_type == "simulator_recovery_required"


@pytest.mark.parametrize("cleanup_failed", [True, False])
def test_finite_script_retains_failed_cleanup_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    cleanup_failed: bool,
) -> None:
    """Recovery evidence survives failure; verified clean runs remove temporary source."""
    runner = tmp_path / "runner"
    runner.touch()
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "output"))
    journals: list[Path] = []

    def run(command: Sequence[str], **_kwargs: object) -> sandbox.ScriptOutcome:
        work = Path(command[command.index("--work-dir") + 1])
        journal = work / OWNERSHIP_FILENAME
        RunOwnership(journal).add((123,))
        journals.append(journal)
        return sandbox.ScriptOutcome(ok=False, result=None, stdout="", error_type="script_timeout")

    def cleanup(outcome: sandbox.ScriptOutcome, **_kwargs: object) -> sandbox.ScriptOutcome:
        del outcome
        return sandbox.ScriptOutcome(
            ok=False,
            result=None,
            stdout="",
            error_type="script_timeout",
            cleanup={
                "failures": [{"actor_id": 123, "error": "unreachable"}] if cleanup_failed else []
            },
        )

    monkeypatch.setattr(sandbox, "_run_sandbox", run)
    monkeypatch.setattr(sandbox, "_cleanup_failed_execution", cleanup)
    outcome = sandbox.execute_script("result = 1", port=3000)
    assert outcome.error_type == "script_timeout"
    assert journals[0].exists() is cleanup_failed
    with SimulatorLease("localhost", 3000, recovering=True) as lease:
        _assert_recovery_state(lease, journals[0], cleanup_failed=cleanup_failed)


def _assert_recovery_state(lease: SimulatorLease, journal: Path, *, cleanup_failed: bool) -> None:
    if cleanup_failed:
        assert lease.recovery_state["ownership_path"] == str(journal)
        assert RunOwnership(journal).actor_ids() == (123,)
    else:
        assert lease.recovery_state == {}
