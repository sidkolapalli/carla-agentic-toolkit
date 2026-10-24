"""Truthful frame advancement and partial traffic creation failure contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter as adapter_module
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import DestroyResult, TrafficPopulationRequest
from carla_agentic_toolkit.traffic_runtime import advance_world_once

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaWorld


@pytest.mark.parametrize("method", ["get_settings", "tick", "wait_for_tick"])
def test_required_world_advancement_reports_original_failure(method: str) -> None:
    """A failed settings read, tick, or wait must never claim success."""
    world = Mock()
    world.get_settings.return_value.synchronous_mode = method != "wait_for_tick"
    original = RuntimeError("simulator unavailable")
    getattr(world, method).side_effect = original

    with pytest.raises(CarlaAdapterError, match="simulator unavailable") as failure:
        advance_world_once(cast("CarlaWorld", world))

    assert failure.value.__cause__ is original


@pytest.mark.parametrize("synchronous", [True, False])
def test_world_advancement_reports_observed_frame(*, synchronous: bool) -> None:
    """Both advancement modes must expose the actual returned frame identity."""
    world = Mock()
    world.get_settings.return_value.synchronous_mode = synchronous
    expected_frame = 41
    world.tick.return_value = expected_frame
    world.wait_for_tick.return_value.frame = expected_frame

    assert advance_world_once(cast("CarlaWorld", world)) == expected_frame
    assert world.tick.call_count == int(synchronous)
    assert world.wait_for_tick.call_count == int(not synchronous)


def test_async_wait_without_snapshot_is_not_success() -> None:
    """An absent snapshot is a failed wait, not a completed frame."""
    world = Mock()
    world.get_settings.return_value.synchronous_mode = False
    world.wait_for_tick.return_value = None

    with pytest.raises(CarlaAdapterError):
        advance_world_once(cast("CarlaWorld", world))


def test_failed_population_advancement_cleans_created_actors_and_retains_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-spawn failure must retain ownership information for incomplete cleanup."""
    adapter = PythonCarlaAdapter()
    world = Mock()
    world.get_settings.return_value.synchronous_mode = False
    world.wait_for_tick.side_effect = RuntimeError("lost during wait")
    monkeypatch.setattr(adapter, "_client", Mock())
    monkeypatch.setattr(adapter, "_world", Mock(return_value=world))
    monkeypatch.setattr(adapter, "configure_traffic_manager", Mock())
    monkeypatch.setattr(adapter_module, "traffic_manager", Mock())
    monkeypatch.setattr(
        adapter_module, "populate_traffic_actors", Mock(return_value=([11, 12], []))
    )
    monkeypatch.setattr(adapter, "_world_state", Mock())
    cleanup = Mock(
        return_value=(
            DestroyResult(12, destroyed=True, error=None),
            DestroyResult(11, destroyed=False, error="busy"),
        )
    )
    monkeypatch.setattr(adapter, "destroy_actors", cleanup)

    with pytest.raises(CarlaAdapterError, match="lost during wait") as failure:
        adapter.populate_traffic(request=TrafficPopulationRequest(vehicle_count=2))

    cleanup.assert_called_once_with((12, 11))
    assert failure.value.details["actor_ids"] == [11, 12]
    assert failure.value.details["remaining_actor_ids"] == [11]
    assert failure.value.details["cleanup"] == {
        "attempted_actor_ids": [12, 11],
        "destroyed_actor_ids": [12],
        "failures": [{"actor_id": 11, "error": "busy"}],
    }
