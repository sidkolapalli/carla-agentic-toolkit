"""Run-local CARLA snapshots returned inline with one script result."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

type JsonObject = dict[str, object]


@dataclass(slots=True)
class RunSnapshots:
    """In-memory snapshots for one sandboxed script execution."""

    _snapshots: dict[str, JsonObject] = field(default_factory=dict)

    def register_snapshot(self, uri: str, payload: Mapping[str, object]) -> None:
        """Register a read-only snapshot for a snapshot URI."""
        self._snapshots[uri] = dict(deepcopy(payload))

    def read_snapshot(self, uri: str) -> JsonObject:
        """Read a snapshot by its run-local identifier."""
        return deepcopy(self._snapshots[uri])

    def snapshot_uris(self) -> tuple[str, ...]:
        """Return registered snapshot URIs in deterministic order."""
        return tuple(sorted(self._snapshots))
