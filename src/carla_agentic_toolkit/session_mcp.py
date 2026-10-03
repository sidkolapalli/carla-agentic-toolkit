"""Opt-in persistent sandbox session lifecycle, scoped to one MCP server instance."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal, Protocol

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from carla_agentic_toolkit import __version__

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SessionAction = Literal["open", "execute", "read", "cancel", "close", "telemetry"]


class SessionManager(Protocol):
    """Local manager owns all worker identities and cleanup behind this small surface."""

    def open(self, config: dict[str, object]) -> dict[str, object]:
        """Start a bounded session."""
        ...

    def execute(self, session_id: str, code: str) -> dict[str, object]:
        """Enqueue one generated script without waiting for completion."""
        ...

    def read(self, session_id: str) -> dict[str, object]:
        """Return bounded latest status, result, and telemetry."""
        ...

    def cancel(self, session_id: str) -> dict[str, object]:
        """Request process-group cancellation."""
        ...

    def close(self, session_id: str) -> dict[str, object]:
        """Close one owned session."""
        ...

    def telemetry(self, session_id: str, config: dict[str, object]) -> dict[str, object]:
        """Configure bounded numerical telemetry for an owned actor."""
        ...

    def close_all(self) -> None:
        """Clean up every owned session on server disconnect."""
        ...


def create_manager() -> SessionManager:
    """Import persistent execution only when the server explicitly enables it."""
    from carla_agentic_toolkit.sandbox_sessions import SandboxSessionManager  # noqa: PLC0415

    return SandboxSessionManager()


def build_session_server() -> MCPServer:
    """Create a fresh manager per server, retained only for its MCP lifespan."""
    manager = create_manager()

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[None]:
        try:
            yield None
        finally:
            await asyncio.to_thread(manager.close_all)

    mcp = MCPServer(
        "carla-agentic-toolkit",
        title="CARLA Agentic Toolkit",
        version=__version__,
        lifespan=lifespan,
    )
    _register(mcp, manager)
    return mcp


def _register(mcp: MCPServer, manager: SessionManager) -> None:
    @mcp.tool(
        annotations=ToolAnnotations(
            title="CARLA Sandbox Session",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
        structured_output=True,
    )
    def carla_script_session(
        action: SessionAction,
        session_id: str | None = None,
        config: dict[str, object] | None = None,
        code: str | None = None,
    ) -> dict[str, object]:
        """Manage a bounded persistent Python namespace inside the existing Linux sandbox.

        Open returns an owner-scoped ID. Execute enqueues one script; read retrieves its
        latest bounded output and optional numerical telemetry. Cancel and close retain
        the same sandbox, ownership, resource, and cleanup rules as finite execution.
        """
        if action == "open":
            return manager.open(config or {})
        if session_id is None:
            message = "An owner-scoped session ID is required."
            raise ValueError(message)
        return _invoke(manager, action, session_id, config, code)


def _invoke(
    manager: SessionManager,
    action: SessionAction,
    session_id: str,
    config: dict[str, object] | None,
    code: str | None,
) -> dict[str, object]:
    if action == "execute":
        if code is None:
            message = "Execute requires Python code."
            raise ValueError(message)
        return manager.execute(session_id, code)
    if action == "telemetry":
        return manager.telemetry(session_id, config or {})
    handlers = {"read": manager.read, "cancel": manager.cancel, "close": manager.close}
    return handlers[action](session_id)
