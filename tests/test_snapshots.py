"""Behavior specs for inline run snapshot handling."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from carla_agentic_toolkit.models import JsonObject


def test_run_snapshots_copy_registered_payloads() -> None:
    """A registered snapshot should be retrievable without exposing mutation."""
    snapshots = RunSnapshots()
    payload: JsonObject = {"connected": True, "actor_counts": {"vehicles": 1}}

    snapshots.register_snapshot("carla-snapshot://session/status", payload)
    payload["connected"] = False
    fetched = snapshots.read_snapshot("carla-snapshot://session/status")
    _nested_object(fetched, "actor_counts")["vehicles"] = 99

    assert snapshots.read_snapshot("carla-snapshot://session/status") == {
        "connected": True,
        "actor_counts": {"vehicles": 1},
    }


def test_run_snapshots_reject_unknown_identifiers() -> None:
    """Unknown snapshots should fail explicitly."""
    snapshots = RunSnapshots()

    with pytest.raises(KeyError):
        snapshots.read_snapshot("carla-snapshot://missing")


def _nested_object(payload: JsonObject, key: str) -> JsonObject:
    """Return a nested JSON object from a structured payload."""
    value = payload[key]
    if not isinstance(value, dict):
        msg = f"Expected {key} to contain an object."
        raise TypeError(msg)
    return cast("JsonObject", value)
