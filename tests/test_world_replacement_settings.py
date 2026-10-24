"""Replacement RPC failures cannot authorize restoration against an unknown episode."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.managed_control_io import read_control
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.script_recovery import _cleanup_connected
from carla_agentic_toolkit.script_settings import RunSettings
from tests.test_sync_settings import FakeSettings, FakeWorld

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld

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
    _assert_fail_closed(result, client.world, ownership, settings, "settings differ")
    assert read_control(path) == original_record


@pytest.mark.parametrize("method", ["load_world", "reload_world"])
def test_unrelated_world_already_at_baseline_is_clean_without_mutation(
    tmp_path: Path, method: str
) -> None:
    """A later replacement is never mutated; it is clean only because it holds the baseline."""
    adapter, client, settings, ownership = _prepared_adapter(tmp_path, lose_response=False)
    _replace(adapter, method)
    path = tmp_path / "world-settings.json"
    assert read_control(path)["world_id"] == client.replacement.id
    client.world = FakeWorld(id=client.replacement.id + 1)
    original = asdict(client.world.settings)

    result = _cleanup_connected(adapter, ownership, RunSettings(path, require_existing=True))

    assert (result["settings_restored"], result["failures"]) == (True, [])
    assert (client.world.applied, asdict(client.world.settings)) == ([], original)
    assert (ownership.actor_ids(), settings.pending()) == ((), False)


def test_replaced_world_with_a_journaled_tm_target_stays_dirty(tmp_path: Path) -> None:
    """TM globals have no readback, so a replacement cannot prove they were restored."""
    adapter, client, settings, ownership = _prepared_adapter(tmp_path, lose_response=False)
    world = cast("CarlaWorld", client.world)
    settings.capture_traffic_manager_setting(world, 8000, setting="seed", value=1)
    client.world = FakeWorld(id=client.world.id + 2)
    reopened = RunSettings(tmp_path / "world-settings.json", require_existing=True)

    result = _cleanup_connected(adapter, ownership, reopened, allowed_ports=(8000,))

    _assert_fail_closed(result, client.world, ownership, settings, "Traffic Manager targets")


@pytest.mark.parametrize("method", ["load_world", "reload_world"])
def test_completed_replacement_rebinds_settings_before_a_later_deadline_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """A deadline after the map RPC must not leave the journal bound to the replaced world."""
    adapter, client, _settings, _ownership = _prepared_adapter(tmp_path, lose_response=False)

    def expire_after_replacement(_client: object) -> None:
        if client.world is client.replacement:
            message = "Execution deadline expired before a native CARLA request."
            raise CarlaAdapterError(message)

    monkeypatch.setattr(adapter, "configure_rpc_timeout", expire_after_replacement)
    with pytest.raises(CarlaAdapterError, match="deadline"):
        _replace(adapter, method)
    assert read_control(tmp_path / "world-settings.json")["world_id"] == client.replacement.id


def _assert_fail_closed(
    result: dict[str, object],
    world: FakeWorld,
    ownership: RunOwnership,
    settings: RunSettings,
    reason: str,
) -> None:
    assert (result["settings_restored"], bool(result["failures"])) == (False, True)
    assert reason in str(result["failures"])
    assert (world.applied, ownership.actor_ids(), settings.pending()) == ([], (ACTOR_ID,), True)
