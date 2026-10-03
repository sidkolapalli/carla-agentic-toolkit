"""Bounded trusted inference, supersession, and deterministic fallback contracts."""

import asyncio
import json
import time
from dataclasses import replace
from typing import TypedDict, Unpack

import pytest

from carla_agentic_toolkit.managed_jev import JevPolicy
from carla_agentic_toolkit.managed_jev_config import TOKEN_RESERVATION, AccountBudget, JevConfig
from carla_agentic_toolkit.managed_jev_questions import MODEL, QUESTION_VERSION, JevReply
from carla_agentic_toolkit.managed_jev_transport import ProviderFailure
from carla_agentic_toolkit.managed_policy import Candidate, DecisionContext, PolicyRequest


def request(*, revision: int = 1, phase: str = "preparing") -> PolicyRequest:
    """Build one request with an explicit phase-appropriate fallback."""
    ids = ("continue", "abort") if phase in {"committed", "settling"} else ("defer", "merge")
    return PolicyRequest(
        DecisionContext(
            "run", "world", 1, revision, 10, f"set-{revision}", 0, phase, time.monotonic() + 5
        ),
        tuple(Candidate(name, 1, 5.0, 1.0, 30) for name in ids),
        '{"gap_m": 25.0}',
    )


def reply(**changes: object) -> JevReply:
    """Return a real-shaped response with unknown token usage preserved."""
    values: dict[str, object] = {
        "model": MODEL,
        "choice": "merge",
        "probabilities": {"defer": 0.2, "merge": 0.8},
        "request_id": "provider-request",
        "input_tokens": None,
        "output_tokens": None,
    }
    values.update(changes)
    return JevReply(**values)


class FakeTransport:
    """Retain a fake client and optionally block its first request."""

    def __init__(self, responses: list[JevReply | Exception]) -> None:
        """Initialize deterministic responses without credentials or network."""
        self.responses = responses
        self.calls = 0
        self.active = 0
        self.maximum_active = 0
        self.closed = False
        self.started = asyncio.Event()
        self.release: asyncio.Event | None = None

    async def infer(self, state_json: str, criteria: dict[str, str]) -> JevReply:
        """Record concurrency while exercising the same immutable query boundary."""
        assert json.loads(state_json)["evidence"] == {"gap_m": 25.0}
        assert criteria
        self.calls += 1
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.started.set()
        try:
            if self.release is not None:
                await self.release.wait()
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        finally:
            self.active -= 1

    async def aclose(self) -> None:
        """Record retained-client closure."""
        self.closed = True


class ConfigChanges(TypedDict, total=False):
    """Typed subset used to vary trusted settings in adversarial tests."""

    timeout_seconds: float
    max_attempts: int
    retry_delay_seconds: float
    max_requests: int


def policy(transport: FakeTransport, **changes: Unpack[ConfigChanges]) -> JevPolicy:
    """Create a policy with a fresh explicit account budget for each test."""
    return JevPolicy(transport, JevConfig(**changes), AccountBudget(10, 10 * TOKEN_RESERVATION))


def test_known_choice_has_versions_and_unknown_usage() -> None:
    """Confidence is recorded without imposing an invented threshold."""
    decision = asyncio.run(policy(FakeTransport([reply()])).choose(request(), "defer"))
    assert (decision.choice_id, decision.source) == ("merge", "jev")
    metadata = json.loads(decision.metadata_json)
    expected = {
        "requested_model": MODEL,
        "returned_model": MODEL,
        "question_version": QUESTION_VERSION,
        "input_tokens": None,
        "request_id": "provider-request",
    }
    assert {key: metadata[key] for key in expected} == expected


@pytest.mark.parametrize(
    "changes",
    [
        {"choice": "invented"},
        {"choice": None},
        {"model": "unexpected"},
        {"probabilities": {"defer": float("nan"), "merge": 1.0}},
        {"probabilities": {"defer": -0.1, "merge": 1.1}},
        {"probabilities": {"defer": 0.2}},
        {"probabilities": {"defer": 0.2, "merge": 0.2}},
        {"input_tokens": -1},
    ],
)
def test_malformed_reply_falls_back(changes: dict[str, object]) -> None:
    """Malformed or mismatched inference never turns into a maneuver."""
    decision = asyncio.run(policy(FakeTransport([reply(**changes)])).choose(request(), "defer"))
    assert decision.choice_id == "defer"
    assert decision.reason == "invalid_response"
    assert decision.source == "fallback"


def test_timeout_and_stop_return_phase_appropriate_fallback() -> None:
    """A blocked provider cannot hold a paced simulation or shutdown open."""

    async def run() -> None:
        transport = FakeTransport([reply()])
        transport.release = asyncio.Event()
        selector = policy(transport, timeout_seconds=0.01)
        timed_out = await selector.choose(request(phase="committed"), "continue")
        assert timed_out.choice_id == "continue"
        assert timed_out.reason == "timeout"
        pending = asyncio.create_task(selector.choose(request(), "defer"))
        await asyncio.sleep(0)
        await selector.aclose()
        stopped = await pending
        assert stopped.choice_id == "defer"
        assert stopped.reason == "cancelled"
        assert transport.closed

    asyncio.run(run())


def test_superseded_world_and_candidate_sets_coalesce() -> None:
    """One outstanding call per actor discards old worlds and skips queued stale work."""

    async def run() -> None:
        transport = FakeTransport([reply(), reply()])
        transport.release = asyncio.Event()
        selector = policy(transport)
        first = asyncio.create_task(selector.choose(request(), "defer"))
        await transport.started.wait()
        second = asyncio.create_task(selector.choose(request(revision=2), "defer"))
        newest_request = request(revision=3)
        newest_request = replace(
            newest_request,
            context=replace(
                newest_request.context,
                world_generation="reloaded",
                maneuver_generation=1,
            ),
        )
        newest = asyncio.create_task(selector.choose(newest_request, "defer"))
        await asyncio.sleep(0)
        transport.release.set()
        old, skipped, accepted = await asyncio.gather(first, second, newest)
        assert old.reason == skipped.reason == "superseded"
        assert accepted.context == newest_request.context
        assert accepted.choice_id == "merge"
        expected_calls = 2
        assert transport.calls == expected_calls
        assert transport.maximum_active == 1
        await selector.aclose()

    asyncio.run(run())


def test_retry_budget_is_shared_across_policies() -> None:
    """Every HTTP attempt consumes the same account-wide conservative reservation."""

    async def run() -> None:
        budget = AccountBudget(1, TOKEN_RESERVATION)
        failed = FakeTransport([ProviderFailure("throttled", retryable=True)])
        first = JevPolicy(failed, JevConfig(max_attempts=2, retry_delay_seconds=0), budget)
        decision = await first.choose(request(), "defer")
        assert decision.reason == "budget_exhausted"
        other = FakeTransport([reply()])
        second = JevPolicy(other, JevConfig(), budget)
        assert (await second.choose(request(), "defer")).reason == "budget_exhausted"
        assert failed.calls == 1
        assert other.calls == 0

    asyncio.run(run())


def test_expired_request_never_calls_provider() -> None:
    """Expired context fails before spending tokens or exposing observations."""
    transport = FakeTransport([reply()])
    captured = request()
    captured = replace(captured, context=replace(captured.context, deadline_monotonic=0))
    result = asyncio.run(policy(transport).choose(captured, "defer"))
    assert result.reason == "expired"
    assert transport.calls == 0


def test_auth_failure_is_sanitized_and_not_retried() -> None:
    """Credentials or response bodies are never copied into diagnostic traces."""
    transport = FakeTransport([ProviderFailure("authentication", retryable=False)])
    result = asyncio.run(policy(transport, max_attempts=2).choose(request(), "defer"))
    assert result.reason == "authentication"
    assert transport.calls == 1


def test_unknown_phase_or_missing_fallback_rejected_before_provider() -> None:
    """Configuration cannot invent a safe fallback outside the validated set."""
    transport = FakeTransport([reply()])
    with pytest.raises(ValueError, match="phase"):
        asyncio.run(policy(transport).choose(request(phase="completed"), "defer"))
    with pytest.raises(ValueError, match="fallback"):
        asyncio.run(policy(transport).choose(request(), "continue"))
    assert transport.calls == 0
