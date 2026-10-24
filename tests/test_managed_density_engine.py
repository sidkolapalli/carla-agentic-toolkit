"""The engine creates background traffic only after its reviewed fixture setup."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit import experiment_replay, managed_engine
from carla_agentic_toolkit.experiment_trace import load_trace
from tests.managed_density_fakes import DensityClient, DensityWorld, density_spec, unused_port
from tests.managed_density_support import _destroy_batch
from tests.test_managed_engine import FakeExperiment

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

TARGET_COUNT = 100
WORK_BUDGET = 4


class DensityExperiment(FakeExperiment):
    """Mark the last fixture setup boundary without owning a separate clock."""

    def prepare(self) -> None:
        """Log fixture completion before the engine enables density."""
        cast("DensityWorld", self.session.world).calls.append(("fixture_prepared",))


def test_engine_starts_density_after_fixture_and_records_acknowledged_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabled engine traces distinguish the target from bounded acknowledged work."""
    world = DensityWorld()
    client = DensityClient(world)
    spec = density_spec(unused_port(), count=TARGET_COUNT)
    object.__setattr__(spec, "max_steps", 2)
    monkeypatch.setattr(managed_engine, "connect_client", lambda _spec: client)
    monkeypatch.setattr(
        managed_engine, "create_policy", lambda *_args: managed_engine.RulesPolicy()
    )
    monkeypatch.setattr(managed_engine, "build_experiment", DensityExperiment)
    monkeypatch.setattr(experiment_replay, "apply_batch", _destroy_batch)
    try:
        result = managed_engine.run_experiment(
            spec,
            "density-engine",
            state_root=tmp_path,
            cancelled=lambda: False,
            publish_status=lambda _status: None,
        )
        assert cast("dict[str, object]", result["cleanup"])["ok"] is True
        assert (
            world.calls.index(("fixture_prepared",))
            < world.calls.index(("tm_mode", True))
            < world.calls.index(("spawn",))
        )
        _assert_density_trace(tmp_path)
    finally:
        client.release_listener()


def _assert_density_trace(tmp_path: Path) -> None:
    records = _density_records(tmp_path)
    assert records[0]["configured_vehicle_count"] == TARGET_COUNT
    assert records[0]["acknowledged_vehicle_count"] == WORK_BUDGET
    assert records[0]["max_spawn_attempts_per_boundary"] == WORK_BUDGET
    assert records[-1]["acknowledged_vehicle_count"] == WORK_BUDGET * 3


def _density_records(tmp_path: Path) -> list[dict[str, object]]:
    read = load_trace(tmp_path / "runs/density-engine/events.jsonl")
    return [
        cast("dict[str, object]", event["data"])
        for event in read.events
        if event["kind"] == "background_density"
    ]
