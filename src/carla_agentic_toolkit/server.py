"""MCP server entrypoint for CARLA script execution."""

from __future__ import annotations

import json
import os
import threading
from typing import cast

from mcp.server import MCPServer
from mcp.types import (
    AudioContent,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    ToolAnnotations,
)

from carla_agentic_toolkit import __version__
from carla_agentic_toolkit.output_content import (
    OutputContentError,
    PublishedCapture,
    published_captures,
    read_capture_resource,
)
from carla_agentic_toolkit.sandbox import ScriptOutcome, execute_script, output_dir_path

_execution_lock = threading.Lock()


def build_server() -> MCPServer:
    """Build the MCP server with one script-execution tool."""
    mcp = _configured_server()
    _register_script_tool(mcp)
    _register_capture_resource(mcp)
    _register_prompts(mcp)
    if os.environ.get("CARLA_AGENTIC_TOOLKIT_MANAGED_EXPERIMENTS") == "1":
        from carla_agentic_toolkit.managed_mcp import register_managed_tool  # noqa: PLC0415

        register_managed_tool(mcp)
    return mcp


def _configured_server() -> MCPServer:
    if os.environ.get("CARLA_AGENTIC_TOOLKIT_ENABLE_SCRIPT_SESSIONS") == "1":
        from carla_agentic_toolkit.session_mcp import build_session_server  # noqa: PLC0415

        return build_session_server()
    return MCPServer("carla-agentic-toolkit", title="CARLA Agentic Toolkit", version=__version__)


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
        with _execution_lock:
            outcome = execute_script(
                code,
                host=host,
                port=port,
                timeout_seconds=timeout_seconds,
                traffic_manager_ports=traffic_manager_ports or (),
            )
        return _tool_result(outcome)


def _tool_result(outcome: ScriptOutcome) -> dict[str, object]:
    """Build one structured result with optional native MCP image content."""
    payload = outcome.to_dict()
    captures = _optional_captures(outcome, payload)
    return cast(
        "dict[str, object]",
        CallToolResult(
            content=_result_content(payload, captures),
            structured_content=payload,
            is_error=payload["ok"] is not True,
        ),
    )


def _optional_captures(
    outcome: ScriptOutcome,
    payload: dict[str, object],
) -> tuple[PublishedCapture, ...]:
    if not outcome.ok:
        return ()
    try:
        return published_captures(
            outcome.snapshots or {},
            output_dir_path(),
            text_bytes=len(json.dumps(payload).encode()),
        )
    except OutputContentError as exc:
        payload.update(ok=False, result=None, error=str(exc), error_type=exc.error_type)
        return ()


def _result_content(
    payload: dict[str, object],
    captures: tuple[PublishedCapture, ...],
) -> list[TextContent | ImageContent | AudioContent | ResourceLink | EmbeddedResource]:
    content: list[TextContent | ImageContent | AudioContent | ResourceLink | EmbeddedResource] = [
        TextContent(type="text", text=json.dumps(payload))
    ]
    for capture in captures:
        content.extend(
            (
                ImageContent(data=capture.data, mime_type=capture.mime_type),
                ResourceLink(
                    name=capture.name,
                    uri=capture.resource_uri,
                    mime_type=capture.mime_type,
                    size=capture.size,
                ),
            )
        )
    return content


def _register_capture_resource(mcp: MCPServer) -> None:
    """Expose validated durable captures through one bounded resource template."""

    @mcp.resource(
        "carla-output://capture/{token}",
        name="carla-capture",
        description="A validated PNG or JPEG created under CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR.",
        mime_type="application/octet-stream",
    )
    def read_capture(token: str) -> bytes:
        """Read one opaque capture token."""
        return read_capture_resource(token, output_dir_path())


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
    def capture_actor_view() -> str:
        """Prompt for returning a visual frame from one CARLA actor."""
        return (
            "Use execute_carla_script. Resolve or list the target actor, attach an RGB "
            "camera, call api.capture_sensor_frame(..., publish=True) below "
            "CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR, "
            "detach the sensor in finally, and summarize the returned image and metadata."
        )

    @mcp.prompt()
    def setup_reproducible_session() -> str:
        """Prompt for configuring deterministic CARLA stepping with one script."""
        return (
            "Write one execute_carla_script Python script that configures "
            "synchronous mode with api.set_sync_mode(enabled=True), advances frames "
            "with api.tick_n(), and returns before/after frame information."
        )

    @mcp.prompt()
    def run_visual_showcase() -> str:
        """Prompt for a self-cleaning visual scenario."""
        return (
            "Use one execute_carla_script call to create an 8-second visual CARLA demo. "
            "Save the current weather. Spawn one red Tesla Model 3 at the first free "
            "spawn point with role_name carla-agentic-toolkit-showcase. Apply rainy golden-hour "
            "weather and vehicle lights, apply gentle throttle with api.apply_vehicle_control(), "
            "and call api.watch_actor(actor_id, seconds=8.0) for a smooth yaw-relative "
            "chase view that restores the spectator, then apply the brake. "
            "Check every operation for an ok: false result. "
            "Attach a 320-by-180 RGB camera and publish "
            "a frame to captures/showcase.png. Return "
            "map, speed before/after, capture metadata, restored-state checks, and "
            "leftovers. In finally, detach sensors, destroy only actors created by this "
            "script, and restore both weather and spectator transform."
        )


def main() -> None:
    """Run the CARLA Agentic Toolkit server."""
    build_server().run()
