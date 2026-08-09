"""Actor names that survive separate sandbox script executions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

import pytest

from carla_agentic_toolkit.actor_registry import ActorRegistry, actor_registry_path
from carla_agentic_toolkit.models import ActorSnapshot, Location, Rotation, Transform
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter

ACTOR_ID: Final = 101


@dataclass
class NamedActorAdapter:
    """Small adapter fake for named-actor liveness checks."""

    actors: tuple[ActorSnapshot, ...]
    host: str = "127.0.0.1"
    port: int = 2000
    timeout: float = 10.0

    def list_actors(self, filter_pattern: str) -> tuple[ActorSnapshot, ...]:
        """Return the current actor inventory."""
        assert filter_pattern == "*"
        return self.actors


def test_actor_name_survives_separate_api_instances(tmp_path: Path) -> None:
    """A later script process should resolve a name written by an earlier one."""
    adapter = NamedActorAdapter((_actor(),))
    path = actor_registry_path(tmp_path, adapter.host, adapter.port)

    named = _api(adapter, path).name_actor("ego", ACTOR_ID)
    resolved = _api(adapter, path).resolve_actor("ego")

    assert named == {"name": "ego", "actor_id": ACTOR_ID}
    assert resolved == named


def test_actor_names_can_be_listed_and_forgotten(tmp_path: Path) -> None:
    """The facade should expose deterministic registry lifecycle operations."""
    adapter = NamedActorAdapter((_actor(),))
    api = _api(adapter, actor_registry_path(tmp_path, adapter.host, adapter.port))
    api.name_actor("ego", ACTOR_ID)

    assert api.list_named_actors() == {"actors": [{"name": "ego", "actor_id": ACTOR_ID}]}
    assert api.forget_actor("ego") == {
        "name": "ego",
        "actor_id": ACTOR_ID,
        "forgotten": True,
    }
    assert api.list_named_actors() == {"actors": []}


def test_resolve_invalidates_an_actor_that_disappeared(tmp_path: Path) -> None:
    """A stale actor ID should never be returned from persistent state."""
    adapter = NamedActorAdapter((_actor(),))
    path = actor_registry_path(tmp_path, adapter.host, adapter.port)
    _api(adapter, path).name_actor("ego", ACTOR_ID)
    adapter.actors = ()

    result = _api(adapter, path).resolve_actor("ego")

    assert result["ok"] is False
    assert result["error_type"] == "resolve_actor_failed"
    assert "no longer exists" in cast("str", result["message"])
    assert ActorRegistry(path).items() == ()


@pytest.mark.parametrize("name", ["", "   ", cast("str", 7)])
def test_actor_name_is_validated(name: str, tmp_path: Path) -> None:
    """Names should be bounded non-empty strings at the script boundary."""
    adapter = NamedActorAdapter((_actor(),))

    result = _api(adapter, actor_registry_path(tmp_path, adapter.host, adapter.port)).name_actor(
        name, ACTOR_ID
    )

    assert result["ok"] is False
    assert result["error_type"] == "name_actor_failed"


def test_missing_actor_name_is_recoverable(tmp_path: Path) -> None:
    """Resolving an unknown conversational name should not crash the script."""
    adapter = NamedActorAdapter((_actor(),))

    result = _api(adapter, actor_registry_path(tmp_path, adapter.host, adapter.port)).resolve_actor(
        "missing"
    )

    assert result["ok"] is False
    assert result["error_type"] == "resolve_actor_failed"


def test_registry_path_is_isolated_by_endpoint(tmp_path: Path) -> None:
    """Separate CARLA endpoints must never share actor aliases."""
    first = actor_registry_path(tmp_path, "127.0.0.1", 2000)
    second = actor_registry_path(tmp_path, "127.0.0.1", 3000)

    ActorRegistry(first).set("ego", ACTOR_ID)

    assert first != second
    assert ActorRegistry(second).items() == ()


def _api(adapter: NamedActorAdapter, registry_path: Path) -> CarlaScriptApi:
    """Build the facade with one persistent registry file."""
    return CarlaScriptApi(
        adapter=cast("PythonCarlaAdapter", adapter),
        snapshots=RunSnapshots(),
        actor_registry=ActorRegistry(registry_path),
    )


def _actor() -> ActorSnapshot:
    """Create one live actor snapshot."""
    return ActorSnapshot(
        actor_id=ACTOR_ID,
        type_id="vehicle.tesla.model3",
        role_name="ego",
        transform=Transform(
            location=Location(x=1.0, y=2.0, z=0.0),
            rotation=Rotation(pitch=0.0, yaw=0.0, roll=0.0),
        ),
        speed_mps=0.0,
        traffic_light_state=None,
    )
