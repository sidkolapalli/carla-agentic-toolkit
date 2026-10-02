"""Persistent sandbox sessions are isolated per MCP connection owner and opt-in."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from mcp import Client

from carla_agentic_toolkit import session_mcp
from carla_agentic_toolkit.server import build_server

if TYPE_CHECKING:
    import pytest


class FakeManager:
    """Track one connection's local session identities without launching a sandbox."""

    def __init__(self) -> None:
        """Create an empty per-owner registry."""
        self.known = False
        self.closed = False

    def open(self, config: dict[str, object]) -> dict[str, object]:
        """Create one locally scoped identifier."""
        self.known = True
        return {"session_id": "local-session", "config": config}

    def execute(self, session_id: str, code: str) -> dict[str, object]:
        """Require this manager's session and retain no generated code."""
        return {**self.read(session_id), "accepted": bool(code)}

    def read(self, session_id: str) -> dict[str, object]:
        """Reject cross-server identity reuse."""
        if not self.known or session_id != "local-session":
            message = "Unknown session for this owner."
            raise ValueError(message)
        return {"session_id": session_id}

    def cancel(self, session_id: str) -> dict[str, object]:
        """Forward cancellation to the owner's manager."""
        return self.read(session_id)

    def close(self, session_id: str) -> dict[str, object]:
        """Forward closure to the owner's manager."""
        return self.read(session_id)

    def telemetry(self, session_id: str, config: dict[str, object]) -> dict[str, object]:
        """Forward only structured telemetry configuration."""
        return {**self.read(session_id), "telemetry": config}

    def close_all(self) -> None:
        """Record cleanup at MCP lifespan shutdown."""
        self.closed = True


def test_opt_in_session_manager_is_per_server_and_closed_on_disconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session IDs cannot cross server instances, and disconnect closes every owned worker."""
    managers: list[FakeManager] = []

    def create() -> FakeManager:
        manager = FakeManager()
        managers.append(manager)
        return manager

    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_ENABLE_SCRIPT_SESSIONS", "1")
    monkeypatch.setattr(session_mcp, "create_manager", create)
    first = build_server()
    second = build_server()

    async def run() -> None:
        async with Client(first) as owner, Client(second) as stranger:
            opened = await owner.call_tool("carla_script_session", {"action": "open"})
            assert opened.is_error is False
            rejected = await stranger.call_tool(
                "carla_script_session",
                {"action": "read", "session_id": "local-session"},
            )
            assert rejected.is_error is True
            configured = await owner.call_tool(
                "carla_script_session",
                {
                    "action": "telemetry",
                    "session_id": "local-session",
                    "config": {"actor_id": 1},
                },
            )
            assert configured.is_error is False
        assert all(manager.closed for manager in managers)

    asyncio.run(run())


def test_session_tool_absent_without_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """Existing finite script clients retain their single-tool default."""
    monkeypatch.delenv("CARLA_AGENTIC_TOOLKIT_ENABLE_SCRIPT_SESSIONS", raising=False)
    monkeypatch.delenv("CARLA_AGENTIC_TOOLKIT_MANAGED_EXPERIMENTS", raising=False)
    assert [tool.name for tool in asyncio.run(build_server().list_tools())] == [
        "execute_carla_script",
    ]
