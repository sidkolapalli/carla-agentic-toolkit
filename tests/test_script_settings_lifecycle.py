"""Scripts must restore simulator timing before relinquishing ownership."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.script_runner import MAX_SCRIPT_STDOUT_BYTES, run_script_file
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient


@dataclass
class Settings:
    """Independent settings copies match CARLA's get_settings semantics."""

    synchronous_mode: bool = False
    fixed_delta_seconds: float | None = None
    no_rendering_mode: bool = False
    substepping: bool = True
    max_substeps: int = 10
    max_substep_delta_time: float = 0.01


@dataclass
class SettingsWorld:
    """Expose settings application and rejection without a simulator."""

    settings: Settings = field(default_factory=Settings)
    id: int = 7
    frame: int = 10
    reject_restore: bool = False
    events: list[str] = field(default_factory=list)

    def get_settings(self) -> Settings:
        """Return a detached current settings value."""
        return replace(self.settings)

    def apply_settings(self, settings: Settings) -> int:
        """Accept writes unless the test rejects restoration."""
        if self.reject_restore and not settings.synchronous_mode:
            message = "restore rejected"
            raise RuntimeError(message)
        self.settings = replace(settings)
        self.events.append("apply")
        return self.frame

    def get_map(self) -> SimpleNamespace:
        """Return a map name for the public world-state payload."""
        return SimpleNamespace(name="Town01")

    def get_actors(self) -> SimpleNamespace:
        """There are no actors in a settings-only script."""
        return SimpleNamespace(filter=lambda _pattern: [])

    def get_snapshot(self) -> SimpleNamespace:
        """Return the currently published frame."""
        return SimpleNamespace(frame=self.frame)

    def tick(self) -> int:
        """Publish a fresh owner-driven frame."""
        self.events.append("tick")
        self.frame += 1
        return self.frame

    def wait_for_tick(self, _seconds: float) -> SimpleNamespace:
        """Publish an asynchronous frame without an owner tick."""
        self.events.append("wait")
        self.frame += 1
        return self.get_snapshot()


def connected_world(monkeypatch: pytest.MonkeyPatch) -> SettingsWorld:
    """Replace only the CARLA connection, leaving real toolkit paths intact."""
    world = SettingsWorld()
    client = cast("CarlaClient", SimpleNamespace(get_world=lambda: world))
    monkeypatch.setattr(PythonCarlaAdapter, "_client", lambda _self: client)
    return world


@pytest.mark.parametrize(
    "ending",
    [
        "result = 1",
        "result = 1 / 0",
        f"print('x' * {MAX_SCRIPT_STDOUT_BYTES + 1}, end='')",
        "result = float('nan')",
    ],
)
def test_finite_script_restores_settings_on_every_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    """Success and failures restore timing even when no actors were created."""
    world = connected_world(monkeypatch)
    original = world.get_settings()
    script = tmp_path / "script.py"
    script.write_text(
        "api.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)\n" + ending,
        encoding="utf-8",
    )
    outcome = run_script_file(
        script_path=script,
        host="localhost",
        port=3000,
        timeout_seconds=1,
        ownership_path=tmp_path / "owned-actors.json",
    )
    assert world.get_settings() == original
    assert cast("dict[str, object]", outcome["cleanup"])["settings_restored"] is True


def test_successful_script_cannot_hide_failed_settings_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restoration RPC failure changes nominal success into cleanup failure."""
    world = connected_world(monkeypatch)
    world.reject_restore = True
    script = tmp_path / "script.py"
    script.write_text("api.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)\nresult = 1")
    outcome = run_script_file(
        script_path=script,
        host="localhost",
        port=3000,
        timeout_seconds=1,
        ownership_path=tmp_path / "owned-actors.json",
    )
    assert outcome["ok"] is False
    assert outcome["error_type"] == "settings_restore_failed"
    cleanup = cast("dict[str, object]", outcome["cleanup"])
    assert cleanup["settings_restored"] is False
    assert "restore rejected" in str(cleanup["failures"])


def test_launched_runner_cannot_recapture_a_lost_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing durable baseline cannot turn mutated settings into a new original."""
    world = connected_world(monkeypatch)
    requested_delta_seconds = 0.05
    path = tmp_path / SETTINGS_FILENAME
    RunSettings(path).initialize()
    apply_settings = world.apply_settings

    def lose_journal(settings: Settings) -> int:
        frame = apply_settings(settings)
        path.unlink(missing_ok=True)
        return frame

    monkeypatch.setattr(world, "apply_settings", lose_journal)
    script = tmp_path / "script.py"
    script.write_text(
        "api.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)\n"
        "api.set_sync_mode(enabled=True, fixed_delta_seconds=0.04)\nresult = 1"
    )
    outcome = run_script_file(
        script_path=script,
        host="localhost",
        port=3000,
        timeout_seconds=1,
        ownership_path=tmp_path / "owned-actors.json",
        require_settings_journal=True,
    )
    assert outcome["ok"] is False
    assert cast("dict[str, object]", outcome["cleanup"])["settings_restored"] is False
    assert path.exists() is False
    assert world.settings.fixed_delta_seconds == requested_delta_seconds
