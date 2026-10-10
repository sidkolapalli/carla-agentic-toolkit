"""Persistent conversational names for CARLA actors."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.actor_identity import (
    ActorIdentity,
    identity_from_value,
    identity_mismatch,
)
from carla_agentic_toolkit.errors import ActorRegistryError

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

MAX_ACTOR_NAME_LENGTH = 100


def actor_registry_path(output_dir: Path, host: str, port: int) -> Path:
    """Return an endpoint-isolated registry path below the durable output directory."""
    endpoint = f"{host}\0{port}".encode()
    digest = hashlib.sha256(endpoint, usedforsecurity=False).hexdigest()[:16]
    return output_dir / f"named-actors-{digest}.json"


class ActorRegistry:
    """Store actor aliases in one small JSON file."""

    def __init__(self, path: Path) -> None:
        """Bind the registry to one endpoint-specific file."""
        self._path = path

    def name_actor(
        self,
        name: object,
        actor_id: object,
        lookup: Callable[[int], ActorIdentity | None],
    ) -> dict[str, object]:
        """Assign a name using an explicit server actor description and episode."""
        normalized_name = _actor_name(name)
        normalized_id = _actor_id(actor_id)
        self._load()
        identity = _lookup_identity(lookup, normalized_id)
        if identity is None:
            message = f"Actor {normalized_id} does not exist."
            raise ActorRegistryError(message)
        self.set(normalized_name, identity)
        return {"name": normalized_name, "actor_id": normalized_id}

    def resolve_actor(
        self, name: object, lookup: Callable[[int], ActorIdentity | None]
    ) -> dict[str, object]:
        """Verify server liveness and exact stored identity before resolving a name."""
        normalized_name = _actor_name(name)
        expected = self.get(normalized_name)
        if expected is None:
            raise ActorRegistryError(_missing_message(normalized_name))
        observed = _lookup_identity(lookup, expected.actor_id)
        if observed is None:
            self.remove(normalized_name)
            message = f"Named actor '{normalized_name}' no longer exists."
            raise ActorRegistryError(message)
        if mismatch := identity_mismatch(expected, observed):
            self.remove(normalized_name)
            message = f"Named actor '{normalized_name}' {mismatch} mismatch; alias invalidated."
            raise ActorRegistryError(message)
        return {"name": normalized_name, "actor_id": expected.actor_id}

    def list_actors(self) -> dict[str, object]:
        """Return aliases in deterministic JSON-compatible order."""
        return {
            "actors": [
                {"name": name, "actor_id": identity.actor_id} for name, identity in self.items()
            ]
        }

    def forget_actor(self, name: object) -> dict[str, object]:
        """Forget one name without destroying its CARLA actor."""
        normalized_name = _actor_name(name)
        identity = self.remove(normalized_name)
        if identity is None:
            raise ActorRegistryError(_missing_message(normalized_name))
        return {"name": normalized_name, "actor_id": identity.actor_id, "forgotten": True}

    def set(self, name: str, identity: ActorIdentity) -> None:
        """Create or replace an alias without accepting unbound integer IDs."""
        actors = self._load()
        actors[_actor_name(name)] = identity_from_value(identity.to_dict())
        self._save(actors)

    def get(self, name: str) -> ActorIdentity | None:
        """Return the complete identity when the alias exists."""
        return self._load().get(name)

    def remove(self, name: str) -> ActorIdentity | None:
        """Remove and return one identity when present."""
        actors = self._load()
        actor_id = actors.pop(name, None)
        if actor_id is not None:
            self._save(actors)
        return actor_id

    def items(self) -> tuple[tuple[str, ActorIdentity], ...]:
        """Return aliases in deterministic order."""
        return tuple(sorted(self._load().items()))

    def _load(self) -> dict[str, ActorIdentity]:
        if not self._path.exists():
            return {}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            message = f"Actor registry could not be read: {exc}"
            raise ActorRegistryError(message) from exc
        return _registry_entries(value)

    def _save(self, actors: dict[str, ActorIdentity]) -> None:
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            values = {name: identity.to_dict() for name, identity in actors.items()}
            temporary.write_text(json.dumps(values, sort_keys=True), encoding="utf-8")
            temporary.replace(self._path)
        except OSError as exc:
            message = f"Actor registry could not be written: {exc}"
            raise ActorRegistryError(message) from exc


def _lookup_identity(
    lookup: Callable[[int], ActorIdentity | None], actor_id: int
) -> ActorIdentity | None:
    identity = lookup(actor_id)
    if identity is not None and identity.actor_id != actor_id:
        message = (
            f"Lookup actor_id {identity.actor_id} does not match requested actor_id {actor_id}."
        )
        raise ActorRegistryError(message)
    return identity


def _registry_entries(value: object) -> dict[str, ActorIdentity]:
    """Validate the complete registry before accepting any alias from it."""
    if not isinstance(value, dict):
        message = "Actor registry must contain named episode-bound actor identities."
        raise ActorRegistryError(message)
    return {_registry_name(name): _registry_identity(identity) for name, identity in value.items()}


def _registry_name(value: object) -> str:
    try:
        name = _actor_name(value)
    except ActorRegistryError as exc:
        message = f"Actor registry contains an invalid name: {exc}"
        raise ActorRegistryError(message) from exc
    if name != value:
        message = "Actor registry names must be normalized non-empty strings."
        raise ActorRegistryError(message)
    return name


def _registry_identity(value: object) -> ActorIdentity:
    if type(value) is int:
        message = "Actor registry contains legacy integer-only aliases without episode evidence."
        raise ActorRegistryError(message)
    try:
        return identity_from_value(value)
    except ActorRegistryError as exc:
        message = f"Actor registry contains an invalid identity: {exc}"
        raise ActorRegistryError(message) from exc


def _actor_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > MAX_ACTOR_NAME_LENGTH
    ):
        message = (
            f"Actor name must be a non-empty string of at most {MAX_ACTOR_NAME_LENGTH} characters."
        )
        raise ActorRegistryError(message)
    return value.strip()


def _actor_id(value: object) -> int:
    if not _positive_actor_id(value):
        message = "actor_id must be a positive integer."
        raise ActorRegistryError(message)
    return cast("int", value)


def _positive_actor_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _missing_message(name: str) -> str:
    return f"Named actor '{name}' was not found."
