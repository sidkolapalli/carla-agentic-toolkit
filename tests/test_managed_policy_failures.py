"""The frame owner bounds provider work independently and records explicit fallback paths."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import managed_engine, managed_selection
from carla_agentic_toolkit.experiment_trace import load_trace
from carla_agentic_toolkit.managed_policy import PolicyDecision
from carla_agentic_toolkit.managed_selection import DecisionScheduler
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from tests.test_managed_engine import FakeExperiment, PendingPolicy, _patch_engine

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.managed_policy import PolicyRequest
    from carla_agentic_toolkit.managed_session import ManagedSession


class FailingPolicy(PendingPolicy):
    """An adapter can still fail after an await despite normal provider error handling."""

    async def choose(self, request: PolicyRequest, fallback_id: str) -> PolicyDecision:
        """Raise a late provider exception after yielding to the owner."""
        del request, fallback_id
        await asyncio.sleep(0)
        message = "late adapter failure"
        raise RuntimeError(message)


class CancelledPolicy(PendingPolicy):
    """A completed cancelled task is a distinct asyncio BaseException path."""

    async def choose(self, request: PolicyRequest, fallback_id: str) -> PolicyDecision:
        """Cancel the policy task without cancelling the experiment owner."""
        del request, fallback_id
        raise asyncio.CancelledError


class MalformedPolicy(PendingPolicy):
    """A provider response may carry malformed metadata despite a valid selection."""

    async def choose(self, request: PolicyRequest, fallback_id: str) -> PolicyDecision:
        """Return a non-object JSON metadata payload."""
        return PolicyDecision(request.context, fallback_id, "jev", "chosen", "[]")


async def _run(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: PendingPolicy,
    spec: ExperimentSpec,
) -> dict[str, object]:
    _patch_engine(monkeypatch, policy)
    return await asyncio.wait_for(
        managed_engine.run_experiment_async(
            spec,
            "policy-failure",
            state_root=root,
            cancelled=lambda: False,
            publish_status=lambda _value: None,
        ),
        timeout=1.0,
    )


def test_simulation_time_wait_enforces_request_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that never resolves must fall back at the request deadline, not max-wall."""
    spec = ExperimentSpec(policy="jev", decision_timeout_seconds=0.1)
    result = asyncio.run(_run(tmp_path, monkeypatch, PendingPolicy(), spec))
    assert result["state"] == "completed"
    assert "deadline_expired" in _reasons(tmp_path)


def test_paced_owner_cancels_expired_pending_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Continuing frames must also expire an inference request independently of its adapter."""
    spec = ExperimentSpec(policy="jev", timing_mode="paced", decision_timeout_seconds=0.1)
    clock = SimpleNamespace(now=0.0)
    timer = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(managed_engine, "time", timer)
    monkeypatch.setattr(managed_selection, "time", timer)

    async def pace(_owner: managed_engine.ExperimentRun, step: int) -> None:
        # The final fake frame lands exactly on the request deadline. Real wall-clock
        # scheduling can land just before it because request setup takes time.
        clock.now = (step + 1) * spec.fixed_delta_seconds
        await asyncio.sleep(0)

    monkeypatch.setattr(managed_engine.ExperimentRun, "_pace", pace)
    policy = PendingPolicy()
    result = asyncio.run(_run(tmp_path, monkeypatch, policy, spec))
    assert result["state"] == "completed"
    assert "deadline_expired" in _reasons(tmp_path)
    assert policy.entered.is_set()
    assert policy.closed


@pytest.mark.parametrize("policy_type", [FailingPolicy, CancelledPolicy])
def test_late_provider_failure_becomes_explicit_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy_type: type[PendingPolicy],
) -> None:
    """Provider failures and cancelled completed tasks do not abort a healthy local run."""
    result = asyncio.run(_run(tmp_path, monkeypatch, policy_type(), ExperimentSpec(policy="jev")))
    assert result["state"] == "completed"
    assert _reasons(tmp_path) & {"provider_error", "provider_cancelled"}


@pytest.mark.parametrize(
    ("policy_type", "message"),
    [
        (FailingPolicy, "late adapter failure"),
        (CancelledPolicy, "Recorded policy request was cancelled"),
    ],
)
def test_offline_recorded_failure_never_generates_fallback(
    policy_type: type[PendingPolicy],
    message: str,
) -> None:
    """Offline saved-context scheduling propagates missing/cancelled recorded responses."""

    async def replay() -> None:
        experiment = FakeExperiment(
            cast(
                "ManagedSession",
                SimpleNamespace(
                    run_id="saved",
                    world_generation="saved-world",
                ),
            )
        )
        selection = DecisionScheduler(
            ExperimentSpec(policy="replay", replay_run_id="0" * 32),
            experiment,
            policy_type(),
            lambda _kind, _data: None,
            lambda: False,
        )
        try:
            await selection.choose(experiment.observe(SimpleNamespace(frame=100)), 0)
        finally:
            await selection.close()

    with pytest.raises(RuntimeError, match=message):
        asyncio.run(replay())


def test_malformed_provider_metadata_is_a_bounded_explicit_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected metadata shape must not crash actuation or fabricate a valid response."""
    result = asyncio.run(
        _run(tmp_path, monkeypatch, MalformedPolicy(), ExperimentSpec(policy="jev"))
    )
    assert result["state"] == "completed"
    assert "invalid_response" in _reasons(tmp_path)


def _reasons(root: Path) -> set[str]:
    events = load_trace(root / "runs/policy-failure/events.jsonl").events
    return {
        cast("str", event["data"].get("reason"))
        for event in events
        if event["kind"] == "validation"
    }
