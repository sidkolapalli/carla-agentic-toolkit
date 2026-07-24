"""Actor and blueprint tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_mcp.adapter import ActorAdapter, CarlaAdapterError
from carla_mcp.models import SpawnRequest, ToolResult

if TYPE_CHECKING:
    from carla_mcp.session import CarlaSession


def list_blueprints(
    adapter: ActorAdapter,
    session: CarlaSession,
    filter_pattern: str,
) -> ToolResult:
    """List CARLA blueprints and publish a filtered blueprint resource."""
    try:
        blueprints = adapter.list_blueprints(filter_pattern)
    except CarlaAdapterError as exc:
        return _adapter_error("list_blueprints_failed", exc)
    payload = {"blueprints": [blueprint.to_dict() for blueprint in blueprints]}
    session.register_resource(f"carla://blueprints/{filter_pattern}", payload)
    return ToolResult.ok(payload)


def spawn_actor_batch(
    adapter: ActorAdapter,
    session: CarlaSession,
    requests: tuple[SpawnRequest, ...],
) -> ToolResult:
    """Spawn a batch of CARLA actors and publish actor results."""
    try:
        results = adapter.spawn_actor_batch(requests)
    except CarlaAdapterError as exc:
        return _adapter_error("spawn_actor_batch_failed", exc)
    payload = {"results": [result.to_dict() for result in results]}
    session.register_resource("carla://actors", payload)
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
