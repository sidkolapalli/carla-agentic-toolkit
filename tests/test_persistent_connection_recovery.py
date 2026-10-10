"""A dead persistent worker cannot lose its unexpected-restart cleanup restriction."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import sandbox_session_process, script_recovery
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.managed_control_io import write_control
from carla_agentic_toolkit.ownership import OWNERSHIP_FILENAME, RunOwnership
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests import test_session_settings
from tests.test_persistent_reconnect import _world

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.sandbox_session_process import SessionProcess

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Real leases and SIGKILL use Linux")
CONNECTION_FILENAME = "persistent-connection.json"
RESTART = {"expected_world_id": 17, "observed_world_id": 18}
REAL_POPEN = subprocess.Popen
owner = test_session_settings.owner


def _record(*, restarted: bool = True) -> dict[str, object]:
    return {"schema_version": 1, "world_id": 17, "restart": RESTART if restarted else None}


def _recovery(owner: SessionProcess) -> dict[str, object]:
    return {
        "kind": "script_session",
        "ownership_path": str(owner.work / OWNERSHIP_FILENAME),
        "settings_path": str(owner.work / SETTINGS_FILENAME),
        "connection_path": str(owner.work / CONNECTION_FILENAME),
    }


def _old_cleanup(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Expose the real old-episode-clearing path without importing a native CARLA client."""
    adapter = PythonCarlaAdapter()
    client = Mock()
    client.get_world.return_value = _world(18)
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001

    def cleanup(request: dict[str, object], _descriptor: int, _timeout: float) -> dict[str, object]:
        path = Path(cast("str", request["ownership_path"]))
        return script_recovery._cleanup_connected(  # noqa: SLF001
            adapter, RunOwnership(path), RunSettings(path.with_name(SETTINGS_FILENAME))
        )

    bounded = Mock(side_effect=cleanup)
    monkeypatch.setattr(script_recovery, "_bounded_cleanup", bounded)
    return bounded


def test_connection_evidence_is_initialized_and_advertised_before_launch(
    owner: SessionProcess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish restrictive connection evidence before starting the sandbox worker."""

    def launch(command: list[str], **_kwargs: object) -> subprocess.Popen[str]:
        state = owner.lease.recovery_state
        path = Path(cast("str", state["connection_path"]))
        assert path == owner.work / CONNECTION_FILENAME
        assert json.loads(path.read_text()) == {
            "schema_version": 1,
            "world_id": None,
            "restart": None,
        }
        return cast("subprocess.Popen[str]", Mock(args=command, returncode=0))

    monkeypatch.setattr(sandbox_session_process.subprocess, "Popen", launch)
    owner.start()


def test_restarted_session_close_keeps_known_ids_and_empty_settings_dirty(
    owner: SessionProcess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse fresh cleanup after restart even without settings pending restoration."""
    owner.start()
    ownership = RunOwnership(owner.work / OWNERSHIP_FILENAME)
    ownership.add([11], world_id=17)
    write_control(owner.work / CONNECTION_FILENAME, _record())
    bounded = _old_cleanup(monkeypatch)

    outcome = owner.finish('{"ok": true}', "")

    _assert_cleanup_failed(outcome.cleanup)
    assert owner.root.exists()
    assert ownership.actor_ids() == (11,)
    assert not RunSettings(owner.work / SETTINGS_FILENAME).pending()
    bounded.assert_not_called()
    _assert_dirty(owner)


def _assert_cleanup_failed(cleanup: dict[str, object] | None) -> None:
    assert cleanup is not None
    assert cleanup["failures"]


def _assert_dirty(owner: SessionProcess) -> None:
    with SimulatorLease(owner.config.host, owner.config.port, recovering=True) as lease:
        assert lease.recovery_state


@pytest.mark.parametrize("damage", ["missing", "malformed", "large", "symlink", "path", "legacy"])
def test_required_connection_evidence_is_validated_before_offline_cleanup(
    owner: SessionProcess, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    """Validate required evidence before an empty-journal offline success shortcut."""
    owner.start()
    path = owner.work / CONNECTION_FILENAME
    write_control(path, _record(restarted=False))
    state = _recovery(owner)
    _damage_connection(path, state, damage)
    owner.lease.mark_dirty(state)
    owner.lease.__exit__(None, None, None)
    bounded = _old_cleanup(monkeypatch)

    result = script_recovery.recover_script_ownership(owner.config.host, owner.config.port)

    assert result["ok"] is False
    assert result["recovery_required"] is True
    bounded.assert_not_called()
    _assert_dirty(owner)


def _damage_connection(path: Path, state: dict[str, object], damage: str) -> None:
    if damage == "path":
        state["connection_path"] = str(path.with_name("other.json"))
    elif damage == "legacy":
        state.pop("connection_path")
    else:
        _damage_connection_file(path, damage)


def _damage_connection_file(path: Path, damage: str) -> None:
    if damage == "missing":
        path.unlink()
    elif damage == "malformed":
        path.write_text('{"schema_version":1,"world_id":false,"restart":null}')
    elif damage == "large":
        path.write_bytes(b" " * (script_recovery.MAX_JOURNAL_BYTES + 1))
    else:
        other = path.with_name("other.json")
        other.write_text(json.dumps(_record(restarted=False)))
        path.unlink()
        path.symlink_to(other)


def test_sigkill_worker_restart_record_is_read_by_fresh_trusted_recovery(
    owner: SessionProcess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recover the real worker's durable restriction after it is killed and reaped."""
    owner.start()
    RunOwnership(owner.work / OWNERSHIP_FILENAME).add([11], world_id=17)
    _kill_recording_worker(owner.work / CONNECTION_FILENAME)
    state_root = owner.lease._root  # noqa: SLF001 -- recover the exact fixture's lease.
    owner.lease.mark_dirty(_recovery(owner))
    owner.lease.__exit__(None, None, None)
    bounded = _old_cleanup(monkeypatch)
    result = script_recovery.recover_script_ownership(
        owner.config.host, owner.config.port, state_root=state_root
    )
    assert result["ok"] is False
    assert RunOwnership(owner.work / OWNERSHIP_FILENAME).actor_ids() == (11,)
    bounded.assert_not_called()
    _assert_dirty(owner)


def _kill_recording_worker(path: Path) -> None:
    child = REAL_POPEN(  # fixed fake worker, private durable evidence only.
        [sys.executable, "-c", WORKER_PROGRAM, str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _await_record(child)
        child.kill()
        child.communicate(timeout=2)
        assert child.returncode == -signal.SIGKILL
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=2)


def _await_record(child: subprocess.Popen[str]) -> None:
    import select  # noqa: PLC0415 -- the fixture is Linux-only.

    assert child.stdout is not None
    readable, _, _ = select.select([child.stdout], [], [], 10)
    assert readable, "Fake worker did not publish its durable restart record."
    marker = child.stdout.readline().strip()
    if marker != "recorded":
        _, stderr = child.communicate(timeout=2)
        pytest.fail(f"Fake worker failed before publishing its restart record: {stderr}")


WORKER_PROGRAM = """
import sys,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.persistent_namespace import PersistentNamespace
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_sync_settings import FakeSettings
world = Mock(id=17)
world.get_settings.return_value = FakeSettings()
world.get_map.return_value.name = 'Town10HD'
world.get_snapshot.return_value.frame = 12
world.get_actors.return_value.filter.return_value = []
client = Mock()
client.get_world.return_value = world
client.get_client_version.return_value = '0.9.16-client'
client.get_server_version.return_value = '0.9.16-server'
adapter = PythonCarlaAdapter(persistent_connection_path=Path(sys.argv[1]))
adapter._connected_client = client
namespace = PersistentNamespace(CarlaScriptApi(adapter, RunSnapshots()))
first = namespace.execute('result = api.get_world_state()')
assert first['ok'] is True, first
client.get_world.return_value = SimpleNamespace(id=18)
outcome = namespace.execute('result = api.get_world_state()')
assert outcome['error_type'] == 'simulator_restarted'
print('recorded', flush=True)
time.sleep(30)
"""
