"""Bounded policy request scheduling, contextual validation, and explicit fallback evidence."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.experiment_trace import identity_digest
from carla_agentic_toolkit.managed_decisions import decision_metadata, decision_rejection
from carla_agentic_toolkit.managed_policy import PolicyDecision
from carla_agentic_toolkit.merge_planner import fallback_choice

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.managed_engine import Experiment, Policy
    from carla_agentic_toolkit.managed_policy import PolicyRequest
    from carla_agentic_toolkit.managed_spec import ExperimentSpec
    from carla_agentic_toolkit.merge_planner import MergeObservation


@dataclass(slots=True)
class PendingDecision:
    """Keep the exact requested evidence until its reply is accepted or rejected."""

    request: PolicyRequest
    task: asyncio.Task[PolicyDecision]
    observation_id: str
    failure_reason: str | None = None


class DecisionScheduler:
    """Retain at most one provider task until completion, cancellation, or owner cleanup."""

    def __init__(
        self,
        spec: ExperimentSpec,
        experiment: Experiment,
        policy: Policy,
        event: Callable[[str, dict[str, object]], None],
        should_stop: Callable[[], bool],
    ) -> None:
        """Bind owner evidence and cancellation; acquire no simulator ownership."""
        self.spec, self.experiment, self.policy = spec, experiment, policy
        self.event, self.should_stop = event, should_stop
        self.pending: PendingDecision | None = None
        self.revision = 0

    async def close(self) -> None:
        """Finish pending task cancellation before releasing the retained provider resources."""
        if self.pending is not None:
            self.pending.task.cancel()
            await asyncio.gather(self.pending.task, return_exceptions=True)
            self.pending = None
        await self.policy.aclose()

    async def choose(self, observation: MergeObservation, step: int) -> PolicyDecision | None:
        """Return a currently valid reply or explicit fallback without owning simulator ticks."""
        if self.pending is None and step % self.spec.decision_interval_steps == 0:
            self._request_decision(observation)
        if self.pending is None:
            return None
        if self.spec.timing_mode == "simulation_time":
            await self._await_decision()
        else:
            await asyncio.sleep(0)
        return self._take_decision(observation)

    def _request_decision(self, observation: MergeObservation) -> None:
        experiment = self.experiment
        self.revision += 1
        request = experiment.policy_request(
            observation,
            revision=self.revision,
            deadline_monotonic=time.monotonic() + self.spec.decision_timeout_seconds,
        )
        if request is None:
            return
        observation_id = identity_digest(json.loads(request.evidence_json))
        data: dict[str, object] = {
            **asdict(request),
            "observation_id": observation_id,
            "candidate_set_id": request.context.candidate_set_id,
            "candidates_id": identity_digest(asdict(request)["candidates"]),
        }
        self.event("candidates", data)
        self.event("decision_requested", data)
        policy = self.policy
        task = asyncio.create_task(policy.choose(request, fallback_choice(request.context.phase)))
        self.pending = PendingDecision(request, task, observation_id)

    async def _await_decision(self) -> None:
        pending = cast("PendingDecision", self.pending)
        while not pending.task.done() and not self.should_stop():
            remaining = pending.request.context.deadline_monotonic - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.wait((pending.task,), timeout=min(0.05, remaining))

    def _take_decision(self, observation: MergeObservation) -> PolicyDecision | None:
        pending = cast("PendingDecision", self.pending)
        if self.should_stop():
            return None
        if not pending.task.done():
            return self._pending_fallback(observation, pending)
        self.pending = None
        return self._resolved_decision(observation, pending)

    def _resolved_decision(
        self, observation: MergeObservation, pending: PendingDecision
    ) -> PolicyDecision | None:
        try:
            decision = pending.task.result()
        except asyncio.CancelledError as exc:
            if self.spec.policy == "replay":
                message = "Recorded policy request was cancelled."
                raise RuntimeError(message) from exc
            return self._policy_failure(observation, pending, "provider_cancelled")
        except Exception:
            if self.spec.policy == "replay":
                raise
            return self._policy_failure(observation, pending, "provider_error")
        return self._received_decision(observation, pending, decision)

    def _pending_fallback(
        self, observation: MergeObservation, pending: PendingDecision
    ) -> PolicyDecision | None:
        if time.monotonic() >= pending.request.context.deadline_monotonic:
            if pending.failure_reason is None:
                pending.failure_reason = "deadline_expired"
                pending.task.cancel()
            return self._policy_failure(observation, pending, "deadline_expired")
        return self._fallback(observation, "pending")

    def _policy_failure(
        self, observation: MergeObservation, pending: PendingDecision, reason: str
    ) -> PolicyDecision | None:
        reason = pending.failure_reason or reason
        self.event(
            "validation",
            {
                "accepted": False,
                "reason": reason,
                "request_revision": pending.request.context.revision,
                "observation_id": pending.observation_id,
            },
        )
        return self._fallback(observation, reason)

    def _received_decision(
        self, observation: MergeObservation, pending: PendingDecision, decision: PolicyDecision
    ) -> PolicyDecision | None:
        if pending.failure_reason is not None:
            return self._policy_failure(observation, pending, pending.failure_reason)
        try:
            metadata = decision_metadata(decision)
        except (ValueError, TypeError, RecursionError):
            if self.spec.policy == "replay":
                raise
            return self._policy_failure(observation, pending, "invalid_response")
        return self._validated_decision(observation, pending, decision, metadata)

    def _validated_decision(
        self,
        observation: MergeObservation,
        pending: PendingDecision,
        decision: PolicyDecision,
        metadata: dict[str, object],
    ) -> PolicyDecision | None:
        current = self.experiment.policy_request(
            observation,
            revision=self.revision,
            deadline_monotonic=pending.request.context.deadline_monotonic,
        )
        reason = (
            "no_current_candidates"
            if current is None
            else decision_rejection(
                decision,
                pending.request,
                current,
                now=time.monotonic(),
            )
        )
        self.event(
            "decision_received",
            {
                **asdict(decision),
                "observation_id": pending.observation_id,
                "candidate_set_id": decision.context.candidate_set_id,
                "candidates_id": identity_digest(asdict(pending.request)["candidates"]),
                "latency_seconds": metadata.get("elapsed_seconds"),
                "staleness_frames": observation.frame - decision.context.observation_frame,
                "metadata": metadata,
                "usage": _usage(metadata),
            },
        )
        self.event(
            "validation",
            {
                "accepted": reason is None,
                "reason": reason,
                "requested_choice": decision.choice_id,
                "request_revision": decision.context.revision,
            },
        )
        return decision if reason is None else self._fallback(observation, f"rejected:{reason}")

    def _fallback(self, observation: MergeObservation, reason: str) -> PolicyDecision | None:
        current = self.experiment.policy_request(
            observation,
            revision=self.revision,
            deadline_monotonic=time.monotonic() + self.spec.rpc_timeout_seconds,
        )
        if current is None:
            return None
        return PolicyDecision(
            current.context,
            fallback_choice(current.context.phase),
            "fallback",
            reason,
        )


def _usage(metadata: dict[str, object]) -> dict[str, object] | None:
    if metadata.get("input_tokens") is None or metadata.get("output_tokens") is None:
        return None
    return {"input_tokens": metadata["input_tokens"], "output_tokens": metadata["output_tokens"]}
