"""Discovery distinguishes native equivalents from toolkit/composite workflows."""

from __future__ import annotations

from typing import cast

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots


@pytest.mark.parametrize(
    ("method", "equivalent"),
    [
        ("record_episode", "carla.Client.start_recorder"),
        ("stop_recording", "carla.Client.stop_recorder"),
        ("query_recording_collisions", "carla.Client.show_recorder_collisions"),
        ("get_waypoint", "carla.Map.get_waypoint"),
        ("attach_sensor", "carla.World.spawn_actor"),
        ("apply_batch", "carla.Client.apply_batch_sync"),
        ("set_actor_transform", "carla.Actor.set_transform"),
        ("set_weather", "carla.World.set_weather"),
        ("follow_waypoints", None),
        ("health_check", None),
    ],
)
def test_describe_api_has_truthful_native_equivalent(method: str, equivalent: str | None) -> None:
    """The catalog names the actual operation, never a different route or batch API."""
    snapshots = RunSnapshots()
    api = CarlaScriptApi(PythonCarlaAdapter(), snapshots)
    catalog = api.describe_api()
    methods = cast("dict[str, dict[str, object]]", catalog["methods"])
    assert methods[method]["carla_equivalent"] == equivalent
    assert snapshots.read_snapshot("carla-snapshot://api") == catalog


def test_every_public_catalog_entry_exposes_equivalence_without_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absence of a one-to-one CARLA equivalent is explicit null, not a missing field."""
    adapter = PythonCarlaAdapter()

    def forbidden() -> None:
        message = "API discovery must not connect to CARLA"
        raise AssertionError(message)

    monkeypatch.setattr(adapter, "_client", forbidden)
    api = CarlaScriptApi(adapter, RunSnapshots())
    methods = cast("dict[str, dict[str, object]]", api.describe_api()["methods"])
    assert methods
    assert all("carla_equivalent" in entry for entry in methods.values())
