"""Route exposure validity cannot erase independent outcomes or authoritative cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit import experiment_replay, managed_engine
from carla_agentic_toolkit.experiment_trace import load_trace, summarize_trace
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from tests.test_managed_engine import FakeExperiment, PendingPolicy, _patch_engine
from tests.test_managed_session import _apply_destroy_batch

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from carla_agentic_toolkit.carla_protocols import CarlaActor
    from carla_agentic_toolkit.merge_planner import MergeObservation
    from tests.test_managed_session import FakeWorld

FIRST_RUN_FRAME = 102


class HazardExperiment(FakeExperiment):
    """A fixture owns one native-like actor and reports explicit invalid exposure."""

    def prepare(self) -> None:
        """Register the native-like creation with the real session's ownership journal."""
        actor = SimpleNamespace(
            id=11, type_id="vehicle.test", attributes={"role_name": "managed:test:hazard"}
        )
        self.session.own(cast("CarlaActor", actor), controller="route:hazard")

    def final_summary(self, *, termination: str) -> dict[str, object]:
        """Report fixture validity without replacing the ego route or cleanup result."""
        return {
            "hazard_trial": {
                "valid": False,
                "status": "invalid",
                "reason": "lane_entry_not_observed",
                "deadline_seconds": 8.0,
                "termination": termination,
            }
        }

    def advance(self, observation: MergeObservation, decision: object = None) -> dict[str, object]:
        """Finish the ego route independently of the fixture's hazard classification."""
        del decision
        result = super().advance(observation)
        if result["terminal"]:
            result["outcome"] = {"completed": True, "status": "route_completed"}
        return result


class FailedObservation(HazardExperiment):
    """A native observation error interrupts the run after actor creation."""

    def observe(self, snapshot: object) -> MergeObservation:
        """Raise a native-like transport failure after preparation created the actor."""
        del snapshot
        message = "snapshot transport failed"
        raise RuntimeError(message)


class ValidHazardExperiment(HazardExperiment):
    """An explicitly measured successful trial keeps normal run success."""

    def final_summary(self, *, termination: str) -> dict[str, object]:
        """Supply affirmative measured validity as a success control for the final gate."""
        return {"hazard_trial": {"valid": True, "status": "achieved", "termination": termination}}


@dataclass(frozen=True)
class RunCase:
    """One engine lifecycle scenario, independent of measured-geometry unit fixtures."""

    max_steps: int = 100
    batch_error: str = ""
    termination: str = ""
    fixture: type[HazardExperiment] = HazardExperiment


DEFAULT_CASE = RunCase()


def _run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: RunCase = DEFAULT_CASE
) -> tuple[dict[str, Any], FakeWorld]:
    world = _patch_engine(monkeypatch, PendingPolicy())
    world.batch_error = case.batch_error
    monkeypatch.setattr(managed_engine, "build_experiment", case.fixture)
    monkeypatch.setattr(
        managed_engine, "create_policy", lambda _spec, _root: managed_engine.RulesPolicy()
    )
    monkeypatch.setattr(experiment_replay, "apply_batch", _apply_destroy_batch)
    result = managed_engine.run_experiment(
        ExperimentSpec(max_steps=case.max_steps),
        "hazard-final",
        state_root=tmp_path,
        cancelled=lambda: case.termination == "cancelled" and world.frame >= FIRST_RUN_FRAME,
        publish_status=lambda _status: None,
    )
    return result, world


def test_invalid_hazard_retains_route_completion_and_verified_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid trial is not nominal success, nor is it an infrastructure failure."""
    result, world = _run(tmp_path, monkeypatch)
    _assert_independent_route_completion(result)
    assert result["ok"] is False
    assert result["error"] is None
    _assert_cleaned_actor(result, world)
    assert result["hazard_trial"]["termination"] == "route_completed"


def _assert_independent_route_completion(result: dict[str, Any]) -> None:
    assert result["outcome"] == {"completed": True, "status": "route_completed"}
    assert result["state"] == "completed"


def _assert_cleaned_actor(result: dict[str, Any], world: FakeWorld) -> None:
    assert result["cleanup"]["ok"] is True
    assert world.destroyed == [11]


def test_frame_limit_finalizes_trial_without_extra_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame budget retains its outcome and emits the fixture's final validity evidence."""
    result, world = _run(tmp_path, monkeypatch, RunCase(max_steps=1))
    assert result["outcome"] == {"completed": False, "status": "frame_limit"}
    assert result["hazard_trial"]["termination"] == "frame_limit"
    assert cast("dict[str, object]", result["cleanup"])["ok"] is True
    assert world.frame == FIRST_RUN_FRAME


def test_cleanup_failure_preserves_trial_reason_and_infrastructure_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed actor deletion is still dirty even when the hazard was already invalid."""
    result, world = _run(tmp_path, monkeypatch, RunCase(batch_error="server busy"))
    assert (result["state"], result["error"]) == ("failed", "Experiment cleanup did not complete.")
    assert result["outcome"] == {"completed": True, "status": "route_completed"}
    assert result["hazard_trial"]["reason"] == "lane_entry_not_observed"
    _assert_failed_cleanup_restores_settings(result, world)


def _assert_failed_cleanup_restores_settings(result: dict[str, Any], world: FakeWorld) -> None:
    assert result["cleanup"]["ok"] is False
    assert world.settings["synchronous_mode"] is False


def test_final_trial_reaches_trace_metrics_without_relabeling_route_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final explicit fixture summary is saved before trace reports are derived."""
    result, _world = _run(tmp_path, monkeypatch)
    trace = load_trace(tmp_path / "runs/hazard-final/events.jsonl")
    summary = next(event for event in trace.events if event["kind"] == "fixture_summary")
    assert summary["data"]["hazard_trial"] == result["hazard_trial"]
    assert summarize_trace(trace)["metrics"]["completed"] is True


def test_cancelled_run_retains_cause_trial_and_cleaned_actor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation between observation and actuation still finalizes physical evidence."""
    result, world = _run(tmp_path, monkeypatch, RunCase(termination="cancelled"))
    assert result["state"] == "cancelled"
    assert result["outcome"] == {"completed": False, "status": "cancelled"}
    assert result["hazard_trial"]["termination"] == "cancelled"
    _assert_cleaned_actor(result, world)


def test_native_error_retains_infrastructure_failure_trial_and_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An observation failure is not rewritten as an ordinary invalid hazard outcome."""
    result, world = _run(tmp_path, monkeypatch, RunCase(fixture=FailedObservation))
    assert (result["state"], result["error"]) == (
        "failed",
        "RuntimeError: snapshot transport failed",
    )
    assert result["hazard_trial"]["termination"] == "failed"
    _assert_cleaned_actor(result, world)


def test_verified_valid_trial_preserves_nominal_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trial gate does not reject measured valid exposure or change the route outcome."""
    result, _world = _run(tmp_path, monkeypatch, RunCase(fixture=ValidHazardExperiment))
    assert result["ok"] is True
    assert result["outcome"] == {"completed": True, "status": "route_completed"}
    assert result["hazard_trial"]["valid"] is True
