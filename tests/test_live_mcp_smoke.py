"""Behavior specs for the opt-in live MCP smoke harness."""

from __future__ import annotations

import pytest

from scripts.live_mcp_smoke import _mutation_script, _parse_args


def test_live_smoke_requires_explicit_confirmation() -> None:
    """A live simulator mutation must never run by default."""
    with pytest.raises(SystemExit):
        _parse_args([])


def test_live_smoke_script_tags_and_cleans_only_its_actors() -> None:
    """The generated script should own cleanup through its unique role tag."""
    code = _mutation_script("carla-agentic-toolkit-smoke-test", drive_seconds=8.0)

    assert all(
        marker in code
        for marker in (
            "carla-agentic-toolkit-smoke-test",
            "finally:",
            "api.destroy_actors(actor_ids)",
            "api.set_weather(weather_before)",
            "api.watch_actor(actor_id, seconds=8.0)",
            '"desired_speed": 8.0',
            "publish=True",
        )
    )
