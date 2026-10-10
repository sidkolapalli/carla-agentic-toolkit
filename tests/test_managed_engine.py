"""Experiment cancellation and paced ticks stay responsive during inference."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit import managed_engine
from carla_agentic_toolkit.experiment_trace import load_trace, summarize_trace
from carla_agentic_toolkit.managed_policy import (
    Candidate,
    DecisionContext,
    PolicyDecision,
    PolicyRequest,
)
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.merge_planner import ActorObservation, LaneGeometry, MergeObservation
from tests.test_managed_session import FakeWorld

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from carla_agentic_toolkit.experiment_trace import TraceRead
    from carla_agentic_toolkit.managed_session import ManagedSession

FINAL_FRAME = 104
EGO_ID = 2
IMPULSE_MAGNITUDE = 5.0


class PendingPolicy:
    """Keep inference pending until cancellation, with no timing-dependent delay."""

    def __init__(self) -> None:
        """Create observable provider lifecycle events."""
        self.entered = asyncio.Event()
        self.closed = False

    async def choose(self, request: PolicyRequest, fallback_id: str) -> PolicyDecision:
        """Wait for the engine to cancel the request."""
        self.entered.set()
        await asyncio.Future()
        return PolicyDecision(request.context, fallback_id, "fallback", "test")

    async def aclose(self) -> None:
        """Expose retained client closure."""
        self.closed = True


class FakeExperiment:
    """Numerical fixture double with fixed valid candidate identity."""

    def __init__(self, session: ManagedSession) -> None:
        """Retain the fake session for identity generation."""
        self.session = session

    def prepare(self) -> None:
        """Avoid unrelated actor geometry in engine lifecycle tests."""

    def observe(self, snapshot: object) -> MergeObservation:
        """Build all actor data at the same snapshot frame."""
        frame = cast("SimpleNamespace", snapshot).frame
        actor = ActorObservation(1, frame, 0.0, 0.0, 3.0, 0.0, 4.0, 2.0, -1)
        lane = LaneGeometry(
            1,
            -1,
            -2,
            3.5,
            3.5,
            100.0,
            adjacent=True,
            same_direction=True,
            lane_change_allowed=True,
            source_marking="Broken",
            target_marking="Broken",
        )
        return MergeObservation(
            self.session.run_id,
            self.session.world_generation,
            frame,
            frame * 0.05,
            actor,
            replace(actor, actor_id=2, longitudinal_m=30.0, lane_id=-2),
            lane,
            phase="preparing",
        )

    def policy_request(
        self,
        observation: MergeObservation,
        *,
        revision: int,
        deadline_monotonic: float,
    ) -> PolicyRequest:
        """Keep a safe deterministic request identity."""
        context = DecisionContext(
            observation.run_id,
            observation.world_generation,
            1,
            revision,
            observation.frame,
            "candidates",
            0,
            observation.phase,
            deadline_monotonic,
        )
        return PolicyRequest(
            context, (Candidate("defer", -1, 3.0, 3.0, observation.frame + 20),), "{}"
        )

    def advance(
        self,
        observation: MergeObservation,
        decision: PolicyDecision | None = None,
    ) -> dict[str, object]:
        """Terminate a paced run without waiting for an inference response."""
        del decision
        done = observation.frame >= FINAL_FRAME
        return {
            "phase": "completed" if done else "preparing",
            "terminal": done,
            "outcome": {"completed": done, "status": "completed" if done else "running"},
            "controls": [],
            "intervention": {"fallback": True, "reason": "pending"},
        }


def _patch_engine(monkeypatch: pytest.MonkeyPatch, policy: PendingPolicy) -> FakeWorld:
    world = FakeWorld()
    client = SimpleNamespace(
        get_world=lambda: world,
        get_server_version=lambda: "0.9.16-test",
        get_client_version=lambda: "0.9.16-test",
    )
    monkeypatch.setattr(managed_engine, "connect_client", lambda _spec: client)
    monkeypatch.setattr(managed_engine, "build_experiment", FakeExperiment)
    monkeypatch.setattr(managed_engine, "create_policy", lambda _spec, _root: policy)
    return world


def test_simulation_time_cancel_during_inference_restores_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation must interrupt a pending provider and finish all cleanup."""

    async def scenario() -> dict[str, object]:
        policy = PendingPolicy()
        world = _patch_engine(monkeypatch, policy)
        stopped = asyncio.Event()
        task = asyncio.create_task(
            managed_engine.run_experiment_async(
                ExperimentSpec(policy="jev"),
                "test-cancel",
                state_root=tmp_path,
                cancelled=stopped.is_set,
                publish_status=lambda _status: None,
            )
        )
        await asyncio.wait_for(policy.entered.wait(), 2.0)
        stopped.set()
        result = await asyncio.wait_for(task, 2.0)
        assert policy.closed
        assert world.settings["synchronous_mode"] is False
        return result

    result = asyncio.run(scenario())
    assert result["state"] == "cancelled"
    assert cast("dict[str, object]", result["cleanup"])["ok"] is True


def test_paced_run_ticks_while_provider_is_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker's sole tick owner must never await paced inference completion."""

    async def scenario() -> dict[str, object]:
        policy = PendingPolicy()
        _patch_engine(monkeypatch, policy)
        result = await asyncio.wait_for(
            managed_engine.run_experiment_async(
                ExperimentSpec(policy="jev", timing_mode="paced"),
                "test-paced",
                state_root=tmp_path,
                cancelled=lambda: False,
                publish_status=lambda _status: None,
            ),
            2.0,
        )
        assert policy.entered.is_set()
        assert policy.closed
        return result

    result = asyncio.run(scenario())
    assert result["state"] == "completed"
    read = load_trace(tmp_path / "runs/test-paced/events.jsonl")
    _assert_observation_frames(read)


def _assert_observation_frames(read: TraceRead) -> None:
    assert read.complete
    frames = [event["frame"] for event in read.events if event["kind"] == "observation"]
    assert frames == [102, 103, 104]


class CollisionExperiment(FakeExperiment):
    """Emit one delayed event on the ego stream to check actual trace-to-metric wiring."""

    def observe(self, snapshot: object) -> MergeObservation:
        """Provide a nested drain payload as the real sensor backend does."""
        value = super().observe(snapshot)
        if value.frame != FINAL_FRAME:
            return value
        payload: dict[str, object] = {
            "actor_id": 2,
            "sensor_id": 3,
            "kind": "collision",
            "dropped_samples": 0,
            "samples": [
                {
                    "measurement_frame": value.frame - 1,
                    "collision_impulse": {"x": 3.0, "y": 4.0, "z": 0.0},
                }
            ],
        }
        return replace(value, sensors=(payload,))


def test_sensor_measurements_reach_metrics_without_losing_actor_or_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counting a batch envelope cannot replace its physical collision measurements."""
    _patch_engine(monkeypatch, PendingPolicy())
    monkeypatch.setattr(managed_engine, "build_experiment", CollisionExperiment)
    monkeypatch.setattr(
        managed_engine, "create_policy", lambda _spec, _root: managed_engine.RulesPolicy()
    )
    managed_engine.run_experiment(
        ExperimentSpec(),
        "test-collision",
        state_root=tmp_path,
        cancelled=lambda: False,
        publish_status=lambda _status: None,
    )
    read = load_trace(tmp_path / "runs/test-collision/events.jsonl")
    _assert_collision_metrics(read)
    _assert_collision_identity(read)


def _assert_collision_metrics(read: TraceRead) -> None:
    metrics = summarize_trace(read)["metrics"]
    assert metrics["collision_events"] == 1
    assert metrics["collision_impulse_magnitude_sum_kg_mps"] == IMPULSE_MAGNITUDE


def _assert_collision_identity(read: TraceRead) -> None:
    event = next(event for event in read.events if "collision_impulse" in event["data"])
    assert event["actor_id"] == EGO_ID
    assert event["data"]["measurement_frame"] == FINAL_FRAME - 1


def test_frame_budget_does_not_create_an_unobserved_extra_tick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final bounded observation must be the final scheduled simulator frame."""
    world = _patch_engine(monkeypatch, PendingPolicy())
    monkeypatch.setattr(
        managed_engine, "create_policy", lambda _spec, _root: managed_engine.RulesPolicy()
    )
    result = managed_engine.run_experiment(
        ExperimentSpec(max_steps=1),
        "test-frame-budget",
        state_root=tmp_path,
        cancelled=lambda: False,
        publish_status=lambda _status: None,
    )
    assert (result["state"], result["outcome"]) == (
        "completed",
        {"completed": False, "status": "frame_limit"},
    )
    trace = load_trace(tmp_path / "runs/test-frame-budget/events.jsonl")
    _assert_no_extra_tick(world, trace)
    assert summarize_trace(trace)["status"] == "frame_limit"


def _assert_no_extra_tick(world: FakeWorld, trace: TraceRead) -> None:
    setup = next(event for event in trace.events if event["kind"] == "setup_frames")
    setup_data = cast("dict[str, object]", setup["data"])
    assert world.frame == cast("int", setup_data["settled_frame"]) + 1


def test_last_frame_cancellation_is_not_overwritten_by_frame_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation observed after the final decision remains the terminal cause."""
    cancelled = False

    class LastFramePolicy(PendingPolicy):
        async def choose(self, request: PolicyRequest, fallback_id: str) -> PolicyDecision:
            nonlocal cancelled
            cancelled = True
            return PolicyDecision(request.context, fallback_id, "rules", "cancelled-test")

    _patch_engine(monkeypatch, LastFramePolicy())
    result = managed_engine.run_experiment(
        ExperimentSpec(max_steps=1),
        "last-frame-cancel",
        state_root=tmp_path,
        cancelled=lambda: cancelled,
        publish_status=lambda _status: None,
    )
    assert (result["state"], result["outcome"]) == (
        "cancelled",
        {"completed": False, "status": "cancelled"},
    )
