"""Persistent conversational names for CARLA actors."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.errors import ActorRegistryError

if TYPE_CHECKING:
    from collections.abc import Collection
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
        self, name: object, actor_id: object, live_ids: Collection[int]
    ) -> dict[str, object]:
        """Assign a validated name to one live actor."""
        normalized_name = _actor_name(name)
        normalized_id = _actor_id(actor_id)
        if normalized_id not in live_ids:
            message = f"Actor {normalized_id} does not exist."
            raise ActorRegistryError(message)
        self.set(normalized_name, normalized_id)
        return {"name": normalized_name, "actor_id": normalized_id}

    def resolve_actor(self, name: object, live_ids: Collection[int]) -> dict[str, object]:
        """Resolve one name and invalidate it when its actor disappeared."""
        normalized_name = _actor_name(name)
        actor_id = self.get(normalized_name)
        if actor_id is None:
            raise ActorRegistryError(_missing_message(normalized_name))
        if actor_id not in live_ids:
            self.remove(normalized_name)
            message = f"Named actor '{normalized_name}' no longer exists."
            raise ActorRegistryError(message)
        return {"name": normalized_name, "actor_id": actor_id}

    def list_actors(self) -> dict[str, object]:
        """Return aliases in deterministic JSON-compatible order."""
        return {"actors": [{"name": name, "actor_id": actor_id} for name, actor_id in self.items()]}

    def forget_actor(self, name: object) -> dict[str, object]:
        """Forget one name without destroying its CARLA actor."""
        normalized_name = _actor_name(name)
        actor_id = self.remove(normalized_name)
        if actor_id is None:
            raise ActorRegistryError(_missing_message(normalized_name))
        return {"name": normalized_name, "actor_id": actor_id, "forgotten": True}

    def set(self, name: str, actor_id: int) -> None:
        """Create or replace an actor alias."""
        actors = self._load()
        actors[name] = actor_id
        self._save(actors)

    def get(self, name: str) -> int | None:
        """Return one actor ID when the alias exists."""
        return self._load().get(name)

    def remove(self, name: str) -> int | None:
        """Remove and return one actor ID when present."""
        actors = self._load()
        actor_id = actors.pop(name, None)
        if actor_id is not None:
            self._save(actors)
        return actor_id

    def items(self) -> tuple[tuple[str, int], ...]:
        """Return aliases in deterministic order."""
        return tuple(sorted(self._load().items()))

    def _load(self) -> dict[str, int]:
        if not self._path.exists():
            return {}
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            message = f"Actor registry could not be read: {exc}"
            raise ActorRegistryError(message) from exc
        if not isinstance(value, dict) or not _valid_entries(value):
            msg = "Actor registry must contain string names and positive integer actor IDs."
            raise ActorRegistryError(msg)
        return cast("dict[str, int]", value)

    def _save(self, actors: dict[str, int]) -> None:
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(actors, sort_keys=True), encoding="utf-8")
            temporary.replace(self._path)
        except OSError as exc:
            message = f"Actor registry could not be written: {exc}"
            raise ActorRegistryError(message) from exc


def _valid_entries(value: dict[object, object]) -> bool:
    """Return whether a decoded registry has the expected bounded scalar shape."""
    return all(_valid_entry(name, actor_id) for name, actor_id in value.items())


def _valid_entry(name: object, actor_id: object) -> bool:
    return isinstance(name, str) and bool(name) and _positive_actor_id(actor_id)


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
