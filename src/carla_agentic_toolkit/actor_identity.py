"""Validated episode-bound actor descriptions for persistent conversational aliases."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.actor_runtime import actor_by_id
from carla_agentic_toolkit.errors import ActorRegistryError

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient

IDENTITY_FIELDS = frozenset({"actor_id", "world_id", "type_id", "role_name"})


@dataclass(frozen=True)
class ActorIdentity:
    """Server actor description and originating episode, without ownership authority."""

    actor_id: int
    world_id: int
    type_id: str
    role_name: str | None

    def to_dict(self) -> dict[str, object]:
        """Return the durable alias record, retaining an absent role as null."""
        return {
            "actor_id": self.actor_id,
            "world_id": self.world_id,
            "type_id": self.type_id,
            "role_name": self.role_name,
        }


def identity_from_value(value: object) -> ActorIdentity:
    """Validate every stored field without deriving missing episode or role evidence."""
    if not isinstance(value, dict) or value.keys() != IDENTITY_FIELDS:
        message = "Actor identity requires actor_id, world_id, type_id, and role_name."
        raise ActorRegistryError(message)
    fields = cast("dict[str, object]", value)
    return ActorIdentity(
        actor_id=_identity_integer(fields["actor_id"], "actor_id", minimum=1),
        world_id=_identity_integer(fields["world_id"], "world_id", minimum=0),
        type_id=_identity_type(fields["type_id"]),
        role_name=_identity_role(fields["role_name"]),
    )


def read_actor_identity(client: CarlaClient, actor_id: int) -> ActorIdentity | None:
    """Query one server actor and verify the actual episode remained stable."""
    try:
        world = client.get_world()
        world_id = _identity_integer(getattr(world, "id", None), "world_id", minimum=0)
        actor = actor_by_id(world, actor_id)
        identity = _native_identity(actor, world_id) if actor is not None else None
        current_id = _identity_integer(
            getattr(client.get_world(), "id", None), "world_id", minimum=0
        )
    except ActorRegistryError:
        raise
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise ActorRegistryError(str(exc)) from exc
    if current_id != world_id:
        message = "Actor alias episode changed during server lookup; alias was not rebound."
        raise ActorRegistryError(message)
    return identity


def identity_mismatch(expected: ActorIdentity, observed: ActorIdentity) -> str | None:
    """Describe a verified identity mismatch for precise invalidation evidence."""
    if expected.world_id != observed.world_id:
        return "episode"
    for field_name in ("actor_id", "type_id", "role_name"):
        if getattr(expected, field_name) != getattr(observed, field_name):
            return field_name
    return None


def _native_identity(actor: object, world_id: int) -> ActorIdentity:
    attributes = getattr(actor, "attributes", None)
    if not isinstance(attributes, Mapping):
        message = "Actor identity role_name requires native actor attributes."
        raise ActorRegistryError(message)
    return identity_from_value(
        {
            "actor_id": getattr(actor, "id", None),
            "world_id": world_id,
            "type_id": getattr(actor, "type_id", None),
            "role_name": attributes.get("role_name"),
        }
    )


def _identity_integer(value: object, field_name: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        message = f"Actor identity {field_name} must be an integer at least {minimum}."
        raise ActorRegistryError(message)
    return value


def _identity_type(value: object) -> str:
    if not isinstance(value, str) or not value:
        message = "Actor identity type_id must be a non-empty string."
        raise ActorRegistryError(message)
    return value


def _identity_role(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        message = "Actor identity role_name must be a string or null."
        raise ActorRegistryError(message)
    return value
