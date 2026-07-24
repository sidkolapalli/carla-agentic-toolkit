"""Behavior specs for the MCP server entrypoint."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from mcp import Client
from mcp.server import MCPServer

from carla_mcp import server as server_module
from carla_mcp.sandbox import ScriptOutcome
from carla_mcp.server import build_server

if TYPE_CHECKING:
    import pytest
    from mcp.types import CallToolResult, Implementation


def _successful_script(*_args: object, **_kwargs: object) -> ScriptOutcome:
    """Return a successful sandbox result without launching a subprocess."""
    return ScriptOutcome(ok=True, result=1, stdout="", resources={})


async def _call_script_tool(
    server: MCPServer,
) -> tuple[str, Implementation | None, CallToolResult]:
    """Call the server through the modern in-memory MCP transport."""
    async with Client(server) as client:
        result = await client.call_tool("execute_carla_script", {"code": "result = 1"})
        return client.protocol_version, client.server_info, result


def test_build_server_registers_with_installed_mcp_sdk() -> None:
    """The server should build with the installed official MCP Python SDK."""
    server = build_server()

    assert isinstance(server, MCPServer)


def test_build_server_exposes_only_script_execution_tool() -> None:
    """The public MCP surface should be one code-execution tool."""
    server = build_server()

    tools = asyncio.run(server.list_tools())

    assert tuple(tool.name for tool in tools) == ("execute_carla_script",)


def test_server_supports_latest_mcp_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server should negotiate MCP 2026-07-28 and return structured output."""
    monkeypatch.setattr(server_module, "execute_script", _successful_script)

    protocol_version, server_info, result = asyncio.run(_call_script_tool(build_server()))

    assert server_info is not None
    assert result.structured_content is not None
    assert {
        "protocol_version": protocol_version,
        "server_name": server_info.name,
        "server_version": server_info.version,
        "is_error": result.is_error,
        "result": result.structured_content["result"],
    } == {
        "protocol_version": "2026-07-28",
        "server_name": "carla-mcp",
        "server_version": "0.1.0",
        "is_error": False,
        "result": 1,
    }
