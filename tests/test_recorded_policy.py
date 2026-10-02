"""Exact-scene replay contracts for the shared managed policy interface."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit import experiment_trace as trace
from carla_agentic_toolkit.managed_policy import Candidate, DecisionContext, PolicyRequest

if TYPE_CHECKING:
    from pathlib import Path


def _request() -> PolicyRequest:
    context = DecisionContext("old-run", "world", 7, 2, 10, "stable-set", 1, "preparing", 99.0)
    return PolicyRequest(context, (Candidate("yield", 1, 2.0, 1.0, 12),), '{"gap_m":14.125}')


def _record(tmp_path: Path, request: PolicyRequest) -> trace.TraceRead:
    with trace.TraceStore("replay-contract", root=tmp_path) as store:
        store.append(
            "decision_received",
            world_generation="world",
            frame=10,
            actor_id=7,
            data={
                "context": asdict(request.context),
                "choice_id": "yield",
                "source": "jev",
                "reason": "recorded evidence",
                "metadata_json": "{}",
                "observation_id": trace.identity_digest(json.loads(request.evidence_json)),
                "candidate_set_id": request.context.candidate_set_id,
                "candidates_id": trace.identity_digest(asdict(request)["candidates"]),
            },
        )
    return trace.load_trace(store.path)


def test_recorded_policy_preserves_new_request_context_after_exact_match(tmp_path: Path) -> None:
    """Only operational run/deadline identity changes when the recorded scene is exact."""
    request = _request()
    policy = trace.RecordedPolicy(_record(tmp_path, request))
    updated = replace(
        request, context=replace(request.context, run_id="new-run", deadline_monotonic=101.0)
    )

    decision = asyncio.run(policy.choose(updated, fallback_id="yield"))

    assert decision.context == updated.context
    assert decision.choice_id == "yield"
    assert decision.source == "recorded"


def test_recorded_policy_supports_managed_cleanup_lifecycle(tmp_path: Path) -> None:
    """The shared supervisor can close every policy, including resource-free replay."""
    policy = trace.RecordedPolicy(_record(tmp_path, _request()))

    asyncio.run(policy.aclose())
    asyncio.run(policy.aclose())


@pytest.mark.parametrize("change", ["observation", "candidate", "phase"])
def test_recorded_policy_rejects_changed_evidence(tmp_path: Path, change: str) -> None:
    """Recorded responses cannot silently become fixed actions in different situations."""
    request = _request()
    policy = trace.RecordedPolicy(_record(tmp_path, request))
    requests = {
        "observation": replace(request, evidence_json='{"gap_m":0.5}'),
        "candidate": replace(
            request, candidates=(replace(request.candidates[0], expires_frame=13),)
        ),
        "phase": replace(request, context=replace(request.context, phase="settling")),
    }

    with pytest.raises(ValueError, match="identity"):
        asyncio.run(policy.choose(requests[change], fallback_id="yield"))
