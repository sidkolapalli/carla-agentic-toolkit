"""Durable, restrictive episode evidence for persistent script recovery only."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.errors import CarlaAdapterError, OwnershipError
from carla_agentic_toolkit.managed_control_io import read_control, write_control

if TYPE_CHECKING:
    from pathlib import Path

CONNECTION_FILENAME = "persistent-connection.json"


class ConnectionJournal:
    """Record the origin before mutation, never authorize actor adoption or deletion."""

    def __init__(self, path: Path) -> None:
        """Bind an existing private journal path without replacing its evidence."""
        self.path = path

    def initialize(self) -> None:
        """Initialize only the new parent-owned session before launching its worker."""
        self._save({"schema_version": 1, "world_id": None, "restart": None})

    def world_id(self) -> int | None:
        """Return a validated origin only when no restart was recorded."""
        state = self._load()
        self._require_healthy(state)
        return cast("int | None", state["world_id"])

    def require_healthy(self) -> None:
        """Refuse cleanup when durable evidence records an unexpected restart."""
        self._require_healthy(self._load())

    def bind(self, identity: int, *, acknowledged: bool = False) -> None:
        """Persist first access or an explicitly acknowledged toolkit replacement."""
        state = self._load()
        self._require_healthy(state)
        if state["world_id"] not in (None, identity) and not acknowledged:
            raise restart_error(cast("int", state["world_id"]), identity)
        state["world_id"] = identity
        self._save(state)

    def record_restart(self, expected: int, observed: int) -> None:
        """Retain both episode identities without replacing the origin."""
        state = self._load()
        if state["world_id"] != expected:
            message = "Persistent connection origin changed before recording a restart."
            raise CarlaAdapterError(message)
        state["restart"] = {"expected_world_id": expected, "observed_world_id": observed}
        self._save(state)

    def require_identity(self, identity: int) -> None:
        """Restrict cleanup to the durable origin without adopting another episode."""
        recorded = self.world_id()
        if recorded is not None and recorded != identity:
            raise restart_error(recorded, identity)

    def _load(self) -> dict[str, object]:
        state = read_control(self.path)
        _validate(state)
        return state

    def _save(self, state: dict[str, object]) -> None:
        _validate(state)
        try:
            write_control(self.path, state)
        except OSError as exc:
            raise OwnershipError(str(exc)) from exc

    @staticmethod
    def _require_healthy(state: dict[str, object]) -> None:
        restart = state["restart"]
        if isinstance(restart, dict):
            identities = cast("dict[str, int]", restart)
            raise restart_error(identities["expected_world_id"], identities["observed_world_id"])


def restart_error(expected: int, observed: int) -> CarlaAdapterError:
    """Return a terminal error that preserves the origin and observation separately."""
    return CarlaAdapterError(
        "The CARLA simulator episode changed outside a toolkit world replacement. "
        "Close this session and investigate its retained cleanup evidence.",
        details={
            "error_type": "simulator_restarted",
            "retryable": False,
            "expected_world_id": expected,
            "observed_world_id": observed,
        },
    )


def _identity(value: object) -> bool:
    return type(value) is int and value >= 0


def _validate(state: dict[str, object]) -> None:
    if set(state) != {"schema_version", "world_id", "restart"}:
        message = "Persistent connection journal requires exactly the reviewed fields."
        raise ValueError(message)
    _validate_schema(state["schema_version"])
    _validate_origin(state["world_id"])
    _validate_restart(state["restart"], state["world_id"])


def _validate_schema(version: object) -> None:
    if type(version) is not int or version != 1:
        message = "Persistent connection journal has an unsupported schema."
        raise ValueError(message)


def _validate_origin(identity: object) -> None:
    if identity is not None and not _identity(identity):
        message = "Persistent connection journal requires an integer episode identity."
        raise ValueError(message)


def _validate_restart(restart: object, identity: object) -> None:
    if restart is None:
        return
    fields = _restart_fields(restart)
    expected, observed = fields["expected_world_id"], fields["observed_world_id"]
    _validate_restart_identities(expected, observed)
    if expected != identity or expected == observed:
        message = "Persistent restart evidence must retain a distinct originating episode."
        raise ValueError(message)


def _restart_fields(restart: object) -> dict[str, object]:
    if not isinstance(restart, dict) or set(restart) != {"expected_world_id", "observed_world_id"}:
        message = "Persistent restart evidence requires both episode identities."
        raise ValueError(message)
    return cast("dict[str, object]", restart)


def _validate_restart_identities(expected: object, observed: object) -> None:
    if not _identity(expected) or not _identity(observed):
        message = "Persistent restart evidence requires integer episode identities."
        raise ValueError(message)
