"""Keep a failed creation transaction visible through worker completion."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.errors import OwnershipError

if TYPE_CHECKING:
    from carla_agentic_toolkit.models import DestroyResult
    from carla_agentic_toolkit.ownership import RunOwnership


class CreationHealth:
    """A journal failure permanently blocks further creation in this execution."""

    def __init__(self) -> None:
        """Start with no uncertain creation."""
        self._lock = threading.Lock()
        self._error: str | None = None
        self._failures: list[dict[str, object]] = []

    def record(self, error: str, failures: tuple[DestroyResult, ...]) -> None:
        """Retain unconfirmed rollback evidence even if the journal stayed empty."""
        with self._lock:
            self._error = self._error or error
            self._failures.extend(
                {"actor_id": result.actor_id, "error": result.error or "destroy returned false"}
                for result in failures
            )

    def require_healthy(self) -> None:
        """Reject creation after the first ownership failure."""
        with self._lock:
            if self._error is not None:
                raise OwnershipError(self._error)

    def error(self) -> str | None:
        """Return the terminal ownership failure, if any."""
        with self._lock:
            return self._error

    def failures(self) -> list[dict[str, object]]:
        """Return independent cleanup evidence for unresolved returned IDs."""
        with self._lock:
            return list(self._failures)


def finish_creation_outcome(
    outcome: dict[str, object], ownership: RunOwnership | None
) -> dict[str, object]:
    """Turn an ignored ownership error into a truthful terminal result."""
    if ownership is None:
        return outcome
    try:
        ownership.require_completed_creations()
    except OwnershipError as exc:
        ownership.creation_health.record(str(exc), ())
    if ownership.creation_health.error() is None:
        return outcome
    if outcome.get("ok"):
        outcome = outcome | {
            "ok": False,
            "result": None,
            "error_type": "actor_ownership_failed",
            "error": ownership.creation_health.error(),
        }
    outcome["cleanup"] = add_creation_failures(
        cast("dict[str, object]", outcome.get("cleanup", {"failures": []})), ownership
    )
    return outcome


def add_creation_failures(cleanup: dict[str, object], ownership: RunOwnership) -> dict[str, object]:
    """Do not let an empty durable journal erase failed rollback evidence."""
    existing = cast("list[dict[str, object]]", cleanup.get("failures", []))
    failures = existing + ownership.creation_health.failures() + _pending_failures(ownership)
    return cleanup | {"failures": failures}


def _pending_failures(ownership: RunOwnership) -> list[dict[str, object]]:
    try:
        ownership.require_completed_creations()
    except OwnershipError as exc:
        return [{"actor_id": None, "error": str(exc)}]
    return []
