"""Explicit sole-owner operations must never advance the world behind the owner."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter as adapter_module
from carla_agentic_toolkit import experiment_replay
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import UnsupportedFeatureError
from carla_agentic_toolkit.snapshots import RunSnapshots
from carla_agentic_toolkit.tool_inputs import (
    parse_autopilot_request,
    parse_traffic_population_request,
)
from tests.api_helpers import build_api

if TYPE_CHECKING:
    from pathlib import Path


def _adapter(monkeypatch: pytest.MonkeyPatch) -> tuple[PythonCarlaAdapter, Mock]:
    adapter = PythonCarlaAdapter()
    world = Mock()
    world.get_settings.return_value.synchronous_mode = True
    world.tick.return_value = 4
    monkeypatch.setattr(adapter, "_client", Mock())
    monkeypatch.setattr(adapter, "_world", Mock(return_value=world))
    monkeypatch.setattr(adapter, "configure_traffic_manager", Mock())
    monkeypatch.setattr(adapter_module, "traffic_manager", Mock())
    monkeypatch.setattr(adapter_module, "populate_traffic_actors", Mock(return_value=([7], [])))
    monkeypatch.setattr(adapter_module, "set_actor_autopilot", Mock(return_value=([7], [])))
    monkeypatch.setattr(adapter_module, "world_state", Mock())
    return adapter, world


@pytest.mark.parametrize("operation", ["populate", "autopilot"])
def test_owner_can_mutate_traffic_without_advancement(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """The JSON facade request flag survives parsing and prevents every tick or wait."""
    adapter, world = _adapter(monkeypatch)
    if operation == "populate":
        adapter.populate_traffic(
            request=parse_traffic_population_request({"vehicle_count": 1, "advance_world": False})
        )
    else:
        adapter.set_autopilot(
            request=parse_autopilot_request({"actor_ids": [7], "advance_world": False})
        )

    world.tick.assert_not_called()
    world.wait_for_tick.assert_not_called()


@pytest.mark.parametrize("operation", ["populate", "autopilot"])
def test_finite_traffic_operations_keep_default_advancement(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Existing finite scripts retain one required advancement by default."""
    adapter, world = _adapter(monkeypatch)
    if operation == "populate":
        adapter.populate_traffic(request=parse_traffic_population_request({"vehicle_count": 1}))
    else:
        adapter.set_autopilot(request=parse_autopilot_request({"actor_ids": [7]}))

    world.tick.assert_called_once()


@pytest.mark.parametrize("do_tick", [False, True])
def test_batch_passes_explicit_tick_ownership(
    monkeypatch: pytest.MonkeyPatch, *, do_tick: bool
) -> None:
    """The actual CARLA batch call receives the requested do_tick flag."""
    client = Mock()
    client.apply_batch_sync.return_value = []
    monkeypatch.setattr(experiment_replay, "import_module", Mock())

    experiment_replay.apply_batch(client, [], do_tick=do_tick)

    client.apply_batch_sync.assert_called_once_with([], do_tick=do_tick)


def test_batch_facade_preserves_non_ticking_request() -> None:
    """A script API caller can opt into the same explicit owner-controlled batch."""
    adapter = Mock()
    adapter.apply_batch.return_value = {"responses": []}

    build_api(adapter, RunSnapshots()).apply_batch([], do_tick=False)

    adapter.apply_batch.assert_called_once_with([], do_tick=False)


def test_replay_refuses_unsupported_no_tick_before_mutation(tmp_path: Path) -> None:
    """CARLA's installed replay API has no do_tick control and must fail closed."""
    client = Mock()
    request = experiment_replay.ReplayRequest(
        tmp_path / "record.log", 0.0, 0.0, 0, replay_sensors=False, do_tick=False
    )

    with pytest.raises(UnsupportedFeatureError, match="non-ticking"):
        experiment_replay.replay_recording(client, request)

    client.replay_file.assert_not_called()
