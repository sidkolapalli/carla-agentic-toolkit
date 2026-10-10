"""Durable aliases must use server liveness and retain episode-bound identity."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.actor_registry import ActorRegistry, actor_registry_path
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient

ACTOR_ID = 101
WORLD_ID = 7
TYPE_ID = "vehicle.tesla.model3"
ALIAS = "ego"
NAMED = {"name": ALIAS, "actor_id": ACTOR_ID}


@dataclass
class NativeActor:
    """Actor description exists on the server before cached frame publication."""

    id: int = ACTOR_ID
    type_id: object = TYPE_ID
    attributes: dict[str, object] = field(default_factory=lambda: {"role_name": "hero"})

    def get_transform(self) -> SimpleNamespace:
        """Support the old cached inventory path without hiding its defect."""
        return SimpleNamespace(
            location=SimpleNamespace(x=1.0, y=2.0, z=0.5),
            rotation=SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0),
        )

    def get_velocity(self) -> SimpleNamespace:
        """Support the old cached inventory's actor serialization."""
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)

    def destroy(self) -> bool:
        """Reject unexpected alias-driven simulator mutation."""
        message = "alias operations must not destroy actors"
        raise AssertionError(message)


@dataclass
class ActorList:
    """Native-like cached or explicitly requested actor descriptions."""

    actors: tuple[NativeActor, ...]

    def filter(self, wildcard_pattern: str) -> list[NativeActor]:
        """Allow the actual pre-fix facade's cached enumeration to run."""
        assert wildcard_pattern == "*"
        return list(self.actors)

    def find(self, actor_id: int) -> NativeActor | None:
        """Find one requested actor without returning an unrelated handle."""
        return next((actor for actor in self.actors if actor.id == actor_id), None)


@dataclass
class World:
    """Separate authoritative actor lookup from intentionally stale enumeration."""

    actor: NativeActor | None = field(default_factory=NativeActor)
    id: object = WORLD_ID
    cached: bool = True
    cached_actor: NativeActor | None = None
    queries: list[list[int] | None] = field(default_factory=list)
    after_lookup: Callable[[], None] | None = None
    lookup_error: str | None = None

    def get_actors(self, actor_ids: list[int] | None = None) -> ActorList:
        """Query the server only for an explicit ID, as the real overload does."""
        self.queries.append(actor_ids)
        if actor_ids is not None:
            return self._server_actors(actor_ids)
        return self._cached_actors()

    def _cached_actors(self) -> ActorList:
        cached_actor = self.cached_actor or self.actor
        return ActorList((cached_actor,) if self.cached and cached_actor is not None else ())

    def _server_actors(self, actor_ids: list[int]) -> ActorList:
        if self.lookup_error is not None:
            raise RuntimeError(self.lookup_error)
        result = ActorList((self.actor,) if self.actor is not None else ())
        assert actor_ids == [ACTOR_ID]
        if self.after_lookup is not None:
            self.after_lookup()
        return result


@dataclass
class Client:
    """Cached adapter client, never connected to a real simulator."""

    world: World

    def set_timeout(self, _seconds: float) -> None:
        """Accept the existing per-operation RPC budget refresh."""

    def get_world(self) -> World:
        """Return the actual current episode, not an old retained proxy."""
        return self.world


@dataclass
class AliasCase:
    """Real facade and adapter over one endpoint-specific durable registry."""

    world: World
    client: Client
    adapter: PythonCarlaAdapter
    path: Path

    def api(self) -> CarlaScriptApi:
        """Create a new facade/reader to exercise persisted aliases across calls."""
        return CarlaScriptApi(self.adapter, RunSnapshots(), actor_registry=ActorRegistry(self.path))


def _case(tmp_path: Path, *, cached: bool = True) -> AliasCase:
    world = World(cached=cached)
    client = Client(world)
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    path = actor_registry_path(tmp_path, adapter.host, adapter.port)
    return AliasCase(world, client, adapter, path)


def _stored_identity(*, role_name: str | None = "hero") -> dict[str, object]:
    return {
        "actor_id": ACTOR_ID,
        "world_id": WORLD_ID,
        "type_id": TYPE_ID,
        "role_name": role_name,
    }


def _actor(case: AliasCase) -> NativeActor:
    actor = case.world.actor
    assert actor is not None
    return actor


def _assert_error(result: dict[str, object], operation: str, text: str) -> None:
    assert result["ok"] is False
    assert result["error_type"] == f"{operation}_failed"
    assert text in cast("str", result["message"])
    assert result["error"] == result["message"]


def _assert_invalidated(case: AliasCase, result: dict[str, object], text: str) -> None:
    _assert_error(result, "resolve_actor", text)
    assert case.api().list_named_actors() == {"actors": []}
    assert json.loads(case.path.read_text(encoding="utf-8")) == {}


def test_newly_spawned_actor_can_be_named_without_cached_snapshot(tmp_path: Path) -> None:
    """The explicit-ID overload sees a server actor before its first frame."""
    case = _case(tmp_path, cached=False)

    named = case.api().name_actor(ALIAS, ACTOR_ID)

    assert named == NAMED
    assert case.world.queries == [[ACTOR_ID]]
    assert json.loads(case.path.read_text(encoding="utf-8")) == {ALIAS: _stored_identity()}


def test_resolve_checks_server_liveness_even_when_cached_actor_is_missing(tmp_path: Path) -> None:
    """A second facade must not invalidate an alias based on stale enumeration."""
    case = _case(tmp_path)
    assert case.api().name_actor(ALIAS, ACTOR_ID) == NAMED
    case.world.cached = False
    case.world.queries.clear()

    resolved = case.api().resolve_actor(ALIAS)

    assert resolved == NAMED
    assert case.world.queries == [[ACTOR_ID]]


@pytest.mark.parametrize("role_name", ["hero", "", None])
def test_alias_persists_all_identity_fields_and_preserves_public_payload(
    tmp_path: Path, role_name: str | None
) -> None:
    """Empty and missing native role names remain exact, not guessed labels."""
    case = _case(tmp_path)
    actor = _actor(case)
    actor.attributes = {} if role_name is None else {"role_name": role_name}

    named = case.api().name_actor(ALIAS, ACTOR_ID)

    assert named == NAMED
    assert json.loads(case.path.read_text(encoding="utf-8")) == {
        ALIAS: _stored_identity(role_name=role_name)
    }
    assert case.api().resolve_actor(ALIAS) == NAMED


def test_reused_actor_id_in_new_episode_invalidates_alias(tmp_path: Path) -> None:
    """Even identical type and role do not authorize reuse across episodes."""
    case = _case(tmp_path)
    case.api().name_actor(ALIAS, ACTOR_ID)
    case.client.world = World(id=WORLD_ID + 1)

    result = case.api().resolve_actor(ALIAS)

    _assert_invalidated(case, result, "episode")


@pytest.mark.parametrize("field_name", ["type_id", "role_name"])
def test_reused_actor_id_with_different_description_invalidates_alias(
    tmp_path: Path, field_name: str
) -> None:
    """Numeric liveness alone does not establish an actor's stored identity."""
    case = _case(tmp_path)
    case.api().name_actor(ALIAS, ACTOR_ID)
    actor = case.world.actor
    assert actor is not None
    if field_name == "type_id":
        actor.type_id = "vehicle.audi.a2"
    else:
        actor.attributes["role_name"] = "other"

    result = case.api().resolve_actor(ALIAS)

    _assert_invalidated(case, result, field_name)


def test_verified_absence_invalidates_alias_despite_stale_cached_handle(tmp_path: Path) -> None:
    """Explicit server absence must invalidate the stored conversational name."""
    case = _case(tmp_path)
    case.api().name_actor(ALIAS, ACTOR_ID)
    case.world.cached_actor = case.world.actor
    case.world.actor = None

    result = case.api().resolve_actor(ALIAS)

    _assert_invalidated(case, result, "no longer exists")


def test_invalidating_one_alias_does_not_clear_other_aliases(tmp_path: Path) -> None:
    """Only the failed name is removed from the endpoint registry."""
    case = _case(tmp_path)
    case.api().name_actor(ALIAS, ACTOR_ID)
    case.api().name_actor("other", ACTOR_ID)
    case.client.world = World(id=WORLD_ID + 1)

    result = case.api().resolve_actor(ALIAS)

    _assert_error(result, "resolve_actor", "episode")
    assert case.api().list_named_actors() == {"actors": [{"name": "other", "actor_id": ACTOR_ID}]}


@pytest.mark.parametrize(
    "operation", ["name_actor", "resolve_actor", "list_named_actors", "forget_actor"]
)
def test_legacy_integer_registry_is_explicitly_rejected_without_rewrite(
    tmp_path: Path, operation: str
) -> None:
    """An old integer ID supplies no episode evidence and cannot be migrated by guessing."""
    case = _case(tmp_path)
    original = json.dumps({ALIAS: ACTOR_ID})
    case.path.write_text(original, encoding="utf-8")

    result = _call(case, operation)

    _assert_error(result, operation, "legacy")
    assert case.path.read_text(encoding="utf-8") == original


MALFORMED_IDENTITIES = [
    None,
    [],
    True,
    {},
    {**_stored_identity(), "actor_id": True},
    {**_stored_identity(), "actor_id": 0},
    {**_stored_identity(), "world_id": True},
    {**_stored_identity(), "world_id": "7"},
    {**_stored_identity(), "world_id": -1},
    {**_stored_identity(), "type_id": ""},
    {**_stored_identity(), "type_id": None},
    {**_stored_identity(), "role_name": 7},
]


@pytest.mark.parametrize("identity", MALFORMED_IDENTITIES)
def test_malformed_registry_identity_stays_recoverable_and_unmodified(
    tmp_path: Path, identity: object
) -> None:
    """Neither malformed records nor booleans can masquerade as stored identities."""
    case = _case(tmp_path)
    original = json.dumps({ALIAS: identity})
    case.path.write_text(original, encoding="utf-8")

    result = case.api().resolve_actor(ALIAS)

    _assert_error(result, "resolve_actor", "Actor registry")
    assert case.path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("content", [b"{", b"\xff"])
def test_malformed_registry_encoding_and_json_return_structured_errors(
    tmp_path: Path, content: bytes
) -> None:
    """Invalid UTF-8 or JSON cannot escape the recoverable registry error contract."""
    case = _case(tmp_path)
    case.path.write_bytes(content)

    result = case.api().resolve_actor(ALIAS)

    _assert_error(result, "resolve_actor", "Actor registry could not be read")
    assert case.path.read_bytes() == content


@pytest.mark.parametrize("identity", [None, True, "7", -1])
def test_unknown_or_invalid_world_identity_cannot_be_named(
    tmp_path: Path, identity: object
) -> None:
    """Unverified episode identity cannot become a persistent alias."""
    case = _case(tmp_path)
    case.world.id = identity

    result = case.api().name_actor(ALIAS, ACTOR_ID)

    _assert_error(result, "name_actor", "world_id")
    assert not case.path.exists()


@pytest.mark.parametrize(
    ("field_name", "value"), [("type_id", None), ("type_id", 7), ("type_id", ""), ("role_name", 7)]
)
def test_invalid_native_actor_description_cannot_be_named(
    tmp_path: Path, field_name: str, value: object
) -> None:
    """Invalid native descriptions must not be stringified into trusted alias evidence."""
    case = _case(tmp_path)
    actor = case.world.actor
    assert actor is not None
    if field_name == "type_id":
        actor.type_id = value
    else:
        actor.attributes["role_name"] = value

    result = case.api().name_actor(ALIAS, ACTOR_ID)

    _assert_error(result, "name_actor", field_name)
    assert not case.path.exists()


@pytest.mark.parametrize("operation", ["name_actor", "resolve_actor"])
def test_native_lookup_error_is_structured_and_retains_prior_registry(
    tmp_path: Path, operation: str
) -> None:
    """An unavailable server is not evidence that an existing alias disappeared."""
    case = _case(tmp_path)
    case.api().name_actor(ALIAS, ACTOR_ID)
    original = case.path.read_text(encoding="utf-8")
    case.world.lookup_error = "native lookup failed"

    result = _call(case, operation)

    _assert_error(result, operation, "native lookup failed")
    assert case.path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("operation", ["name_actor", "resolve_actor"])
def test_episode_changes_during_lookup_refuse_alias_without_rebinding(
    tmp_path: Path, operation: str
) -> None:
    """The actual client world is rechecked after the server actor description."""
    case = _case(tmp_path)
    case.api().name_actor(ALIAS, ACTOR_ID)
    original = case.path.read_text(encoding="utf-8")
    case.world.after_lookup = lambda: setattr(case.client, "world", World(id=WORLD_ID + 1))

    result = _call(case, operation)

    _assert_error(result, operation, "episode changed")
    assert case.path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("operation", ["name_actor", "resolve_actor"])
def test_wrong_actor_id_lookup_evidence_retains_prior_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """An inconsistent server description is not authority to rebind or invalidate."""
    case = _case(tmp_path)
    assert case.api().name_actor(ALIAS, ACTOR_ID) == NAMED
    original = case.path.read_text(encoding="utf-8")
    monkeypatch.setattr(ActorList, "find", lambda _actors, _actor_id: NativeActor(id=ACTOR_ID + 1))

    result = _call(case, operation)

    _assert_error(result, operation, "does not match requested actor_id")
    assert case.path.read_text(encoding="utf-8") == original


def _call(case: AliasCase, operation: str) -> dict[str, object]:
    api = case.api()
    arguments = {
        "name_actor": (ALIAS, ACTOR_ID),
        "resolve_actor": (ALIAS,),
        "list_named_actors": (),
        "forget_actor": (ALIAS,),
    }
    method = cast("Callable[..., dict[str, object]]", getattr(api, operation))
    return method(*arguments[operation])
