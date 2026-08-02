"""Failure-safe lifecycle tracking for actors created by one script."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, cast

from carla_mcp import sandbox, script_runner
from carla_mcp.errors import OwnershipError
from carla_mcp.models import (
    DestroyResult,
    Location,
    Rotation,
    SensorInfo,
    SpawnResult,
    Transform,
)
from carla_mcp.ownership import OWNERSHIP_FILENAME, RunOwnership
from carla_mcp.script_api import CarlaScriptApi
from carla_mcp.snapshots import RunSnapshots

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

    from carla_mcp.adapter import CarlaAdapter

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

    api.detach_sensor(SENSOR_ID)
    api.destroy_actors([ACTOR_ID, WALKER_ID, CONTROLLER_ID])

    assert ownership.actor_ids() == ()


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
    ownership.add((ACTOR_ID, SENSOR_ID))
    adapter = OwnershipAdapter()

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
    runner = tmp_path / "carla-mcp-sandbox"
    runner.write_text("runner", encoding="utf-8")
    adapter = OwnershipAdapter()

    def run_command(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        work_dir = Path(command[command.index("--work-dir") + 1])
        RunOwnership(work_dir / OWNERSHIP_FILENAME).add((ACTOR_ID,))
        payload = {
            "ok": False,
            "result": None,
            "stdout": "",
            "error": "Script exceeded 1s timeout.",
            "error_type": "script_timeout",
            "timed_out": True,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setenv("CARLA_MCP_SANDBOX", str(runner))
    monkeypatch.setenv("CARLA_MCP_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(sandbox.subprocess, "run", run_command)
    monkeypatch.setattr(sandbox, "PythonCarlaAdapter", lambda **_kwargs: adapter)

    outcome = sandbox.execute_script("result = api.wait(10)", timeout_seconds=1)

    assert outcome.error_type == "script_timeout"
    assert outcome.cleanup == {
        "attempted_actor_ids": [ACTOR_ID],
        "destroyed_actor_ids": [ACTOR_ID],
        "failures": [],
    }
    assert adapter.destroyed == [ACTOR_ID]


def _api(adapter: OwnershipAdapter, ownership: RunOwnership) -> CarlaScriptApi:
    return CarlaScriptApi(
        cast("CarlaAdapter", adapter),
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
