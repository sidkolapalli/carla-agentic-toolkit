"""A native client finalizer must not erase the cleanup worker's durable result."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import script_recovery
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_script_recovery import journal

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Native cleanup requires Linux")

CRASHING_CLIENT = """
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from carla_agentic_toolkit import adapter, ownership, script_recovery

job = Path(sys.argv[1])
script_recovery.tempfile.gettempdir = lambda: str(job.parent)

class World:
    id = 7
    def __init__(self):
        self.frame = 0
    def get_snapshot(self):
        return SimpleNamespace(frame=self.frame)
    def get_settings(self):
        return SimpleNamespace(synchronous_mode=False)
    def wait_for_tick(self, timeout):
        self.frame += 1
        return self.get_snapshot()

class NativeAdapter:
    def __init__(self, **kwargs):
        self.world = World()
    def _client(self):
        return SimpleNamespace(get_world=lambda: self.world)
    def __del__(self):
        os._exit(73)

def cleanup(adapter, record):
    if sys.argv[2] == 'failure':
        raise RuntimeError('injected destruction refusal')
    ids = record.actor_ids()
    record.discard(ids)
    return ownership.cleanup_report(ids, ids)

adapter.PythonCarlaAdapter = NativeAdapter
ownership.cleanup_owned_actors = cleanup
script_recovery.main()
"""


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_cleanup_publishes_before_native_client_finalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Fatal native teardown cannot discard either a success or a failure report."""
    path = journal(tmp_path, monkeypatch, {"schema_version": 1, "world_id": 7, "actor_ids": [17]})
    job = tmp_path / "cleanup"
    job.mkdir(mode=0o700)
    write_control(
        job / "request.json", {"host": "localhost", "port": 3000, "ownership_path": str(path)}
    )
    child = subprocess.run(  # noqa: S603 - fixed test program in a disposable child.
        [sys.executable, "-c", CRASHING_CLIENT, str(job), outcome],
        env={**os.environ, "CARLA_AGENTIC_TOOLKIT_SUPERVISOR_PID": str(os.getpid())},
        check=False,
        timeout=5,
        capture_output=True,
    )
    assert (child.returncode, (job / "result.json").exists()) == (0, True), child.stderr
    result = read_control(job / "result.json")
    assert bool(result["failures"]) == (outcome == "failure")
    assert json.loads(path.read_text())["actor_ids"] == ([17] if outcome == "failure" else [])


@pytest.mark.parametrize("exit_code", [0, 17, -11])
def test_missing_cleanup_report_identifies_worker_exit_and_retains_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    """Report a missing result distinctly from an abnormal exit, without exposing stderr."""
    path = journal(tmp_path, monkeypatch, {"schema_version": 1, "world_id": 7, "actor_ids": [17]})
    original = path.read_bytes()

    def fail(_job: Path, lease_descriptor: int) -> subprocess.Popen[bytes]:
        return subprocess.Popen(  # noqa: S603 - fixed isolated failure injection.
            [
                sys.executable,
                "-c",
                "import os, resource, sys; resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); "
                "sys.stderr.write('SENSITIVE_TEST_DIAGNOSTIC'); sys.stderr.flush(); "
                "code = int(sys.argv[1]); "
                "os.kill(os.getpid(), -code) if code < 0 else sys.exit(code)",
                str(exit_code),
            ],
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(lease_descriptor,),
        )

    monkeypatch.setattr(script_recovery, "_spawn_cleanup", fail)
    state = tmp_path / "state"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty({"kind": "script", "ownership_path": str(path)})
    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)
    assert (result["ok"], path.read_bytes()) == (False, original)
    cleanup = cast("dict[str, object]", result["cleanup"])
    assert cleanup["worker"] == {"exit_code": exit_code, "result_available": False}
    assert "SENSITIVE_TEST_DIAGNOSTIC" not in json.dumps(result)
    with SimulatorLease("localhost", 3000, state_root=state, recovering=True) as lease:
        assert lease.recovery_state
