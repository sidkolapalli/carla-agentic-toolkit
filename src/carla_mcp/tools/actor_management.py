"""Actor inspection and cleanup tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from carla_mcp.errors import CarlaAdapterError
from carla_mcp.models import ActorSnapshot, DestroyResult, ToolResult

if TYPE_CHECKING:
    from carla_mcp.session import CarlaSession


class ActorManagementAdapter(Protocol):
    """Adapter contract for actor inspection and cleanup."""

    def list_actors(self, filter_pattern: str) -> tuple[ActorSnapshot, ...]:
        """Return actor snapshots matching a CARLA wildcard filter."""

    def destroy_actors(self, actor_ids: tuple[int, ...]) -> tuple[DestroyResult, ...]:
        """Destroy explicit CARLA actor IDs."""


def list_actors(
    adapter: ActorManagementAdapter,
    session: CarlaSession,
    filter_pattern: str,
) -> ToolResult:
    """List CARLA actors and publish the actor inventory resource."""
    try:
        actors = adapter.list_actors(filter_pattern)
    except CarlaAdapterError as exc:
        return _adapter_error("list_actors_failed", exc)
    payload = {"actors": [actor.to_dict() for actor in actors]}
    session.register_resource("carla://actors/current", payload)
    return ToolResult.ok(payload)


def destroy_actors(
    adapter: ActorManagementAdapter,
    session: CarlaSession,
    actor_ids: tuple[int, ...],
) -> ToolResult:
    """Destroy CARLA actors by ID and publish cleanup results."""
    try:
        results = adapter.destroy_actors(actor_ids)
    except CarlaAdapterError as exc:
        return _adapter_error("destroy_actors_failed", exc)
    payload = {"results": [result.to_dict() for result in results]}
    session.register_resource("carla://actors/destroyed", payload)
    return ToolResult.ok(payload)


def _adapter_error(error_type: str, exc: CarlaAdapterError) -> ToolResult:
    """Convert adapter failures into MCP-style tool errors."""
    return ToolResult.error(
        {
            "error_type": error_type,
            "message": str(exc),
            "retryable": True,
        }
    )
