"""Durable managed spawn plans retain returned IDs, never infer them from scene labels."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.experiment_common import transform_dict
from carla_agentic_toolkit.managed_world import SessionInvariantError

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaBlueprint
    from carla_agentic_toolkit.managed_session import ManagedSession


@dataclass(frozen=True, slots=True)
class OwnedActor:
    """Returned creation identity is distinct from assignment and descriptive role."""

    actor_id: int
    type_id: str
    role_name: str
    controller: str
    protected: bool


@dataclass(frozen=True, slots=True)
class SpawnIntent:
    """A native call plan has no actor ownership until its returned ID is recorded."""

    intent_id: int
    world_id: int
    type_id: str
    role_name: str
    controller: str
    protected: bool
    transform: dict[str, object]
    attach_to: int | None
    actor_id: int | None = None

    def owned(self) -> OwnedActor:
        """Construct only the identity associated with an explicitly returned ID."""
        if self.actor_id is None:
            message = "Unresolved spawn has no returned actor ID."
            raise SessionInvariantError(message)
        return OwnedActor(
            self.actor_id, self.type_id, self.role_name, self.controller, self.protected
        )


@dataclass(frozen=True, slots=True)
class CreationOptions:
    """Every managed fixture creation is protected and assigned exactly once."""

    role_name: str
    controller: str
    attach_to: CarlaActor | None = None


class ManagedCreationJournal:
    """Persist intent, returned ID, and completion in separate fail-closed writes."""

    def __init__(self, world_id: int, persist: Callable[[], None]) -> None:
        """Retain only JSON-safe plans and a trusted session journal callback."""
        self.world_id, self.persist = world_id, persist
        self.intents: dict[int, SpawnIntent] = {}
        self.failure: str | None = None
        self.legacy = False
        self._next_intent = 1

    def fields(self) -> dict[str, object]:
        """Never manufacture new coverage for a historical marker."""
        if self.legacy:
            return {}
        return {
            "spawn_journal_version": 1,
            "spawn_intents": [asdict(plan) for plan in self.intents.values()],
        }

    def begin(
        self,
        blueprint: CarlaBlueprint,
        transform: object,
        options: CreationOptions,
    ) -> SpawnIntent:
        """Finish local translation before authorizing any native creation call."""
        if self.failure is not None or self.legacy:
            message = self.failure or "Legacy managed ownership coverage cannot authorize creation."
            raise SessionInvariantError(message)
        plan = SpawnIntent(
            intent_id=self._next_intent,
            world_id=self.world_id,
            type_id=blueprint.id,
            role_name=options.role_name,
            controller=options.controller,
            protected=True,
            transform=transform_dict(transform),
            attach_to=options.attach_to.id if options.attach_to is not None else None,
        )
        self._next_intent += 1
        self.intents[plan.intent_id] = plan
        self._write()
        return plan

    def returned(self, plan: SpawnIntent, actor_id: int) -> SpawnIntent:
        """Retain the raw native ID before reading actor metadata or starting a listener."""
        require_returned_id(actor_id)
        known = replace(plan, actor_id=actor_id)
        self.intents[plan.intent_id] = known
        self._write()
        return known

    def complete(self, intent_id: int) -> None:
        """Resolve a known result only after its originating episode was verified."""
        del self.intents[intent_id]
        self._write()

    def confirmed_destroyed(self, actor_id: int) -> None:
        """Authoritative same-episode deletion may resolve known-ID completion uncertainty."""
        resolved = [key for key, plan in self.intents.items() if plan.actor_id == actor_id]
        if resolved:
            for key in resolved:
                del self.intents[key]
            self._write()

    def failures(self) -> list[str]:
        """Unknown outcomes and legacy gaps require reviewed resolution, not adoption."""
        failures = [self.failure] if self.failure is not None else []
        if self.legacy:
            failures.append(
                "Legacy managed journal lacks pre-spawn ownership coverage; "
                "manual resolution required."
            )
        unknown = sum(plan.actor_id is None for plan in self.intents.values())
        if unknown:
            failures.append(
                f"{unknown} unresolved native spawn outcome(s) have no returned actor ID; "
                "manual resolution required."
            )
        return failures

    def load(self, state: dict[str, object]) -> None:
        """Read existing evidence without changing its coverage or marker bytes."""
        self.legacy = not _supported_version(state.get("spawn_journal_version"))
        if self.legacy:
            return
        values = state.get("spawn_intents")
        if not isinstance(values, list):
            message = "Managed spawn intent evidence is invalid."
            raise SessionInvariantError(message)
        for value in values:
            plan = _read_intent(value, self.world_id)
            if plan.intent_id in self.intents:
                message = "Managed spawn intent identity is ambiguous."
                raise SessionInvariantError(message)
            self.intents[plan.intent_id] = plan

    def known_actors(self) -> tuple[OwnedActor, ...]:
        """Return only durably recorded native IDs for trusted recovery."""
        return tuple(plan.owned() for plan in self.intents.values() if plan.actor_id is not None)

    def _write(self) -> None:
        try:
            self.persist()
        except (OSError, RuntimeError, ValueError) as exc:
            self.failure = f"Managed creation journal failed: {exc}"
            raise


def _supported_version(value: object) -> bool:
    return type(value) is int and value == 1


def _read_intent(value: object, world_id: int) -> SpawnIntent:
    if not isinstance(value, dict):
        message = "Managed spawn intent evidence is invalid."
        raise SessionInvariantError(message)
    try:
        plan = SpawnIntent(**cast("dict[str, Any]", value))
    except TypeError as exc:
        message = "Managed spawn intent fields are invalid."
        raise SessionInvariantError(message) from exc
    _validate_intent(plan, world_id)
    return plan


def _validate_intent(plan: SpawnIntent, world_id: int) -> None:
    if not all(
        (
            _intent_ids_valid(plan, world_id),
            _intent_labels_valid(plan),
            type(plan.protected) is bool,
            isinstance(plan.transform, dict),
        )
    ):
        message = "Managed spawn intent identity or origin is invalid."
        raise SessionInvariantError(message)


def _intent_ids_valid(plan: SpawnIntent, world_id: int) -> bool:
    valid_ids = all(_positive_id(value) for value in (plan.intent_id, plan.world_id))
    valid_optional = all(_optional_id(value) for value in (plan.actor_id, plan.attach_to))
    return all((valid_ids, valid_optional, plan.world_id == world_id))


def _intent_labels_valid(plan: SpawnIntent) -> bool:
    return all(
        isinstance(value, str) and value
        for value in (plan.type_id, plan.role_name, plan.controller)
    )


def _positive_id(value: object) -> bool:
    return type(value) is int and value > 0


def _optional_id(value: object) -> bool:
    return value is None or _positive_id(value)


def require_returned_id(value: object) -> int:
    """Native malformed IDs cannot be converted into guessed deletion authority."""
    if not _positive_id(value):
        message = "Native spawn did not return a valid actor ID."
        raise SessionInvariantError(message)
    return cast("int", value)


def recover_owned(
    state: dict[str, object], journal: ManagedCreationJournal
) -> dict[int, OwnedActor]:
    """Merge durably returned IDs only, refusing incompatible evidence for one ID."""
    values = cast("list[dict[str, Any]]", state["actors"])
    owned = {actor.actor_id: actor for actor in (OwnedActor(**item) for item in values)}
    for actor in journal.known_actors():
        _merge_returned(owned, actor)
    return owned


def _merge_returned(owned: dict[int, OwnedActor], actor: OwnedActor) -> None:
    if actor.actor_id in owned and owned[actor.actor_id] != actor:
        message = "Managed journal contains conflicting returned actor identities."
        raise SessionInvariantError(message)
    owned[actor.actor_id] = actor


def spawn_managed(
    session: ManagedSession,
    blueprint: CarlaBlueprint,
    transform: object,
    options: CreationOptions,
) -> CarlaActor:
    """Journal a configured native call and each raw returned ID before later setup."""
    session.assert_current()
    journal = session.creation
    plan = journal.begin(blueprint, transform, options)
    session.assert_current()
    parent = {"attach_to": options.attach_to} if options.attach_to is not None else {}
    actor = session.world.spawn_actor(blueprint, transform, **parent)
    actor_id = require_returned_id(actor.id)
    session.record_returned(actor, replace(plan, actor_id=actor_id).owned())
    journal.returned(plan, actor_id)
    if actor.type_id != plan.type_id or actor.attributes.get("role_name", "") != plan.role_name:
        message = "Native actor identity differs from its journalled creation plan."
        raise SessionInvariantError(message)
    session.assert_current()
    journal.complete(plan.intent_id)
    return actor
