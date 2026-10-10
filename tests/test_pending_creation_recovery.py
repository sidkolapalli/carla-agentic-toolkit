"""An unresolved native spawn quarantines the lease without abandoning known safe cleanup."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import script_recovery
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_settings_recovery import ACTOR_ID, SettingsWorld, _adapter, _inline_worker, _records

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Trusted recovery requires Linux")


def test_pending_spawn_restores_settings_and_cleans_known_ids_without_releasing_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh trusted reader recovers observed actors, never inferring an unknown spawn ID."""
    world = SettingsWorld()
    path, ownership, settings = _records(tmp_path, monkeypatch, world, actors=True)
    ownership.begin_creation(world.id)
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

    report = cast("dict[str, object]", result["cleanup"])
    assert (result["ok"], result["recovery_required"], len(requests)) == (False, True, 1)
    assert (world.events, ownership.actor_ids(), settings.pending()) == (
        ["restore", "wait", "destroy"],
        (),
        False,
    )
    _assert_quarantined_report(report)
    assert report["destroyed_actor_ids"] == [ACTOR_ID]
    assert read_control(path)["pending_creations"] == 1
    _assert_dirty_lease(state)


def _assert_dirty_lease(state: Path) -> None:
    with SimulatorLease("localhost", 3000, state_root=state, recovering=True) as lease:
        assert lease.recovery_state


@pytest.mark.parametrize("actors", [False, True])
def test_pending_nominal_success_preserves_actors_but_restores_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, actors: bool
) -> None:
    """The parent's success-validation mode may restore timing, never waive unresolved intent."""
    world = SettingsWorld()
    path, ownership, settings = _records(tmp_path, monkeypatch, world, actors=actors)
    ownership.begin_creation(world.id)
    _inline_worker(monkeypatch, world)

    report = script_recovery.cleanup_script_ownership(
        "localhost", 3000, path, -1, 5.0, require_settings=True, destroy_actors=False
    )

    _assert_quarantined_report(report)
    assert (world.events, ownership.actor_ids(), settings.pending()) == (
        ["restore"],
        (ACTOR_ID,) if actors else (),
        False,
    )
    assert read_control(path)["pending_creations"] == 1


def test_pending_old_episode_is_never_cleared_or_destroyed_in_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An old-world ID and unknown intent cannot authorize cleanup in a replacement episode."""
    world = SettingsWorld()
    path, ownership, settings = _records(tmp_path, monkeypatch, world, actors=True)
    assert settings.restore(_adapter(world))["settings_restored"] is True
    ownership.begin_creation(world.id)
    recorded_world = world.id
    world.id += 1
    world.events.clear()
    _inline_worker(monkeypatch, world)

    report = script_recovery.cleanup_script_ownership(
        "localhost", 3000, path, -1, 5.0, require_settings=True
    )

    _assert_quarantined_report(report)
    assert (world.events, ownership.actor_ids(), ownership.world_id()) == (
        [],
        (ACTOR_ID,),
        recorded_world,
    )
    assert read_control(path)["pending_creations"] == 1


@pytest.mark.parametrize("failure", ["raise", "mismatch"])
def test_pending_restore_failure_never_ticks_or_destroys_known_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Pending best-effort recovery retains the existing restore-before-destruction rule."""
    world = SettingsWorld(restore_failure=failure)
    path, ownership, settings = _records(tmp_path, monkeypatch, world, actors=True)
    ownership.begin_creation(world.id)
    _inline_worker(monkeypatch, world)

    report = script_recovery.cleanup_script_ownership(
        "localhost", 3000, path, -1, 5.0, require_settings=True
    )

    assert (
        report["settings_restored"],
        world.events,
        ownership.actor_ids(),
        settings.pending(),
    ) == (
        False,
        ["restore"],
        (ACTOR_ID,),
        True,
    )
    assert "Unresolved actor creation" in str(report["failures"])
    assert read_control(path)["pending_creations"] == 1


@pytest.mark.parametrize(
    "damage",
    [
        {"pending_creations": True},
        {"pending_creations": 1, "world_id": None, "actor_ids": []},
        {"pending_creations": 1, "world_id": "not-an-episode"},
    ],
)
def test_invalid_pending_metadata_is_rejected_before_settings_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: dict[str, object]
) -> None:
    """Best-effort cleanup does not weaken validation of child-supplied journal metadata."""
    world = SettingsWorld()
    path, _ownership, settings = _records(tmp_path, monkeypatch, world, actors=True)
    value = read_control(path)
    write_control(path, value | damage)
    requests = _inline_worker(monkeypatch, world)

    report = script_recovery.cleanup_script_ownership(
        "localhost", 3000, path, -1, 5.0, require_settings=True
    )

    assert report["failures"]
    assert (world.events, requests, settings.pending()) == ([], [], True)


def _assert_quarantined_report(report: dict[str, object]) -> None:
    assert report["settings_restored"] is True
    assert "Unresolved actor creation" in str(report["failures"])
