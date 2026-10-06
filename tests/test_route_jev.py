"""The route extension must send a route question and preserve provider/fallback attribution."""

from __future__ import annotations

import asyncio
import json
import time

from carla_agentic_toolkit.managed_jev import JevPolicy
from carla_agentic_toolkit.managed_jev_config import TOKEN_RESERVATION, AccountBudget, JevConfig
from carla_agentic_toolkit.managed_jev_questions import (
    MODEL,
    ROUTE_INSTRUCTIONS,
    ROUTE_QUESTION_VERSION,
    JevReply,
    question_instructions,
)
from carla_agentic_toolkit.route_policy import build_request
from tests.test_route_driving import observation


class RouteTransport:
    """Observe the semantic boundary without a network, secret, or simulator."""

    async def infer(self, state_json: str, criteria: dict[str, str]) -> JevReply:
        """Return a real-shaped selection after checking the reviewed route question."""
        assert question_instructions(state_json) == ROUTE_INSTRUCTIONS
        assert set(criteria) == {"cruise", "caution", "yield"}
        return JevReply(MODEL, "yield", {"cruise": 0.1, "caution": 0.2, "yield": 0.7})

    async def aclose(self) -> None:
        """Release the resource-free fake."""


def test_route_question_version_and_budget_fallback() -> None:
    """A route run cannot be mislabeled as a merge question or silently bypass its budget."""

    async def run() -> None:
        policy = JevPolicy(
            RouteTransport(), JevConfig(max_requests=1), AccountBudget(2, 2 * TOKEN_RESERVATION)
        )
        request = build_request(
            observation(), 6.0, 45, revision=1, deadline_monotonic=time.monotonic() + 5
        )
        decision = await policy.choose(request, "yield")
        assert (decision.source, decision.choice_id) == ("jev", "yield")
        assert json.loads(decision.metadata_json)["question_version"] == ROUTE_QUESTION_VERSION
        fallback = await policy.choose(request, "yield")
        assert (fallback.source, fallback.choice_id, fallback.reason) == (
            "fallback",
            "yield",
            "budget_exhausted",
        )
        await policy.aclose()

    asyncio.run(run())
