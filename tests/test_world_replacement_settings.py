"""Replacement RPC failures cannot authorize restoration against an unknown episode."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.managed_control_io import read_control
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.script_recovery import _cleanup_connected
from carla_agentic_toolkit.script_settings import RunSettings
from tests.test_sync_settings import FakeSettings, FakeWorld

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient

ACTOR_ID = 101


@dataclass
class ReplacementClient:
    """Keep server episode replacement independent of the RPC response reaching the client."""

    world: FakeWorld
    replacement: FakeWorld
    lose_response: bool = False

    def get_world(self) -> FakeWorld:
        """Return the episode that currently exists on the fake server."""
        return self.world

    def load_world(self, map_name: str, *, reset_settings: bool = True) -> FakeWorld:
        """Replace the map and optionally lose the successful server-side response."""
        del map_name, reset_settings
        return self._replace_world()

    def reload_world(self, reset_settings: bool) -> FakeWorld:  # noqa: FBT001
        """Replace the episode while preserving the configured timing values."""
        del reset_settings
        return self._replace_world()

    def _replace_world(self) -> FakeWorld:
        self.world = self.replacement
        if self.lose_response:
            message = "connection lost after replacing world"
            raise RuntimeError(message)
        return self.world


def _prepared_adapter(
    tmp_path: Path, *, lose_response: bool
) -> tuple[PythonCarlaAdapter, ReplacementClient, RunSettings, RunOwnership]:
    world = FakeWorld()
    replacement = FakeWorld(
        id=world.id + 1,
        settings=FakeSettings(synchronous_mode=True, fixed_delta_seconds=0.05),
    )
    client = ReplacementClient(world, replacement, lose_response=lose_response)
    settings = RunSettings(tmp_path / "world-settings.json", require_existing=True)
    settings.initialize()
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((ACTOR_ID,), world_id=world.id)
    adapter = PythonCarlaAdapter(settings_journal=settings)
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    adapter.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)
    return adapter, client, settings, ownership


def _replace(adapter: PythonCarlaAdapter, method: str) -> None:
    operations = {
        "load_world": lambda: adapter.load_world("Town02"),
        "reload_world": lambda: adapter.reload_world(reset_settings=False),
    }
    operations[method]()


@pytest.mark.parametrize("method", ["load_world", "reload_world"])
def test_replacement_applied_before_rpc_failure_retains_unrestored_baseline(
    tmp_path: Path, method: str
) -> None:
    """A changed server episode and lost reply keep both journals quarantined for recovery."""
    adapter, client, settings, ownership = _prepared_adapter(tmp_path, lose_response=True)
    path = tmp_path / "world-settings.json"
    original_record = read_control(path)

    with pytest.raises(RuntimeError, match="connection lost after replacing world"):
        _replace(adapter, method)

    reopened = RunSettings(path, require_existing=True)
    result = _cleanup_connected(adapter, ownership, reopened)
    _assert_fail_closed(result, client.world, ownership, settings)
    assert read_control(path) == original_record


@pytest.mark.parametrize("method", ["load_world", "reload_world"])
def test_unexpected_episode_after_completed_replacement_refuses_restore(
    tmp_path: Path, method: str
) -> None:
    """Completed map replacement binds its returned episode, never a later unrelated world."""
    adapter, client, settings, ownership = _prepared_adapter(tmp_path, lose_response=False)
    _replace(adapter, method)
    path = tmp_path / "world-settings.json"
    rebound = read_control(path)
    assert rebound["world_id"] == client.replacement.id
    client.world = FakeWorld(id=client.replacement.id + 1)
    original = asdict(client.world.settings)

    result = _cleanup_connected(adapter, ownership, RunSettings(path, require_existing=True))

    _assert_fail_closed(result, client.world, ownership, settings)
    assert (asdict(client.world.settings), read_control(path)) == (original, rebound)


def _assert_fail_closed(
    result: dict[str, object], world: FakeWorld, ownership: RunOwnership, settings: RunSettings
) -> None:
    assert (result["settings_restored"], bool(result["failures"])) == (False, True)
    assert "World changed" in str(result["failures"])
    assert (world.applied, ownership.actor_ids(), settings.pending()) == ([], (ACTOR_ID,), True)
