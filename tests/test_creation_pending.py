"""Durable intent quarantines creation if the worker dies before journal completion."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter_objects, script_recovery
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import OwnershipError
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_incremental_actor_ownership import (
    FIRST_ACTOR_ID,
    WORLD_ID,
    CreationCase,
    CreationWorld,
    _spawn_request,
)
from tests.test_incremental_actor_ownership import (
    creation_case as creation_case,  # noqa: PLC0414 - re-export the shared pytest fixture.
)
from tests.test_script_recovery import journal

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.ownership import _Journal

CHILD_PROGRAM = "from tests.test_creation_pending import _child; import sys; _child(*sys.argv[1:])"
COMPLETED_ACTORS = 3
FINAL_MAP_CHECK = 2


def test_pending_write_failure_prevents_native_creation(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A creation RPC is never issued when durable intent cannot be written."""
    save = creation_case.ownership._save  # noqa: SLF001 - inject only the durable intent write failure.

    def fail(_journal: _Journal) -> None:
        if _journal.pending_creations:
            message = "intent unavailable"
            raise OwnershipError(message)
        save(_journal)

    monkeypatch.setattr(creation_case.ownership, "_save", fail)
    result = creation_case.api.spawn_actor_batch([_spawn_request()])
    assert result["ok"] is False
    assert creation_case.world.actors == []


def test_empty_pending_journal_cannot_authorize_trusted_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty actor list does not prove an interrupted creation RPC made no actor."""
    path = journal(tmp_path, monkeypatch, [])
    value = {"schema_version": 1, "world_id": WORLD_ID, "actor_ids": [], "pending_creations": 1}
    path.write_text(json.dumps(value))
    with pytest.raises((ValueError, OwnershipError), match="creation"):
        script_recovery._ownership(path).require_completed_creations()  # noqa: SLF001


def test_active_controller_prevents_map_mutation(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A map cannot race the controller's next creation intent."""
    state = Mock()
    state.to_dict.return_value = {"map_name": "Town02"}
    load = Mock(return_value=state)
    monkeypatch.setattr(creation_case.adapter, "load_world", load)
    monkeypatch.setattr(
        creation_case.api._traffic_controller,  # noqa: SLF001 - emulate an active controller worker.
        "get_status",
        lambda: SimpleNamespace(active=True, stopping=False),
    )
    creation_case.api.load_world("Town02")
    load.assert_not_called()


def test_map_completion_cannot_erase_intent_started_after_precheck(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final clear checks pending intent under the same journal lock as the write."""
    state = Mock()
    state.to_dict.return_value = {"map_name": "Town02", "settings": {"synchronous_mode": False}}
    monkeypatch.setattr(creation_case.adapter, "load_world", lambda _name, **_kwargs: state)
    require = creation_case.ownership.require_completed_creations
    calls = 0

    def begin_after_final_check() -> None:
        nonlocal calls
        require()
        calls += 1
        if calls == FINAL_MAP_CHECK:
            creation_case.ownership.begin_creation(WORLD_ID)

    monkeypatch.setattr(
        creation_case.ownership, "require_completed_creations", begin_after_final_check
    )
    creation_case.api.load_world("Town02")
    with pytest.raises(OwnershipError, match="creation"):
        RunOwnership(creation_case.journal_path).require_completed_creations()


def test_concurrent_success_cannot_clear_another_unresolved_spawn(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Controller and foreground RPCs can overlap without losing the earlier intent."""
    entered, release = threading.Event(), threading.Event()
    world = creation_case.world
    world.interrupt_after = 100
    native_spawn = world.spawn_actor

    def spawn(*args: object) -> object:
        if threading.current_thread().name == "unresolved-spawn":
            entered.set()
            assert release.wait(5)
        return native_spawn(*args)

    monkeypatch.setattr(world, "spawn_actor", spawn)
    thread = threading.Thread(
        target=lambda: creation_case.api.spawn_actor_batch([_spawn_request()]),
        name="unresolved-spawn",
    )
    thread.start()
    try:
        assert entered.wait(5)
        creation_case.api.spawn_actor_batch([_spawn_request()])
        assert len(world.actors) == 1
        with pytest.raises(OwnershipError, match="creation"):
            RunOwnership(creation_case.journal_path).require_completed_creations()
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    creation_case.ownership.require_completed_creations()


def _child(path_value: str, ready_value: str, mode: str) -> None:
    path, ready = Path(path_value), Path(ready_value)
    world = CreationWorld(interrupt_after=100)
    ownership = RunOwnership(path)
    adapter = PythonCarlaAdapter()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(adapter_objects, "carla_transform", lambda value: value)
        adapter._connected_client = cast(  # noqa: SLF001 - isolate only native CARLA transport.
            "CarlaClient", SimpleNamespace(get_world=lambda: world)
        )
        if mode == "failed_add":
            patch.setattr(ownership, "add", _fail_add)
            patch.setattr(adapter, "apply_batch", lambda *_args, **_kwargs: _hang(ready))
        elif mode == "verification":

            def identity() -> int:
                if world.actors:
                    _hang(ready)
                return WORLD_ID

            patch.setattr(adapter, "get_world_identity", identity)
        else:
            native_spawn = world.spawn_actor

            def spawn(*args: object) -> object:
                if len(world.actors) == COMPLETED_ACTORS:
                    return _hang(ready)
                return native_spawn(*args)

            patch.setattr(world, "spawn_actor", spawn)
        CarlaScriptApi(adapter, RunSnapshots(), ownership=ownership).spawn_actor_batch(
            [_spawn_request()] * (COMPLETED_ACTORS + 1)
        )


def _fail_add(*_args: object, **_kwargs: object) -> None:
    message = "journal unavailable after raw ID"
    raise OwnershipError(message)


def _hang(ready: Path) -> object:
    ready.touch()
    while True:
        time.sleep(0.05)


def _await_ready(process: subprocess.Popen[bytes], ready: Path) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if ready.exists():
            return
        if process.poll() is not None:
            pytest.fail("Creation child exited before the interruption boundary.")
        time.sleep(0.01)
    pytest.fail("Creation child did not reach its interruption boundary.")


@pytest.mark.skipif(sys.platform != "linux", reason="SIGKILL recovery requires Linux")
@pytest.mark.parametrize("mode", ["journaled", "failed_add", "verification"])
def test_killed_creation_preserves_ids_and_quarantines_unfinished_rpc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """A fresh reader refuses clean recovery after a kill during spawn or failed-add rollback."""
    path = journal(tmp_path, monkeypatch, [])
    ready = tmp_path / "ready"
    process = subprocess.Popen(  # noqa: S603 - fixed trusted child reproduces process termination.
        [sys.executable, "-c", CHILD_PROGRAM, str(path), str(ready), mode],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _await_ready(process, ready)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
    expected_ids = {
        "journaled": tuple(range(FIRST_ACTOR_ID, FIRST_ACTOR_ID + COMPLETED_ACTORS)),
        "failed_add": (),
        "verification": (FIRST_ACTOR_ID,),
    }
    expected = expected_ids[mode]
    assert RunOwnership(path).actor_ids() == expected
    with pytest.raises((ValueError, OwnershipError), match="creation"):
        script_recovery._ownership(path).require_completed_creations()  # noqa: SLF001
