"""Persistent-only transport invalidation and originating-episode authority."""

from __future__ import annotations

from contextlib import contextmanager, suppress
from contextvars import ContextVar
from functools import partial
from typing import TYPE_CHECKING, TypeVar

from carla_agentic_toolkit.carla_versions import read_version_info, require_matching_release
from carla_agentic_toolkit.connection_journal import ConnectionJournal, restart_error
from carla_agentic_toolkit.errors import (
    BlueprintInputError,
    CarlaAdapterError,
    OwnershipError,
    UnsupportedFeatureError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path
    from typing import Never

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld

_OPERATION_GUARD: ContextVar[Callable[[CarlaWorld], None] | None] = ContextVar(
    "persistent_episode_guard", default=None
)
_T = TypeVar("_T")


class PersistentConnection:
    """A new connection cannot erase the originating episode or failed request."""

    def __init__(self, path: Path | None = None) -> None:
        """Retain optional durable origin evidence for the parent-launched worker."""
        self._journal = ConnectionJournal(path) if path is not None else None
        self.world_id: int | None = None
        self._request_failure: CarlaAdapterError | None = None
        self._restart: CarlaAdapterError | None = None
        self._evidence_failure: OwnershipError | None = None
        if self._journal is not None:
            with suppress(OwnershipError, CarlaAdapterError):
                self.world_id = self._journal_value(self._journal.world_id)

    def begin_request(self) -> None:
        """Permit a fresh transport attempt, never forgive an unexpected restart."""
        self._request_failure = None

    def require_available(self) -> None:
        """Never repeat an RPC within its failed request or after a detected restart."""
        if failure := self._restart or self._evidence_failure or self._request_failure:
            raise failure
        if self._journal is not None:
            self._journal_value(self._journal.require_healthy)

    def before_world(
        self, client: CarlaClient, *, on_error: Callable[[Exception], bool] | None = None
    ) -> None:
        """Unknown or incompatible native versions cannot safely attach an episode."""
        self.require_available()
        versions = read_version_info(client, on_error=on_error)
        self.require_available()
        require_matching_release(versions)

    def observe(self, identity: int) -> None:
        """Bind only actual world access; refuse an externally changed episode."""
        self.require_available()
        if self.world_id is None:
            if (journal := self._journal) is not None:
                self._persist(partial(journal.bind, identity))
            self.world_id = identity
        elif identity != self.world_id:
            self._restart = restart_error(self.world_id, identity)
            if (journal := self._journal) is not None:
                expected = self.world_id
                self._persist(partial(journal.record_restart, expected, identity))
            raise self._restart

    def acknowledge_replacement(self, identity: int) -> None:
        """Only a successful native toolkit map response grants a new episode."""
        self.require_available()
        if (journal := self._journal) is not None:
            self._persist(partial(journal.bind, identity, acknowledged=True))
        self.world_id = identity

    def record_failure(self, error: Exception) -> bool:
        """Classify native runtime/transport causes without invalidating local errors."""
        if not native_rpc_failure(error):
            return False
        failure = error if isinstance(error, CarlaAdapterError) else CarlaAdapterError(str(error))
        if failure is not error:
            failure.__cause__ = error
        self._request_failure = failure
        return True

    def finish_outcome(self, outcome: dict[str, object]) -> dict[str, object]:
        """Prevent ignored terminal errors from becoming successful execution."""
        failure = self._restart or self._evidence_failure
        if failure is None:
            return outcome
        return {
            **outcome,
            "ok": False,
            "message": str(failure),
            "error": str(failure),
            **self._terminal_details(),
        }

    def operation_error(self, error: Exception) -> Exception:
        """Keep sticky restart details through existing nested native-error wrappers."""
        return self._restart or self._evidence_failure or error

    def _persist(self, operation: Callable[[], None]) -> None:
        self._journal_value(operation)

    def _journal_value(self, operation: Callable[[], _T]) -> _T:
        try:
            return operation()
        except CarlaAdapterError as exc:
            if exc.details.get("error_type") == "simulator_restarted":
                self._restart = exc
                raise
            self._fail_evidence(exc)
        except (OwnershipError, OSError, ValueError, TypeError) as exc:
            self._fail_evidence(exc)

    def _fail_evidence(self, error: Exception) -> Never:
        failure = error if isinstance(error, OwnershipError) else OwnershipError(str(error))
        self._evidence_failure = failure
        if failure is error:
            raise failure
        raise failure from error

    def _terminal_details(self) -> dict[str, object]:
        if self._restart is not None:
            return self._restart.details
        return {"error_type": "persistent_connection_evidence_failed", "retryable": False}


def native_rpc_failure(error: Exception) -> bool:
    """CARLA native RPCs raise RuntimeError; OS transport failures remain explicit."""
    if isinstance(error, OwnershipError | UnsupportedFeatureError | BlueprintInputError):
        return False
    if isinstance(error, CarlaAdapterError):
        if error.details.get("error_type") == "simulator_restarted":
            return False
        cause = error.__cause__
        return isinstance(cause, Exception) and native_rpc_failure(cause)
    return isinstance(error, RuntimeError | OSError)


@contextmanager
def operation_episode_guard(
    guard: Callable[[CarlaWorld], None] | None,
) -> Iterator[None]:
    """Isolate the optional lookup/setter check to this operation and execution context."""
    token = _OPERATION_GUARD.set(guard)
    try:
        yield
    finally:
        _OPERATION_GUARD.reset(token)


def require_operation_episode(world: CarlaWorld) -> None:
    """Recheck after a native lookup immediately before using retained objects."""
    if guard := _OPERATION_GUARD.get():
        guard(world)
