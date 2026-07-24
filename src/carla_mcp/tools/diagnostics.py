"""Diagnostic tool implementations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_mcp.adapter import CarlaAdapterError, HealthAdapter
from carla_mcp.models import ToolResult

if TYPE_CHECKING:
    from carla_mcp.session import CarlaSession


def health_check(adapter: HealthAdapter, session: CarlaSession) -> ToolResult:
    """Check CARLA server health and publish session status."""
    try:
        report = adapter.health_check()
    except CarlaAdapterError as exc:
        return ToolResult.error(
            {
                "error_type": "carla_connection_failed",
                "message": str(exc),
                "retryable": True,
                "suggested_next_tools": ["diagnose_environment", "health_check"],
            },
            message="CARLA health check failed.",
        )
    payload = report.to_dict()
    session.register_resource("carla://session/status", payload)
    return ToolResult.ok(payload)
