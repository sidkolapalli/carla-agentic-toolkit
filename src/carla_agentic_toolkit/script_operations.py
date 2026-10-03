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

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.models import JsonObject
    from carla_agentic_toolkit.snapshots import RunSnapshots


def recover(
    error_type: str,
    *,
    endpoint: bool = False,
) -> Callable[[Callable[..., JsonObject]], Callable[..., JsonObject]]:
    """Route one facade method through the recoverable operation policy."""

    def decorate(method: Callable[..., JsonObject]) -> Callable[..., JsonObject]:
        @wraps(method)
        def recovered(self: ScriptOperations, *args: object, **kwargs: object) -> JsonObject:
            details = {"host": self._adapter.host, "port": self._adapter.port} if endpoint else {}
            return self._operation(
                error_type,
                lambda: method(self, *args, **kwargs),
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
        **details: object,
    ) -> JsonObject:
        """Return a CARLA value or one uniform recoverable failure."""
        try:
            return operation()
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
            return {
                "ok": False,
                "error_type": error_type,
                "message": str(exc),
                "retryable": True,
                "error": str(exc),
                **details,
                **getattr(exc, "details", {}),
            }

    def _snapshot(self, uri: str, payload: JsonObject) -> JsonObject:
        self._snapshots.register_snapshot(uri, payload)
        return payload
