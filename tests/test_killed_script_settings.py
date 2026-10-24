"""A killed script cannot erase settings recovery evidence from durable storage."""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import adapter as adapter_module
from carla_agentic_toolkit import script_recovery, script_runner
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_script_recovery import journal
from tests.test_settings_recovery import _baseline

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="Recovery and SIGKILL require Linux"
)

CHILD_PROGRAM = (
    "import sys; from tests.test_killed_script_settings import _child_run; "
    "_child_run(*sys.argv[1:])"
)
WORLD_ID = 7


@dataclass
class FileWorld:
    """Keep fake server settings outside the child, as a real simulator would."""

    state_path: Path
    id: int = WORLD_ID
    events: list[str] = field(default_factory=list)

    def get_settings(self) -> SimpleNamespace:
        """Read current settings from the external fake simulator state."""
        return SimpleNamespace(**read_control(self.state_path))

    def apply_settings(self, settings: SimpleNamespace) -> int:
        """Persist simulator mutations independently of the runner's ownership journal."""
        self.events.append("apply")
        write_control(self.state_path, vars(settings))
        return 10

    def get_map(self) -> SimpleNamespace:
        """Provide world-state payload metadata without a simulator."""
        return SimpleNamespace(name="Town01")

    def get_actors(self) -> SimpleNamespace:
        """No actors are spawned by this settings-only reproduction."""
        return SimpleNamespace(filter=lambda _pattern: [])

    def get_snapshot(self) -> SimpleNamespace:
        """Provide the frame needed to serialize a completed sync-mode call."""
        return SimpleNamespace(frame=10)


def _file_adapter(world: FileWorld, settings: RunSettings | None = None) -> PythonCarlaAdapter:
    adapter = PythonCarlaAdapter(settings_journal=settings)
    adapter._connected_client = cast(  # noqa: SLF001 - isolate only the native CARLA boundary.
        "CarlaClient", SimpleNamespace(get_world=lambda: world)
    )
    return adapter


def _child_run(script_path: str, ownership_path: str, state_path: str) -> None:
    world = FileWorld(Path(state_path))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            script_runner,
            "PythonCarlaAdapter",
            lambda **values: _file_adapter(world, cast("RunSettings", values["settings_journal"])),
        )
        script_runner.run_script_file(
            script_path=Path(script_path),
            host="localhost",
            port=3000,
            timeout_seconds=1,
            ownership_path=Path(ownership_path),
            require_settings_journal=True,
        )


def _await_sync(process: subprocess.Popen[bytes], state_path: Path) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if read_control(state_path)["synchronous_mode"] is True:
            return
        if process.poll() is not None:
            pytest.fail(f"Script exited before enabling sync: {process.returncode}")
        time.sleep(0.01)
    pytest.fail("Script did not enable synchronous mode before its test deadline.")


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def _recover_in_worker(monkeypatch: pytest.MonkeyPatch, world: FileWorld) -> None:
    monkeypatch.setattr(
        adapter_module, "PythonCarlaAdapter", lambda **_kwargs: _file_adapter(world)
    )

    def spawn(job: Path, _descriptor: int) -> subprocess.Popen[bytes]:
        report, _native_owner = script_recovery._worker_cleanup(  # noqa: SLF001
            read_control(job / "request.json")
        )
        write_control(job / "result.json", report)
        return cast("subprocess.Popen[bytes]", SimpleNamespace(wait=lambda **_kwargs: 0))

    monkeypatch.setattr(script_recovery, "_spawn_cleanup", spawn)
    monkeypatch.setattr(script_recovery, "terminate_tree", lambda _process: None)


def _interrupt_script(script: Path, path: Path, state_path: Path, descriptor: int) -> int:
    process = subprocess.Popen(  # noqa: S603 - fixed trusted child reproduces process termination.
        [sys.executable, "-c", CHILD_PROGRAM, str(script), str(path), str(state_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=(descriptor,),
    )
    try:
        _await_sync(process, state_path)
    finally:
        _kill_and_reap(process)
    return cast("int", process.returncode)


def test_sigkill_preserves_settings_for_a_new_reader_and_trusted_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real killed runner leaves its first baseline recoverable without its in-memory objects."""
    path = journal(tmp_path, monkeypatch, [])
    settings_path = path.with_name(SETTINGS_FILENAME)
    RunSettings(settings_path).initialize()
    baseline = _baseline()
    state_path = tmp_path / "simulator.json"
    write_control(state_path, baseline)
    script = path.parent / "script.py"
    script.write_text(
        "api.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)\nwhile True:\n    pass\n"
    )
    state = tmp_path / "lease"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty(
            {"kind": "script", "ownership_path": str(path), "settings_path": str(settings_path)}
        )
        exit_code = _interrupt_script(script, path, state_path, lease.descriptor)
    _assert_killed_evidence(exit_code, settings_path, state_path, baseline)
    world = FileWorld(state_path)
    _recover_in_worker(monkeypatch, world)
    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)
    _assert_recovered(result, settings_path, state_path, baseline)
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        assert lease.recovery_state == {}


def _assert_killed_evidence(
    exit_code: int, settings_path: Path, state_path: Path, baseline: dict[str, object]
) -> None:
    assert exit_code == -signal.SIGKILL
    assert read_control(state_path)["synchronous_mode"] is True
    assert read_control(settings_path)["world_settings"] == baseline
    assert RunSettings(settings_path, require_existing=True).pending() is True


def _assert_recovered(
    result: dict[str, object], settings_path: Path, state_path: Path, baseline: dict[str, object]
) -> None:
    cleanup = cast("dict[str, object]", result["cleanup"])
    assert (result["ok"], cleanup["settings_restored"], cleanup["failures"]) == (True, True, [])
    assert read_control(state_path) == baseline
    assert RunSettings(settings_path, require_existing=True).pending() is False
