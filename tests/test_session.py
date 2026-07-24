"""Behavior specs for CARLA MCP session resource handling."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from carla_mcp.session import CarlaSession

if TYPE_CHECKING:
    from carla_mcp.models import JsonObject


def test_session_registers_read_only_resource_payloads() -> None:
    """A registered resource should be retrievable without exposing mutation."""
    session = CarlaSession()
    payload: JsonObject = {"connected": True, "actor_counts": {"vehicles": 1}}

    session.register_resource("carla://session/status", payload)
    payload["connected"] = False
    fetched = session.read_resource("carla://session/status")
    _nested_object(fetched, "actor_counts")["vehicles"] = 99

    assert session.read_resource("carla://session/status") == {
        "connected": True,
        "actor_counts": {"vehicles": 1},
    }


def test_session_rejects_unknown_resources() -> None:
    """Unknown resources should fail explicitly."""
    session = CarlaSession()

    with pytest.raises(KeyError):
        session.read_resource("carla://missing")


def _nested_object(payload: JsonObject, key: str) -> JsonObject:
    """Return a nested JSON object from a structured payload."""
    value = payload[key]
    if not isinstance(value, dict):
        msg = f"Expected {key} to contain an object."
        raise TypeError(msg)
    return cast("JsonObject", value)
