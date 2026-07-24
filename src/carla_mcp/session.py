"""Session state for CARLA MCP resources."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

type JsonObject = dict[str, object]


@dataclass(slots=True)
class CarlaSession:
    """In-memory resources for one sandboxed script execution."""

    _resources: dict[str, JsonObject] = field(default_factory=dict)

    def register_resource(self, uri: str, payload: Mapping[str, object]) -> None:
        """Register a read-only snapshot for a resource URI."""
        self._resources[uri] = dict(deepcopy(payload))

    def read_resource(self, uri: str) -> JsonObject:
        """Read a resource snapshot by URI."""
        return deepcopy(self._resources[uri])

    def resource_uris(self) -> tuple[str, ...]:
        """Return registered resource URIs in deterministic order."""
        return tuple(sorted(self._resources))
