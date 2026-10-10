"""Common recoverable-error and snapshot policy for the curated script facade."""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import (
    ActorRegistryError,
    CarlaAdapterError,
    OwnershipError,
    UnsupportedFeatureError,
)
from carla_agentic_toolkit.script_connection import (
    begin_persistent_request,
    finish_persistent_outcome,
    guarded_operation,
    persistent_enabled,
    record_operation_error,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.models import JsonObject
    from carla_agentic_toolkit.snapshots import RunSnapshots


def recover(
    error_type: str,
    *,
    endpoint: bool = False,
    retryable: bool = True,
    hint: str | None = None,
) -> Callable[[Callable[..., JsonObject]], Callable[..., JsonObject]]:
    """Route one facade method through the recoverable operation policy."""

    def decorate(method: Callable[..., JsonObject]) -> Callable[..., JsonObject]:
        @wraps(method)
        def recovered(self: ScriptOperations, *args: object, **kwargs: object) -> JsonObject:
            details = {"host": self._adapter.host, "port": self._adapter.port} if endpoint else {}
            if hint is not None:
                details["hint"] = hint
            return self._operation(
                error_type,
                lambda: method(self, *args, **kwargs),
                retryable=retryable,
                **details,
            )

        return recovered

    return decorate


class ScriptOperations:
    """Keep one error normalization and result-snapshot policy for all script operations."""

    _adapter: PythonCarlaAdapter
    _snapshots: RunSnapshots

    def _operation(
        self,
        error_type: str,
        operation: Callable[[], JsonObject],
        *,
        retryable: bool = True,
        **details: object,
    ) -> JsonObject:
        """Return a CARLA value or one uniform recoverable failure."""
        try:
            return guarded_operation(self._adapter, operation)
        except UnsupportedFeatureError as exc:
            return {
                "ok": False,
                "error_type": "unsupported_feature",
                "message": str(exc),
                "retryable": False,
                "error": str(exc),
                **details,
            }
        except (ActorRegistryError, CarlaAdapterError, OwnershipError) as exc:
            error = record_operation_error(self._adapter, exc)
            return _operation_failure(error_type, error, retryable=retryable, **details)
        except (RuntimeError, OSError) as exc:
            if not persistent_enabled(self._adapter):
                raise
            error = record_operation_error(self._adapter, exc)
            return _operation_failure(error_type, error, retryable=retryable, **details)

    def _begin_persistent_request(self) -> None:
        begin_persistent_request(self._adapter)

    def _finish_persistent_outcome(self, outcome: dict[str, object]) -> dict[str, object]:
        return finish_persistent_outcome(self._adapter, outcome)

    def _snapshot(self, uri: str, payload: JsonObject) -> JsonObject:
        self._snapshots.register_snapshot(uri, payload)
        return payload


def _retryable_error(exc: Exception, requested: bool) -> bool:  # noqa: FBT001
    return (
        requested
        and not isinstance(exc, OwnershipError)
        and getattr(exc, "details", {}).get("retryable") is not False
    )


def _operation_failure(
    error_type: str, error: Exception, *, retryable: bool, **details: object
) -> JsonObject:
    return {
        "ok": False,
        "error_type": error_type,
        "message": str(error),
        "error": str(error),
        **details,
        **getattr(error, "details", {}),
        "retryable": _retryable_error(error, retryable),
    }
