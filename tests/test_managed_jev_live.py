"""Explicitly configured live provider smoke test, never part of offline CI."""

import asyncio
import os
import time
from pathlib import Path

import pytest

from carla_agentic_toolkit.managed_jev import create_jev_policy
from carla_agentic_toolkit.managed_jev_config import JevConfig
from carla_agentic_toolkit.managed_policy import Candidate, DecisionContext, PolicyRequest


@pytest.mark.skipif(
    os.environ.get("CARLA_AGENTIC_TOOLKIT_JEV_LIVE") != "1",
    reason="Requires explicit live provider opt-in and trusted TYPESAFE_API_KEY",
)
def test_configured_live_choice(tmp_path: Path) -> None:
    """Validate one synthetic choice against the pinned provider, with bounded spend."""

    async def run() -> None:
        selector = create_jev_policy(
            JevConfig(timeout_seconds=10, max_requests=1), state_root=tmp_path
        )
        context = DecisionContext(
            "live-smoke",
            "synthetic",
            1,
            1,
            10,
            "synthetic-set",
            0,
            "preparing",
            time.monotonic() + 10,
        )
        request = PolicyRequest(
            context,
            (
                Candidate("defer", 1, 5, 1, 30),
                Candidate("merge", 2, 5, 1, 30),
            ),
            '{"gap_m":30,"relative_speed_mps":0}',
        )
        try:
            result = await selector.choose(request, "defer")
            assert result.source == "jev", result.reason
            assert result.choice_id in {"defer", "merge"}
        finally:
            await selector.aclose()

    asyncio.run(run())
