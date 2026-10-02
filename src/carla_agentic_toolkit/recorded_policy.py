"""Strict recorded-response adapter for the trusted managed policy contract."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from carla_agentic_toolkit.managed_policy import PolicyDecision, PolicyRequest

if TYPE_CHECKING:
    from carla_agentic_toolkit.experiment_trace import TraceRead

_CONTEXT_KEYS = (
    "world_generation",
    "actor_id",
    "revision",
    "observation_frame",
    "candidate_set_id",
    "maneuver_generation",
    "phase",
)


class RecordedPolicy:
    """Replay only the provider choice for identical numerical and maneuver evidence."""

    def __init__(self, read: TraceRead) -> None:
        """Load trusted saved evidence without issuing provider requests."""
        from carla_agentic_toolkit.experiment_trace import RecordedDecisionReplay  # noqa: PLC0415

        self._replay = RecordedDecisionReplay(read)

    async def aclose(self) -> None:
        """Complete the idempotent lifecycle of a resource-free recorded policy."""

    async def choose(self, request: PolicyRequest, fallback_id: str) -> PolicyDecision:
        """Require exact scene identity; mismatches fail instead of fabricating a fallback."""
        del fallback_id
        context = request.context
        saved = self._replay.decision_for(
            world_generation=context.world_generation,
            frame=context.observation_frame,
            actor_id=context.actor_id,
            observation=json.loads(request.evidence_json),
            candidates=asdict(request)["candidates"],
        )
        _validate_context(saved.get("context"), asdict(context))
        choice = _validated_choice(saved, request)
        return PolicyDecision(
            context,
            choice,
            "recorded",
            str(saved.get("reason", "Recorded provider response")),
            str(saved.get("metadata_json", "{}")),
        )


def _validate_context(saved: object, requested: dict[str, object]) -> None:
    if not isinstance(saved, dict):
        message = "Recorded-response replay requires saved decision context identity."
        raise TypeError(message)
    if any(saved.get(key) != requested[key] for key in _CONTEXT_KEYS):
        message = "Recorded-response replay maneuver identity mismatch."
        raise ValueError(message)


def _validated_choice(saved: dict[str, Any], request: PolicyRequest) -> str:
    choice = saved.get("choice_id")
    if not isinstance(choice, str) or choice not in {
        item.candidate_id for item in request.candidates
    }:
        message = "Recorded-response replay choice identity is no longer applicable."
        raise ValueError(message)
    return choice
