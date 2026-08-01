"""MCP server entrypoint for CARLA script execution."""

from __future__ import annotations

import json
from typing import cast

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from carla_mcp import __version__
from carla_mcp.sandbox import execute_script


def build_server() -> MCPServer:
    """Build the MCP server with one script-execution tool."""
    mcp = MCPServer("carla-mcp", title="CARLA MCP", version=__version__)
    _register_script_tool(mcp)
    _register_prompts(mcp)
    return mcp


def _register_script_tool(mcp: MCPServer) -> None:
    """Register the single CARLA code-execution tool."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Execute CARLA Script",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
        structured_output=True,
    )
    def execute_carla_script(
        code: str,
        host: str = "127.0.0.1",
        port: int = 2000,
        timeout_seconds: float = 30.0,
        traffic_manager_ports: list[int] | None = None,
    ) -> dict[str, object]:
        """Run one Python script against the curated CARLA `api` object.

        Scripts run in real Python through a Rust Landlock sandbox wrapper. The
        script receives an injected `api` object and should assign its final
        JSON-compatible summary to `result`.
        """
        outcome = execute_script(
            code,
            host=host,
            port=port,
            timeout_seconds=timeout_seconds,
            traffic_manager_ports=traffic_manager_ports or (),
        )
        payload = outcome.to_dict()
        return cast(
            "dict[str, object]",
            CallToolResult(
                content=[TextContent(type="text", text=json.dumps(payload))],
                structured_content=payload,
                is_error=not outcome.ok,
            ),
        )


def _register_prompts(mcp: MCPServer) -> None:
    """Register script-oriented workflow prompts."""

    @mcp.prompt()
    def diagnose_carla() -> str:
        """Prompt for diagnosing a CARLA session with one script."""
        return (
            "Write one execute_carla_script Python script that calls "
            "api.health_check(), api.get_world_state(), and returns a compact "
            "result dict with connection status, map, actor counts, and next action."
        )

    @mcp.prompt()
    def setup_reproducible_session() -> str:
        """Prompt for configuring deterministic CARLA stepping with one script."""
        return (
            "Write one execute_carla_script Python script that configures "
            "synchronous mode with api.set_sync_mode(enabled=True), advances frames "
            "with api.tick_n(), and returns before/after frame information."
        )


def main() -> None:
    """Run the CARLA MCP server."""
    build_server().run()
