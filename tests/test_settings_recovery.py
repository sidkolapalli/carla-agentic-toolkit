"""Trusted recovery restores settings before granting clean endpoint ownership."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import adapter as adapter_module
from carla_agentic_toolkit import script_recovery
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.models import DestroyResult
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_script_recovery import journal

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.carla_protocols import CarlaWorld

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Trusted recovery requires Linux")

WORLD_ID = 7
ACTOR_ID = 17


def _baseline() -> dict[str, object]:
    return {
        "synchronous_mode": False,
        "fixed_delta_seconds": None,
        "no_rendering_mode": True,
        "substepping": True,
        "max_substeps": 8,
        "max_substep_delta_time": 0.01,
    }


@dataclass
class SettingsWorld:
    """Keep restoration and frame advancement observable at the RPC boundary."""

    id: int = WORLD_ID
    frame: int = 10
    settings: dict[str, object] = field(default_factory=_baseline)
    events: list[str] = field(default_factory=list)
    restore_failure: str = ""

    def get_settings(self) -> SimpleNamespace:
        """Return an isolated settings object as CARLA does."""
        return SimpleNamespace(**self.settings)

    def apply_settings(self, settings: SimpleNamespace) -> int:
        """Apply or inject a failed restore, including a silently ignored update."""
        self.events.append("restore")
        if self.restore_failure == "raise":
            message = "injected settings RPC failure"
            raise RuntimeError(message)
        if self.restore_failure != "mismatch":
            self.settings = vars(settings)
        return self.frame

    def get_snapshot(self) -> SimpleNamespace:
        """Expose the currently published frame."""
        return SimpleNamespace(frame=self.frame)

    def wait_for_tick(self, timeout: float) -> SimpleNamespace:
        """Advance only after recovery has restored asynchronous settings."""
        del timeout
        assert self.settings["synchronous_mode"] is False
        self.events.append("wait")
        self.frame += 1
        return self.get_snapshot()


def _adapter(world: SettingsWorld) -> PythonCarlaAdapter:
    def destroy(actor_ids: tuple[int, ...]) -> tuple[DestroyResult, ...]:
        world.events.append("destroy")
        return tuple(DestroyResult(actor_id, destroyed=True, error=None) for actor_id in actor_ids)

    value = SimpleNamespace(
        _client=lambda: SimpleNamespace(get_world=lambda: world),
        get_world_identity=lambda: world.id,
        destroy_actors=destroy,
    )
    return cast("PythonCarlaAdapter", value)


def _records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, world: SettingsWorld, *, actors: bool = False
) -> tuple[Path, RunOwnership, RunSettings]:
    path = journal(tmp_path, monkeypatch, [])
    ownership = RunOwnership(path)
    if actors:
        ownership.add((ACTOR_ID,), world_id=world.id)
    settings = RunSettings(path.with_name(SETTINGS_FILENAME), require_existing=True)
    settings.initialize()
    settings.capture_world(cast("CarlaWorld", world))
    world.settings.update(synchronous_mode=True, fixed_delta_seconds=0.05)
    return path, ownership, settings


def test_restore_precedes_fresh_snapshot_and_actor_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery must not tick or destroy while the abandoned run's settings remain active."""
    world = SettingsWorld()
    _path, ownership, settings = _records(tmp_path, monkeypatch, world, actors=True)
    result = script_recovery._cleanup_connected(_adapter(world), ownership, settings)  # noqa: SLF001
    assert (
        world.events,
        result["settings_restored"],
        result["failures"],
        ownership.actor_ids(),
        settings.pending(),
    ) == (["restore", "wait", "destroy"], True, [], (), False)


def test_successful_run_settings_restore_preserves_created_actors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings verification on nominal success does not change finite-script actor lifetime."""
    world = SettingsWorld()
    _path, ownership, settings = _records(tmp_path, monkeypatch, world, actors=True)
    result = script_recovery._cleanup_connected(  # noqa: SLF001
        _adapter(world), ownership, settings, destroy_actors=False
    )
    assert world.events == ["restore"]
    assert result["settings_restored"] is True
    assert ownership.actor_ids() == (ACTOR_ID,)


@pytest.mark.parametrize("restore_failure", ["raise", "mismatch"])
def test_failed_restore_never_advances_or_destroys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_failure: str
) -> None:
    """A failed settings restore retains both journals for later trusted recovery."""
    world = SettingsWorld(restore_failure=restore_failure)
    _path, ownership, settings = _records(tmp_path, monkeypatch, world, actors=True)
    result = script_recovery._cleanup_connected(_adapter(world), ownership, settings)  # noqa: SLF001
    assert (
        result["settings_restored"],
        bool(result["failures"]),
        world.events,
        ownership.actor_ids(),
        settings.pending(),
    ) == (False, True, ["restore"], (ACTOR_ID,), True)


@pytest.mark.parametrize("damage", ["absent", "symlink", "large", "malformed", "hardlink"])
def test_invalid_required_settings_cannot_release_empty_actor_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    """No actors is insufficient cleanup evidence when a recorded settings journal is invalid."""
    path = journal(tmp_path, monkeypatch, [])
    settings_path = path.with_name(SETTINGS_FILENAME)
    RunSettings(settings_path).initialize()
    _damage_settings(settings_path, tmp_path, damage)
    state = tmp_path / "state"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty(
            {"kind": "script", "ownership_path": str(path), "settings_path": str(settings_path)}
        )
    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)
    assert result["ok"] is False
    with SimulatorLease("localhost", 3000, state_root=state, recovering=True) as lease:
        assert lease.recovery_state


def _damage_settings(settings_path: Path, tmp_path: Path, damage: str) -> None:
    if damage == "absent":
        settings_path.unlink()
    elif damage == "symlink":
        settings_path.unlink()
        settings_path.symlink_to(tmp_path / "redirect")
    elif damage == "large":
        settings_path.write_bytes(b" " * (script_recovery.MAX_JOURNAL_BYTES + 1))
    elif damage == "hardlink":
        (tmp_path / "alias").hardlink_to(settings_path)
    else:
        settings_path.write_text('{"schema_version": 1}')


def _inline_worker(
    monkeypatch: pytest.MonkeyPatch, world: SettingsWorld
) -> list[dict[str, object]]:
    requests: list[dict[str, object]] = []
    monkeypatch.setattr(adapter_module, "PythonCarlaAdapter", lambda **_kwargs: _adapter(world))

    def spawn(job: Path, _descriptor: int) -> subprocess.Popen[bytes]:
        request = read_control(job / "request.json")
        requests.append(request)
        report, _adapter_owner = script_recovery._worker_cleanup(request)  # noqa: SLF001
        write_control(job / "result.json", report)
        return cast("subprocess.Popen[bytes]", SimpleNamespace(wait=lambda **_kwargs: 0))

    monkeypatch.setattr(script_recovery, "_spawn_cleanup", spawn)
    monkeypatch.setattr(script_recovery, "terminate_tree", lambda _process: None)
    return requests


@pytest.mark.parametrize("restore_failure", ["", "raise", "mismatch"])
def test_settings_only_abandoned_run_is_recovered_by_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_failure: str
) -> None:
    """A killed settings-only run launches trusted restoration and clears only verified leases."""
    world = SettingsWorld(restore_failure=restore_failure)
    path, ownership, settings = _records(tmp_path, monkeypatch, world)
    requests = _inline_worker(monkeypatch, world)
    state = tmp_path / "state"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty(
            {
                "kind": "script",
                "ownership_path": str(path),
                "settings_path": str(path.with_name(SETTINGS_FILENAME)),
            }
        )
    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)
    assert (
        len(requests),
        requests[0]["require_settings"],
        result["ok"],
        world.events,
        ownership.actor_ids(),
        settings.pending(),
    ) == (1, True, not restore_failure, ["restore"], (), bool(restore_failure))
    with SimulatorLease("localhost", 3000, state_root=state, recovering=True) as lease:
        assert bool(lease.recovery_state) is bool(restore_failure)


def test_recovery_rejects_settings_path_outside_owned_workdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lease recovery cannot redirect settings restoration to another sandbox's evidence."""
    path = journal(tmp_path, monkeypatch, [])
    RunSettings(path.with_name(SETTINGS_FILENAME)).initialize()
    state = tmp_path / "state"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty(
            {
                "kind": "script",
                "ownership_path": str(path),
                "settings_path": str(tmp_path / SETTINGS_FILENAME),
            }
        )
    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)
    assert result["ok"] is False
    with SimulatorLease("localhost", 3000, state_root=state, recovering=True) as lease:
        assert lease.recovery_state


def test_detected_settings_journal_cannot_disappear_before_worker_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy callers that discover settings evidence must require it in the cleanup worker."""
    world = SettingsWorld()
    path, _ownership, _settings = _records(tmp_path, monkeypatch, world)
    requests = _inline_worker(monkeypatch, world)
    original_spawn = script_recovery._spawn_cleanup  # noqa: SLF001

    def lose_settings(job: Path, descriptor: int) -> subprocess.Popen[bytes]:
        path.with_name(SETTINGS_FILENAME).unlink()
        return original_spawn(job, descriptor)

    monkeypatch.setattr(script_recovery, "_spawn_cleanup", lose_settings)
    result = script_recovery.cleanup_script_ownership("localhost", 3000, path, -1, 1)
    assert requests[0]["require_settings"] is True
    assert result["failures"]
    assert world.settings["synchronous_mode"] is True


def test_missing_ownership_path_with_settings_evidence_retains_quarantine(tmp_path: Path) -> None:
    """Invalid lease paths report failed recovery without throwing or clearing dirty state."""
    state = tmp_path / "state"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty({"kind": "script", "settings_path": str(tmp_path / SETTINGS_FILENAME)})
    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)
    assert result["ok"] is False
    with SimulatorLease("localhost", 3000, state_root=state, recovering=True) as lease:
        assert lease.recovery_state
