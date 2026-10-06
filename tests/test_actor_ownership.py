"""Failure-safe lifecycle tracking for actors created by one script."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import sandbox, script_runner
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import OwnershipError
from carla_agentic_toolkit.models import (
    DestroyResult,
    Location,
    Rotation,
    SensorInfo,
    SpawnResult,
    Transform,
)
from carla_agentic_toolkit.ownership import OWNERSHIP_FILENAME, RunOwnership, cleanup_owned_actors
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from collections.abc import Sequence


ACTOR_ID = 101
SENSOR_ID = 202
WALKER_ID = 303
CONTROLLER_ID = 304
PREEXISTING_ACTOR_ID = 999


@dataclass
class OwnershipAdapter:
    """Adapter fake that records explicit destruction."""

    destroyed: list[int] = field(default_factory=list)
    detached: list[int] = field(default_factory=list)
    host: str = "127.0.0.1"
    port: int = 2000
    timeout: float = 1.0
    world_instance: int = 17

    def get_world_identity(self) -> int:
        """Read episode identity without ticking."""
        return self.world_instance

    def close_sensor_subscriptions(self) -> None:
        """Satisfy the adapter cleanup contract; this fake never installs listeners."""

    def spawn_actor_batch(self, requests: object) -> tuple[SpawnResult, ...]:
        """Return one successful actor and one failed spawn."""
        del requests
        return (
            SpawnResult(request_index=0, actor_id=ACTOR_ID, error=None),
            SpawnResult(request_index=1, actor_id=None, error="blocked"),
        )

    def attach_sensor(self, **_kwargs: object) -> SensorInfo:
        """Return one attached sensor."""
        return SensorInfo(
            sensor_id=SENSOR_ID,
            blueprint_id="sensor.camera.rgb",
            parent_actor_id=ACTOR_ID,
            attributes={},
            transform=_transform(),
        )

    def spawn_walkers(self, **_kwargs: object) -> dict[str, object]:
        """Return one walker/controller pair."""
        return {
            "walker_ids": [WALKER_ID],
            "controller_ids": [CONTROLLER_ID],
            "failed_spawns": [],
        }

    def destroy_actors(self, actor_ids: tuple[int, ...]) -> tuple[DestroyResult, ...]:
        """Destroy every requested actor."""
        self.destroyed.extend(actor_ids)
        return tuple(DestroyResult(actor_id=item, destroyed=True, error=None) for item in actor_ids)

    def detach_sensor(self, sensor_id: int) -> dict[str, object]:
        """Destroy one requested sensor."""
        self.detached.append(sensor_id)
        return {"sensor_id": sensor_id, "destroyed": True}


def test_creation_and_explicit_destruction_update_the_journal(tmp_path: Path) -> None:
    """Successful actor and sensor creation should be journaled incrementally."""
    ownership = RunOwnership(tmp_path / OWNERSHIP_FILENAME)
    adapter = OwnershipAdapter()
    api = _api(adapter, ownership)

    api.spawn_actor_batch([_spawn_request(), _spawn_request()])
    api.attach_sensor("rgb", ACTOR_ID, _transform_dict())
    api.spawn_walkers(1)

    assert ownership.actor_ids() == (ACTOR_ID, SENSOR_ID, WALKER_ID, CONTROLLER_ID)
    assert ownership.world_id() == adapter.world_instance

    api.detach_sensor(SENSOR_ID)
    api.destroy_actors([ACTOR_ID, WALKER_ID, CONTROLLER_ID])

    assert ownership.actor_ids() == ()


def test_episode_change_during_creation_never_destroys_reused_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Capture identity before creation and refuse cleanup against a replaced world."""
    ownership = RunOwnership(tmp_path / OWNERSHIP_FILENAME)
    adapter = OwnershipAdapter()
    initial_world = adapter.world_instance
    spawn = adapter.spawn_actor_batch

    def replace_world(requests: object) -> tuple[SpawnResult, ...]:
        results = spawn(requests)
        adapter.world_instance += 1
        return results

    monkeypatch.setattr(adapter, "spawn_actor_batch", replace_world)
    result = _api(adapter, ownership).spawn_actor_batch([_spawn_request()])
    assert result["ok"] is False
    assert adapter.destroyed == []
    assert ownership.world_id() == initial_world


def test_legacy_unknown_episode_cleanup_fails_closed(tmp_path: Path) -> None:
    """Readable legacy IDs cannot authorize destruction in an unidentified episode."""
    path = tmp_path / OWNERSHIP_FILENAME
    path.write_text(json.dumps([ACTOR_ID]), encoding="utf-8")
    ownership = RunOwnership(path)
    adapter = OwnershipAdapter()
    assert ownership.actor_ids() == (ACTOR_ID,)
    result = _api(adapter, ownership).cleanup_owned_actors()
    assert result["failures"]
    assert adapter.destroyed == []
    assert ownership.actor_ids() == (ACTOR_ID,)


@pytest.mark.parametrize("resolved_handle", [False, True])
@pytest.mark.parametrize("server_error", [None, "server refused destruction"])
def test_fresh_actor_cleanup_uses_server_destroy_when_snapshot_omits_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    server_error: str | None,
    *,
    resolved_handle: bool,
) -> None:
    """Missing snapshots can omit a handle or make UE5's handle refuse destruction."""
    adapter = PythonCarlaAdapter()
    handle = SimpleNamespace(
        id=ACTOR_ID,
        type_id="vehicle.lincoln.mkz",
        attributes={},
        get_transform=lambda: None,
        get_velocity=lambda: None,
        destroy=lambda: False,
    )
    world = SimpleNamespace(
        id=17,
        get_actors=lambda _actor_ids=None: SimpleNamespace(
            find=lambda _actor_id: handle if resolved_handle else None
        ),
    )
    client = SimpleNamespace(get_world=lambda: world)
    monkeypatch.setattr(adapter, "_client", lambda: client)
    batches: list[tuple[list[dict[str, object]], bool]] = []

    def batch(commands: list[dict[str, object]], *, do_tick: bool = True) -> dict[str, object]:
        batches.append((commands, do_tick))
        return {"responses": [{"actor_id": ACTOR_ID, "error": server_error}]}

    monkeypatch.setattr(adapter, "apply_batch", batch)
    ownership = RunOwnership(tmp_path / OWNERSHIP_FILENAME)
    ownership.add((ACTOR_ID,), world_id=world.id)
    result = cleanup_owned_actors(adapter, ownership)
    assert (
        batches,
        result["destroyed_actor_ids"],
        ownership.actor_ids(),
        bool(result["failures"]),
    ) == (
        [([{"action": "destroy_actor", "actor_id": ACTOR_ID}], False)],
        [] if server_error else [ACTOR_ID],
        (ACTOR_ID,) if server_error else (),
        bool(server_error),
    )


def test_replaced_episode_cleanup_clears_old_ids_without_destroy(tmp_path: Path) -> None:
    """An old episode's actor IDs cannot refer to live replacement-world actors."""
    ownership = RunOwnership(tmp_path / OWNERSHIP_FILENAME)
    adapter = OwnershipAdapter()
    ownership.add((ACTOR_ID,), world_id=adapter.world_instance)
    adapter.world_instance += 1
    result = _api(adapter, ownership).cleanup_owned_actors()
    assert result["failures"] == []
    assert result["world_changed"] is True
    assert adapter.destroyed == []
    assert ownership.actor_ids() == ()


def test_ownership_clear_allows_later_world_binding_without_eager_connection(
    tmp_path: Path,
) -> None:
    """Empty journals and facade construction need no connection; replacement resets identity."""
    ownership = RunOwnership(tmp_path / OWNERSHIP_FILENAME)
    assert ownership.world_id() is None
    adapter = OwnershipAdapter()
    api = _api(adapter, ownership)
    api.spawn_actor_batch([_spawn_request()])
    ownership.clear()
    adapter.world_instance += 1
    api.spawn_actor_batch([_spawn_request()])
    assert ownership.world_id() == adapter.world_instance
    saved = json.loads((tmp_path / OWNERSHIP_FILENAME).read_text(encoding="utf-8"))
    assert saved == {
        "schema_version": 1,
        "world_id": adapter.world_instance,
        "actor_ids": [ACTOR_ID],
    }


def test_journal_failure_rolls_back_newly_spawned_actors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A tracking failure must not turn a created actor into an unowned leak."""
    ownership = RunOwnership(tmp_path / OWNERSHIP_FILENAME)
    adapter = OwnershipAdapter()

    def fail(_actor_ids: object) -> None:
        message = "journal unavailable"
        raise OwnershipError(message)

    monkeypatch.setattr(ownership, "add", fail)

    result = _api(adapter, ownership).spawn_actor_batch([_spawn_request(), _spawn_request()])

    assert result["ok"] is False
    assert result["error_type"] == "spawn_actor_batch_failed"
    assert adapter.destroyed == [ACTOR_ID]


def test_explicit_owned_cleanup_never_touches_preexisting_actors(tmp_path: Path) -> None:
    """Bulk cleanup should destroy only IDs written by this execution."""
    ownership = RunOwnership(tmp_path / OWNERSHIP_FILENAME)
    adapter = OwnershipAdapter()
    ownership.add((ACTOR_ID, SENSOR_ID), world_id=adapter.world_instance)

    result = _api(adapter, ownership).cleanup_owned_actors()

    assert adapter.destroyed == [SENSOR_ID, ACTOR_ID]
    assert PREEXISTING_ACTOR_ID not in adapter.destroyed
    assert result["destroyed_actor_ids"] == [SENSOR_ID, ACTOR_ID]
    assert ownership.actor_ids() == ()


def test_runner_cleans_owned_actors_without_masking_script_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An uncaught script exception should cleanup before returning its original type."""
    adapter = OwnershipAdapter()
    monkeypatch.setattr(script_runner, "PythonCarlaAdapter", lambda **_kwargs: adapter)
    script = tmp_path / "script.py"
    script.write_text(
        f"api.spawn_actor_batch([{_spawn_request()!r}, {_spawn_request()!r}])\nassert False\n",
        encoding="utf-8",
    )
    ownership_path = tmp_path / OWNERSHIP_FILENAME

    outcome = script_runner.run_script_file(
        script_path=script,
        host=adapter.host,
        port=adapter.port,
        timeout_seconds=adapter.timeout,
        ownership_path=ownership_path,
    )

    assert outcome["error_type"] == "AssertionError"
    assert cast("dict[str, object]", outcome["cleanup"])["destroyed_actor_ids"] == [ACTOR_ID]
    assert adapter.destroyed == [ACTOR_ID]
    assert RunOwnership(ownership_path).actor_ids() == ()


def test_parent_cleans_owned_actors_after_sandbox_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The parent should cleanup from the journal after the child is killed."""
    runner = tmp_path / "carla-agentic-toolkit-sandbox"
    runner.write_text("runner", encoding="utf-8")
    adapter = OwnershipAdapter()

    def run_command(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        work_dir = Path(command[command.index("--work-dir") + 1])
        RunOwnership(work_dir / OWNERSHIP_FILENAME).add(
            (ACTOR_ID,), world_id=adapter.world_instance
        )
        payload = {
            "ok": False,
            "result": None,
            "stdout": "",
            "error": "Script exceeded 1s timeout.",
            "error_type": "script_timeout",
            "timed_out": True,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(sandbox.subprocess, "run", run_command)
    monkeypatch.setattr(
        sandbox,
        "cleanup_script_ownership",
        lambda _host, _port, path, *_rest: cleanup_owned_actors(
            cast("PythonCarlaAdapter", adapter),
            RunOwnership(path),
        ),
    )

    outcome = sandbox.execute_script("result = api.wait(10)", timeout_seconds=1)

    assert outcome.error_type == "script_timeout"
    assert outcome.cleanup == {
        "attempted_actor_ids": [ACTOR_ID],
        "destroyed_actor_ids": [ACTOR_ID],
        "failures": [],
    }
    assert adapter.destroyed == [ACTOR_ID]


@pytest.mark.parametrize(
    ("exception", "error_type"),
    [
        (subprocess.TimeoutExpired(["runner"], 1), "sandbox_watchdog_error"),
        (OSError("runner unavailable"), "sandbox_launcher_error"),
    ],
)
def test_parent_cleans_journal_after_wrapper_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    exception: Exception,
    error_type: str,
) -> None:
    """Wrapper failures must preserve cleanup before deleting the run journal."""
    runner = tmp_path / "runner"
    runner.touch()
    adapter = OwnershipAdapter()

    def run_command(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        work_dir = Path(command[command.index("--work-dir") + 1])
        RunOwnership(work_dir / OWNERSHIP_FILENAME).add(
            (ACTOR_ID, SENSOR_ID),
            world_id=adapter.world_instance,
        )
        raise exception

    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(sandbox.subprocess, "run", run_command)
    monkeypatch.setattr(
        sandbox,
        "cleanup_script_ownership",
        lambda _host, _port, path, *_rest: cleanup_owned_actors(
            cast("PythonCarlaAdapter", adapter),
            RunOwnership(path),
        ),
    )

    outcome = sandbox.execute_script("result = 1")

    assert outcome.error_type == error_type
    assert adapter.destroyed == [SENSOR_ID, ACTOR_ID]
    assert outcome.cleanup == {
        "attempted_actor_ids": [SENSOR_ID, ACTOR_ID],
        "destroyed_actor_ids": [SENSOR_ID, ACTOR_ID],
        "failures": [],
    }


def _api(adapter: OwnershipAdapter, ownership: RunOwnership) -> CarlaScriptApi:
    return CarlaScriptApi(
        cast("PythonCarlaAdapter", adapter),
        RunSnapshots(),
        ownership=ownership,
    )


def _spawn_request() -> dict[str, object]:
    return {
        "blueprint_id": "vehicle.tesla.model3",
        "transform": _transform_dict(),
        "attributes": {"role_name": "owned-test"},
    }


def _transform_dict() -> dict[str, object]:
    return {
        "location": {"x": 0.0, "y": 0.0, "z": 1.0},
        "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
    }


def _transform() -> Transform:
    return Transform(
        location=Location(x=0.0, y=0.0, z=1.0),
        rotation=Rotation(pitch=0.0, yaw=0.0, roll=0.0),
    )
