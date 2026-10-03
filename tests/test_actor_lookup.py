"""Explicit known actor IDs remain usable before the cached world list receives a frame."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.actor_runtime import destroy_actor
from carla_agentic_toolkit.adapter import _parent_actor
from carla_agentic_toolkit.experiment_common import actor, sensor_actor

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaWorld

ACTOR_ID = 71


class World:
    """Cached enumeration omits a new actor; explicit-ID RPC can resolve its live handle."""

    def __init__(self) -> None:
        """Track the lookup overload without advancing any simulator frame."""
        self.actor = SimpleNamespace(
            id=ACTOR_ID,
            type_id="sensor.other.gnss",
            attributes={},
            get_transform=lambda: None,
            get_velocity=lambda: None,
            listen=lambda _callback: None,
            stop=lambda: None,
            destroy=lambda: True,
        )
        self.lookups: list[object] = []

    def get_actors(self, actor_ids: list[int] | None = None) -> object:
        """Expose the difference between snapshot enumeration and explicit actor retrieval."""
        self.lookups.append(actor_ids)
        found = self.actor if actor_ids == [ACTOR_ID] else None
        return SimpleNamespace(find=lambda _actor_id: found)

    def tick(self) -> None:
        """Reject hidden ticks in actor lookup."""
        message = "actor resolution must not advance the world"
        raise AssertionError(message)


@pytest.mark.parametrize("lookup", [actor, sensor_actor, _parent_actor])
def test_known_actor_resolves_before_cached_enumeration(
    lookup: Callable[[CarlaWorld, int], object],
) -> None:
    """Vehicle operations, existing sensors, and new attachment parents share one lookup."""
    world = World()
    assert lookup(cast("CarlaWorld", world), ACTOR_ID) is world.actor
    assert world.lookups == [[ACTOR_ID]]


def test_destroy_known_actor_does_not_report_a_cached_false_absence() -> None:
    """Actor cleanup must contact the explicit actor even before its first published frame."""
    world = World()
    result = destroy_actor(cast("CarlaWorld", world), ACTOR_ID)
    assert result.destroyed is True
    assert world.lookups == [[ACTOR_ID]]
