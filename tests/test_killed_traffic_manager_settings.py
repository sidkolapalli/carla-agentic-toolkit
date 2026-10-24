"""A killed finite runner leaves TM globals recoverable by a fresh trusted reader."""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import script_runner
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_killed_script_settings import FileWorld, _kill_and_reap
from tests.test_script_recovery import journal
from tests.test_settings_recovery import _baseline
from tests.test_traffic_manager_settings_journal import (
    ATTEMPTED,
    DISTANCE,
    PORT,
    SEED,
    SPEED,
    SYNC,
    TARGETS,
    _inline_recovery,
    entry,
)

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="SIGKILL requires Linux")
CHILD_PROGRAM = (
    "import sys; from tests.test_killed_traffic_manager_settings import _child_run; "
    "_child_run(*sys.argv[1:])"
)


@dataclass
class FileManager:
    """Persist simulator-side globals independently of the child's settings journal."""

    path: Path

    def get_port(self) -> int:
        """Return the dedicated fake sidecar port."""
        return PORT

    def _set(self, setting: str, value: object) -> None:
        values = read_control(self.path)
        values[setting] = value
        write_control(self.path, values)

    def set_global_distance_to_leading_vehicle(self, value: float) -> None:
        """Write the external fake manager's distance."""
        self._set(DISTANCE, value)

    def global_percentage_speed_difference(self, value: float) -> None:
        """Write the external fake manager's speed difference."""
        self._set(SPEED, value)

    def set_random_device_seed(self, value: int) -> None:
        """Model the traffic-light reset side effect even when restoring seed zero."""
        values = read_control(self.path)
        values.update(
            seed=value,
            traffic_light_resets=cast("int", values["traffic_light_resets"]) + 1,
        )
        write_control(self.path, values)

    def set_synchronous_mode(self, value: bool) -> None:  # noqa: FBT001
        """Persist mode without exposing nonexistent TM setting getters."""
        values = read_control(self.path)
        values.update(synchronous_mode=value, sync_writes=cast("int", values["sync_writes"]) + 1)
        write_control(self.path, values)


def file_adapter(
    world: FileWorld, manager: FileManager, settings: RunSettings | None = None
) -> PythonCarlaAdapter:
    """Use real facade/runtime/journal behavior around only fake native RPC objects."""
    adapter = PythonCarlaAdapter(settings_journal=settings)
    adapter._connected_client = cast(  # noqa: SLF001
        "CarlaClient",
        SimpleNamespace(get_world=lambda: world, get_trafficmanager=lambda _port: manager),
    )
    return adapter


def _child_run(script: str, ownership: str, world_path: str, manager_path: str) -> None:
    world = FileWorld(Path(world_path))
    manager = FileManager(Path(manager_path))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            script_runner,
            "PythonCarlaAdapter",
            lambda **values: file_adapter(
                world, manager, cast("RunSettings", values["settings_journal"])
            ),
        )
        script_runner.run_script_file(
            script_path=Path(script),
            host="localhost",
            port=3000,
            timeout_seconds=1,
            ownership_path=Path(ownership),
            require_settings_journal=True,
        )


def _await_mutation(process: subprocess.Popen[bytes], manager_path: Path) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if read_control(manager_path)["sync_writes"] == 1:
            return
        if process.poll() is not None:
            pytest.fail(f"Runner exited before TM mutation: {process.returncode}")
        time.sleep(0.01)
    pytest.fail("Runner did not mutate TM globals before the test deadline.")


def _interrupt(
    script: Path, ownership: Path, world_path: Path, manager_path: Path, descriptor: int
) -> int:
    process = subprocess.Popen(  # noqa: S603 - fixed trusted child and validated local test files.
        [
            sys.executable,
            "-c",
            CHILD_PROGRAM,
            str(script),
            str(ownership),
            str(world_path),
            str(manager_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(descriptor,),
    )
    try:
        _await_mutation(process, manager_path)
    finally:
        _kill_and_reap(process)
    return cast("int", process.returncode)


def test_sigkill_tm_mutations_are_restored_from_durable_attempted_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery restores declared targets, not the external sidecar's unknown old values."""
    ownership = journal(tmp_path, monkeypatch, [])
    settings_path = ownership.with_name(SETTINGS_FILENAME)
    RunSettings(settings_path).initialize()
    world_path = tmp_path / "world.json"
    write_control(world_path, _baseline())
    manager_path = tmp_path / "manager.json"
    write_control(
        manager_path,
        {
            DISTANCE: 6.0,
            SPEED: -5.0,
            SEED: 99,
            SYNC: False,
            "traffic_light_resets": 0,
            "sync_writes": 0,
        },
    )
    script = ownership.parent / "script.py"
    script.write_text(f"api.configure_traffic_manager({ATTEMPTED!r})\nwhile True:\n    pass\n")
    state = tmp_path / "lease"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty(
            {
                "kind": "script",
                "ownership_path": str(ownership),
                "settings_path": str(settings_path),
            }
        )
        exit_code = _interrupt(script, ownership, world_path, manager_path, lease.descriptor)
    _assert_durable_evidence(exit_code, settings_path)
    world = FileWorld(world_path)
    _inline_recovery(monkeypatch, file_adapter(world, FileManager(manager_path)))

    from carla_agentic_toolkit import script_recovery  # noqa: PLC0415

    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)

    _assert_recovered(result, settings_path, world_path, manager_path)
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        assert lease.recovery_state == {}


def _assert_durable_evidence(exit_code: int, settings_path: Path) -> None:
    assert exit_code == -signal.SIGKILL
    assert RunSettings(settings_path, require_existing=True).pending() is True
    assert read_control(settings_path)["traffic_managers"] == [entry(TARGETS, ATTEMPTED)]


def _assert_recovered(
    result: dict[str, object], settings_path: Path, world_path: Path, manager_path: Path
) -> None:
    cleanup = cast("dict[str, object]", result["cleanup"])
    assert (result["ok"], cleanup["settings_restored"], cleanup["failures"]) == (True, True, [])
    assert RunSettings(settings_path, require_existing=True).pending() is False
    assert read_control(world_path) == _baseline()
    assert read_control(manager_path) == TARGETS | {"traffic_light_resets": 2, "sync_writes": 2}
