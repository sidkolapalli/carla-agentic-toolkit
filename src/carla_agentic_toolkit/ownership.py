"""Durable per-execution ownership journal and cleanup."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.errors import OwnershipError

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.models import DestroyResult

OWNERSHIP_FILENAME = "owned-actors.json"


class RunOwnership:
    """Persist actor IDs incrementally so cleanup survives child termination."""

    def __init__(self, path: Path) -> None:
        """Bind one execution journal."""
        self._path = path
        self._lock = threading.Lock()

    def add(self, actor_ids: Iterable[int]) -> None:
        """Append newly created actor IDs once."""
        with self._lock:
            current = self._load()
            current.extend(actor_id for actor_id in actor_ids if actor_id not in current)
            self._save(current)

    def discard(self, actor_ids: Iterable[int]) -> None:
        """Remove actors explicitly cleaned by the script."""
        discarded = set(actor_ids)
        with self._lock:
            self._save([actor_id for actor_id in self._load() if actor_id not in discarded])

    def actor_ids(self) -> tuple[int, ...]:
        """Return IDs in creation order."""
        with self._lock:
            return tuple(self._load())

    def clear(self) -> None:
        """Clear ownership after world replacement or complete cleanup."""
        with self._lock:
            self._save([])

    def _load(self) -> list[int]:
        if not self._path.exists():
            return []
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            message = f"Actor ownership journal could not be read: {exc}"
            raise OwnershipError(message) from exc
        return _validated_actor_ids(value)

    def _save(self, actor_ids: list[int]) -> None:
        temporary = self._path.with_name(f".{self._path.name}.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(actor_ids), encoding="utf-8")
            temporary.replace(self._path)
        except OSError as exc:
            message = f"Actor ownership journal could not be written: {exc}"
            raise OwnershipError(message) from exc


def track_owned(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership | None,
    actor_ids: Iterable[int],
) -> None:
    """Journal created actors or immediately roll them back if journaling fails."""
    created = tuple(actor_ids)
    if ownership is None or not created:
        return
    try:
        ownership.add(created)
    except OwnershipError:
        adapter.destroy_actors(tuple(reversed(created)))
        raise


def cleanup_owned_actors(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership | None,
) -> dict[str, object]:
    """Best-effort destroy owned IDs in reverse creation order."""
    if ownership is None:
        return cleanup_report()
    try:
        return _cleanup_owned_actors(adapter, ownership)
    except (OwnershipError, RuntimeError) as exc:
        failure: dict[str, object] = {"actor_id": None, "error": str(exc)}
        return cleanup_report(failures=(failure,))


def _cleanup_owned_actors(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership,
) -> dict[str, object]:
    attempted = tuple(reversed(ownership.actor_ids()))
    results = adapter.destroy_actors(attempted) if attempted else ()
    cleaned = _cleaned_ids(results)
    ownership.discard(cleaned)
    return cleanup_report(attempted, cleaned, _cleanup_failures(results))


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
