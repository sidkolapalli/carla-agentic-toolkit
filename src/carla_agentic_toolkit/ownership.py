"""Durable per-execution ownership journal and cleanup."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.errors import CarlaAdapterError, OwnershipError

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


class RunOwnership:
    """Persist actor IDs incrementally so cleanup survives child termination."""

    def __init__(self, path: Path) -> None:
        """Bind one execution journal."""
        self._path = path
        self._lock = threading.Lock()

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

    def world_id(self) -> int | None:
        """Return recorded episode identity, unknown for legacy journals."""
        with self._lock:
            return self._load().world_id

    def discard(self, actor_ids: Iterable[int]) -> None:
        """Remove actors explicitly cleaned by the script."""
        discarded = set(actor_ids)
        with self._lock:
            current = self._load()
            current.actor_ids = [item for item in current.actor_ids if item not in discarded]
            self._save(current)

    def actor_ids(self) -> tuple[int, ...]:
        """Return IDs in creation order."""
        with self._lock:
            return tuple(self._load().actor_ids)

    def clear(self) -> None:
        """Clear ownership after world replacement or complete cleanup."""
        with self._lock:
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
    return _Journal(cast("int | None", identity), _validated_actor_ids(value.get("actor_ids")))


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
    created = tuple(actor_ids)
    if ownership is None or not created:
        return
    identity = ownership.world_id()
    try:
        if identity is None:
            identity = adapter.get_world_identity()
            ownership.bind_world(identity)
        ownership.add(created)
        _verify_creation_episode(adapter, identity)
    except OwnershipError:
        _rollback_creation(adapter, created, identity)
        raise


def _verify_creation_episode(adapter: PythonCarlaAdapter, identity: int) -> None:
    if adapter.get_world_identity() != identity:
        message = "CARLA world episode changed during actor creation."
        raise OwnershipError(message)


def _rollback_creation(
    adapter: PythonCarlaAdapter,
    created: tuple[int, ...],
    world_id: int | None,
) -> None:
    try:
        if world_id == adapter.get_world_identity():
            adapter.destroy_actors(tuple(reversed(created)))
    except (CarlaAdapterError, RuntimeError):
        return


def cleanup_owned_actors(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership | None,
) -> dict[str, object]:
    """Best-effort destroy owned IDs in reverse creation order."""
    if ownership is None:
        return cleanup_report()
    try:
        return _cleanup_owned_actors(adapter, ownership)
    except (OwnershipError, CarlaAdapterError, RuntimeError) as exc:
        failure: dict[str, object] = {"actor_id": None, "error": str(exc)}
        return cleanup_report(failures=(failure,))


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
    ownership.clear()
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
) -> None:
    """Remove successful destroy-actor batch responses from a journal."""
    if ownership is not None:
        ownership.discard(_successful_batch_ids(payload.get("responses")))


def _successful_batch_ids(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    actor_ids = (_successful_response_id(response) for response in value)
    return tuple(actor_id for actor_id in actor_ids if actor_id is not None)


def _successful_response_id(value: object) -> int | None:
    if not isinstance(value, dict) or value.get("error") is not None:
        return None
    actor_id = value.get("actor_id")
    return cast("int", actor_id) if _actor_id(actor_id) else None


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
