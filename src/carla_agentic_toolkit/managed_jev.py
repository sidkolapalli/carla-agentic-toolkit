"""Bounded nonblocking Jev selection; the supervisor alone validates and actuates."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from carla_agentic_toolkit.managed_jev_budget import PersistentAccountBudget
from carla_agentic_toolkit.managed_jev_config import JevConfig, ReservationBudget
from carla_agentic_toolkit.managed_jev_questions import (
    MODEL,
    SDK_VERSION,
    build_query,
    question_version,
    response_metadata,
    valid_reply,
    validate_fallback,
)
from carla_agentic_toolkit.managed_jev_transport import (
    JevTransport,
    ProviderFailure,
    TypeSafeTransport,
)
from carla_agentic_toolkit.managed_policy import DecisionContext, PolicyDecision, PolicyRequest
from carla_agentic_toolkit.simulator_lease import private_state_root

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.managed_jev_questions import JevReply


@dataclass(slots=True)
class _Attempt:
    request: PolicyRequest
    fallback_id: str
    started: float = field(default_factory=time.monotonic)
    metadata: dict[str, object] = field(default_factory=dict)
    attempts: int = 0

    def decision(self, reason: str, choice: str | None = None) -> PolicyDecision:
        metadata = {
            "requested_model": MODEL,
            "question_version": question_version(self.request.context.phase),
            "returned_question_version": None,
            "sdk_version": SDK_VERSION,
            "request_id": None,
            "returned_model": None,
            "input_tokens": None,
            "output_tokens": None,
            "elapsed_seconds": time.monotonic() - self.started,
            **self.metadata,
            "attempts": self.attempts,
        }
        return PolicyDecision(
            self.request.context,
            choice or self.fallback_id,
            "jev" if choice else "fallback",
            reason,
            json.dumps(metadata, allow_nan=False, separators=(",", ":")),
        )


class JevPolicy:
    """Coalesce per-actor decisions while preserving the exact captured context."""

    def __init__(
        self, transport: JevTransport, config: JevConfig, budget: ReservationBudget
    ) -> None:
        """Retain a trusted client and share the account's atomic reservation budget."""
        self._transport = transport
        self._config = config
        self._budget = budget
        self._locks: dict[int, asyncio.Lock] = {}
        self._latest: dict[int, DecisionContext] = {}
        self._tasks: set[asyncio.Task[PolicyDecision]] = set()
        self._requests = 0
        self._closed = False

    async def choose(self, request: PolicyRequest, fallback_id: str) -> PolicyDecision:
        """Await bounded selection in its own task; paced ticks must not await this task."""
        validate_fallback(request, fallback_id)
        attempt = _Attempt(request, fallback_id)
        if self._closed:
            return attempt.decision("closed")
        self._latest[request.context.actor_id] = request.context
        task = asyncio.create_task(self._choose(attempt))
        self._tasks.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            return attempt.decision("cancelled")
        finally:
            self._tasks.discard(task)

    async def _choose(self, attempt: _Attempt) -> PolicyDecision:
        context = attempt.request.context
        lock = self._locks.setdefault(context.actor_id, asyncio.Lock())
        timeout = min(self._config.timeout_seconds, context.deadline_monotonic - time.monotonic())
        if timeout <= 0:
            return attempt.decision("expired")
        try:
            async with asyncio.timeout(timeout), lock:
                return await self._current(attempt)
        except TimeoutError:
            return attempt.decision("timeout")
        except Exception:  # noqa: BLE001
            return attempt.decision("provider_error")

    async def _current(self, attempt: _Attempt) -> PolicyDecision:
        if self._superseded(attempt):
            return attempt.decision("superseded")
        try:
            query, criteria = build_query(attempt.request)
        except (ValueError, KeyError, TypeError):
            return attempt.decision("invalid_request")
        return await self._infer(attempt, query, criteria)

    async def _infer(
        self, attempt: _Attempt, query: str, criteria: dict[str, str]
    ) -> PolicyDecision:
        for index in range(self._config.max_attempts):
            result = await self._one_attempt(attempt, query, criteria)
            if isinstance(result, PolicyDecision):
                return result
            if not result.retryable or index + 1 == self._config.max_attempts:
                return attempt.decision(result.category)
            await asyncio.sleep(self._config.retry_delay_seconds)
        return attempt.decision("provider_error")

    async def _one_attempt(
        self,
        attempt: _Attempt,
        query: str,
        criteria: dict[str, str],
    ) -> PolicyDecision | ProviderFailure:
        if self._superseded(attempt):
            return attempt.decision("superseded")
        if not self._reserve():
            return attempt.decision("budget_exhausted")
        attempt.attempts += 1
        try:
            reply = await self._transport.infer(query, criteria)
        except ProviderFailure as error:
            attempt.metadata["request_id"] = error.request_id
            return error
        return self._received(attempt, reply)

    def _reserve(self) -> bool:
        if self._requests >= self._config.max_requests or not self._budget.reserve():
            return False
        self._requests += 1
        return True

    def _superseded(self, attempt: _Attempt) -> bool:
        context = attempt.request.context
        return self._latest.get(context.actor_id) != context

    def _received(self, attempt: _Attempt, reply: JevReply) -> PolicyDecision:
        attempt.metadata.update(response_metadata(reply))
        if self._closed:
            return attempt.decision("cancelled")
        if self._superseded(attempt):
            return attempt.decision("superseded")
        if time.monotonic() > attempt.request.context.deadline_monotonic:
            return attempt.decision("expired")
        if not valid_reply(reply, attempt.request):
            return attempt.decision("invalid_response")
        attempt.metadata["probabilities"] = reply.probabilities
        return attempt.decision("selected", str(reply.choice))

    async def aclose(self) -> None:
        """Cancel pending inference, then close the retained client once."""
        if self._closed:
            return
        self._closed = True
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self._transport.aclose()


def create_jev_policy(
    config: JevConfig | None = None, *, state_root: Path | None = None
) -> JevPolicy:
    """Read a trusted server credential; baseline rules need no SDK or API key."""
    resolved = config or JevConfig()
    api_key = os.environ.get("TYPESAFE_API_KEY", "")
    if not api_key.strip():
        msg = "Jev requires TYPESAFE_API_KEY in the trusted server environment."
        raise ValueError(msg)
    budget = PersistentAccountBudget(state_root or private_state_root())
    return JevPolicy(TypeSafeTransport(api_key, resolved.timeout_seconds), resolved, budget)
