"""Durable per-execution ownership journal and cleanup."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.creation_health import CreationHealth, add_creation_failures
from carla_agentic_toolkit.errors import CarlaAdapterError, OwnershipError
from carla_agentic_toolkit.ownership_creation import track_created_result
from carla_agentic_toolkit.ownership_release import (
    release_batch_destroyed as _release_batch_destroyed,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.models import DestroyResult

OWNERSHIP_FILENAME = "owned-actors.json"


@dataclass
class _Journal:
    world_id: int | None
    actor_ids: list[int]
    pending_creations: int = 0


class RunOwnership:
    """Persist actor IDs incrementally so cleanup survives child termination."""

    def __init__(self, path: Path) -> None:
        """Bind one execution journal."""
        self._path = path
        self._lock = threading.Lock()
        self.creation_health = CreationHealth()

    def add(self, actor_ids: Iterable[int], *, world_id: int | None = None) -> None:
        """Append newly created actor IDs once."""
        with self._lock:
            current = self._load()
            if world_id is not None:
                _bind_world(current, world_id)
            current.actor_ids.extend(
                actor_id for actor_id in actor_ids if actor_id not in current.actor_ids
            )
            self._save(current)

    def bind_world(self, world_id: int) -> None:
        """Bind lazily before creation; a replacement requires an explicit clear."""
        with self._lock:
            current = self._load()
            _bind_world(current, world_id)
            self._save(current)

    def begin_creation(self, world_id: int) -> None:
        """Persist an unresolved native mutation before issuing its RPC."""
        with self._lock:
            current = self._load()
            _bind_world(current, world_id)
            current.pending_creations += 1
            self._save(current)

    def complete_creation(self, world_id: int) -> None:
        """Resolve one intent only after same-episode creation or rollback evidence."""
        with self._lock:
            current = self._load()
            _bind_world(current, world_id)
            current.pending_creations = max(0, current.pending_creations - 1)
            self._save(current)

    def require_completed_creations(self) -> None:
        """Refuse recovery when an interrupted creation has no conclusive outcome."""
        with self._lock:
            _require_completed(self._load())

    def pending_creations(self) -> int:
        """Return the unresolved RPC count without dropping known actor IDs."""
        with self._lock:
            return self._load().pending_creations

    def world_id(self) -> int | None:
        """Return recorded episode identity, unknown for legacy journals."""
        with self._lock:
            return self._load().world_id

    def discard(self, actor_ids: Iterable[int], *, world_id: int | None = None) -> None:
        """Remove cleaned actors, optionally only from their originating episode."""
        discarded = set(actor_ids)
        with self._lock:
            current = self._load()
            if world_id is not None and current.world_id != world_id:
                return
            current.actor_ids = [item for item in current.actor_ids if item not in discarded]
            self._save(current)

    def actor_ids(self) -> tuple[int, ...]:
        """Return IDs in creation order."""
        with self._lock:
            return tuple(self._load().actor_ids)

    def clear(self, *, require_completed: bool = False) -> None:
        """Clear ownership after world replacement or complete cleanup."""
        with self._lock:
            if require_completed:
                _require_completed(self._load())
            self._save(_Journal(None, []))

    def _load(self) -> _Journal:
        if not self._path.exists():
            return _Journal(None, [])
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            message = f"Actor ownership journal could not be read: {exc}"
            raise OwnershipError(message) from exc
        return _validated_journal(value)

    def _save(self, journal: _Journal) -> None:
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        payload = {
            "schema_version": 1,
            "world_id": journal.world_id,
            "actor_ids": _validated_actor_ids(journal.actor_ids),
        }
        if journal.pending_creations:
            payload["pending_creations"] = journal.pending_creations
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(self._path)
        except OSError as exc:
            message = f"Actor ownership journal could not be written: {exc}"
            raise OwnershipError(message) from exc


def _validated_journal(value: object) -> _Journal:
    if isinstance(value, list):
        return _Journal(None, _validated_actor_ids(value))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        message = "Actor ownership journal has an unsupported schema."
        raise OwnershipError(message)
    identity = value.get("world_id")
    _validate_world_id(identity)
    pending = value.get("pending_creations", 0)
    _validate_pending_creations(pending)
    return _Journal(
        cast("int | None", identity),
        _validated_actor_ids(value.get("actor_ids")),
        cast("int", pending),
    )


def _validate_pending_creations(value: object) -> None:
    if type(value) is not int or value < 0:
        message = "Actor creation intent count must be a nonnegative integer."
        raise OwnershipError(message)


def _require_completed(journal: _Journal) -> None:
    if journal.pending_creations:
        message = "Unresolved actor creation requires manual recovery."
        raise OwnershipError(message)


def _validate_world_id(value: object) -> None:
    if value is not None and type(value) is not int:
        message = "Actor ownership world identity must be an integer or null."
        raise OwnershipError(message)


def _bind_world(journal: _Journal, world_id: int) -> None:
    if type(world_id) is not int:
        message = "Binding actor ownership requires an integer world identity."
        raise OwnershipError(message)
    if journal.world_id is None and journal.actor_ids:
        message = "Cannot adopt actor IDs without a recorded world identity."
        raise OwnershipError(message)
    if _different_world(journal.world_id, world_id):
        message = "CARLA world episode changed during actor ownership."
        raise OwnershipError(message)
    journal.world_id = world_id


def _different_world(recorded: int | None, current: int) -> bool:
    return recorded is not None and recorded != current


def track_owned(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership | None,
    actor_ids: Iterable[int],
) -> None:
    """Journal created actors or immediately roll them back if journaling fails."""
    track_created_result(adapter, ownership, actor_ids)


def cleanup_owned_actors(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership | None,
) -> dict[str, object]:
    """Best-effort destroy owned IDs in reverse creation order."""
    if ownership is None:
        return cleanup_report()
    try:
        return add_creation_failures(_cleanup_owned_actors(adapter, ownership), ownership)
    except (OwnershipError, CarlaAdapterError, RuntimeError) as exc:
        failure: dict[str, object] = {"actor_id": None, "error": str(exc)}
        return add_creation_failures(cleanup_report(failures=(failure,)), ownership)


def _cleanup_owned_actors(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership,
) -> dict[str, object]:
    attempted = tuple(reversed(ownership.actor_ids()))
    if attempted:
        changed = _ownership_world_changed(adapter, ownership)
        if changed is not None:
            return changed
    results = adapter.destroy_actors(attempted) if attempted else ()
    cleaned = _cleaned_ids(results)
    ownership.discard(cleaned)
    return cleanup_report(attempted, cleaned, _cleanup_failures(results))


def _ownership_world_changed(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership,
) -> dict[str, object] | None:
    recorded = ownership.world_id()
    if recorded is None:
        message = "Actor cleanup requires a recorded world identity; legacy IDs are ambiguous."
        raise OwnershipError(message)
    current = adapter.get_world_identity()
    if current == recorded:
        return None
    ownership.require_completed_creations()
    ownership.clear(require_completed=True)
    return cleanup_report() | {
        "world_changed": True,
        "previous_world_id": recorded,
        "current_world_id": current,
    }


def _cleaned_ids(results: Iterable[DestroyResult]) -> tuple[int, ...]:
    return tuple(result.actor_id for result in results if _cleaned(result))


def _cleanup_failures(results: Iterable[DestroyResult]) -> tuple[dict[str, object], ...]:
    return tuple(_cleanup_failure(result) for result in results if not _cleaned(result))


def _cleanup_failure(result: DestroyResult) -> dict[str, object]:
    return {"actor_id": result.actor_id, "error": result.error or "destroy returned false"}


def release_destroyed(
    ownership: RunOwnership | None,
    results: Iterable[DestroyResult],
) -> None:
    """Remove explicitly destroyed or already absent IDs from a journal."""
    if ownership is not None:
        ownership.discard(result.actor_id for result in results if _cleaned(result))


def release_batch_destroyed(
    ownership: RunOwnership | None,
    payload: dict[str, object],
    *,
    commands: list[dict[str, object]] | None = None,
) -> None:
    """Release validated batch evidence through the ownership compatibility API."""
    _release_batch_destroyed(ownership, payload, commands=commands)


def payload_actor_ids(payload: dict[str, object], *keys: str) -> tuple[int, ...]:
    """Read positive actor ID lists from a trusted adapter payload."""
    values: list[int] = []
    for key in keys:
        candidate = payload.get(key, [])
        if isinstance(candidate, list):
            values.extend(cast("list[int]", candidate))
    return tuple(actor_id for actor_id in values if _actor_id(actor_id))


def _validated_actor_ids(value: object) -> list[int]:
    if not isinstance(value, list) or not all(_actor_id(item) for item in value):
        message = "Actor ownership journal must contain positive integer actor IDs."
        raise OwnershipError(message)
    return cast("list[int]", value)


def _cleaned(result: DestroyResult) -> bool:
    return result.destroyed or result.error == "Actor was not found."


def _actor_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def cleanup_report(
    attempted: Iterable[int] = (),
    destroyed: Iterable[int] = (),
    failures: Iterable[dict[str, object]] = (),
) -> dict[str, object]:
    """Return one stable cleanup report shape."""
    return {
        "attempted_actor_ids": list(attempted),
        "destroyed_actor_ids": list(destroyed),
        "failures": list(failures),
    }
