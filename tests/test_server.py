"""Behavior specs for the MCP server entrypoint."""

from __future__ import annotations

import asyncio
import base64
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from mcp import Client
from mcp.server import MCPServer
from mcp.types import BlobResourceContents, GetPromptResult, ImageContent, ResourceLink, TextContent

from carla_agentic_toolkit import server as server_module
from carla_agentic_toolkit.output_content import capture_resource_uri
from carla_agentic_toolkit.sandbox import ScriptOutcome
from carla_agentic_toolkit.server import build_server

if TYPE_CHECKING:
    from mcp.types import CallToolResult, Implementation, ReadResourceResult


def _successful_script(*_args: object, **_kwargs: object) -> ScriptOutcome:
    """Return a successful sandbox result without launching a subprocess."""
    return ScriptOutcome(ok=True, result=1, stdout="", snapshots={})


def _capture_script(path: str, *, publish: bool = True) -> object:
    """Return a sandbox stub that reports one durable capture."""

    def captured(*_args: object, **_kwargs: object) -> ScriptOutcome:
        return ScriptOutcome(
            ok=True,
            result={"capture": path},
            stdout="",
            snapshots={
                "carla-snapshot://captures/capture-1": {
                    "capture_id": "capture-1",
                    "path": path,
                    "mime_type": "image/png",
                    "publish": publish,
                }
            },
        )

    return captured


def _failed_script(*_args: object, **_kwargs: object) -> ScriptOutcome:
    """Return a failed sandbox result without launching a subprocess."""
    return ScriptOutcome(
        ok=False,
        result=None,
        stdout="partial output",
        error="boom",
        error_type="sandbox_error",
    )


async def _read_resource(server: MCPServer, uri: str) -> ReadResourceResult:
    """Read one resource through the negotiated in-memory MCP transport."""
    async with Client(server) as client:
        return await client.read_resource(uri)


async def _call_script_tool(
    server: MCPServer,
    arguments: dict[str, object] | None = None,
) -> tuple[str, Implementation | None, CallToolResult]:
    """Call the server through the modern in-memory MCP transport."""
    async with Client(server) as client:
        result = await client.call_tool("execute_carla_script", arguments or {"code": "result = 1"})
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


def test_server_exposes_only_a_bounded_capture_resource_template() -> None:
    """Durable captures should be resources without exposing live simulator state."""
    server = build_server()

    resources = asyncio.run(server.list_resources())
    templates = asyncio.run(server.list_resource_templates())

    assert resources == []
    assert [str(template.uri_template) for template in templates] == [
        "carla-output://capture/{token}"
    ]


def test_capture_resource_template_returns_bounded_binary_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resource links emitted by the tool should be readable through MCP."""
    image = b"\x89PNG\r\n\x1a\nimage"
    (tmp_path / "front.png").write_bytes(image)
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path))
    uri = capture_resource_uri(Path("front.png"))

    result = asyncio.run(_read_resource(build_server(), uri))
    content = cast("BlobResourceContents", result.contents[0])

    assert base64.b64decode(content.blob) == image


def test_server_prompts_cover_diagnosis_capture_synchronous_stepping_and_demo() -> None:
    """User-selected MCP prompts should teach the common workflow shapes."""
    prompts = asyncio.run(build_server().list_prompts())

    assert [prompt.name for prompt in prompts] == [
        "diagnose_carla",
        "capture_actor_view",
        "setup_synchronous_stepping",
        "run_visual_showcase",
    ]


@pytest.mark.parametrize(
    "fragment",
    [
        'health["connected"]',
        'health["warnings"]',
        "health-only",
        "do not call api.get_world_state()",
        "Only if connected is True and there are no compatibility warnings",
        "api.health_check()",
        "compact",
    ],
)
def test_diagnosis_prompt_requires_verified_health_before_world_inspection(fragment: str) -> None:
    """The served diagnosis workflow must not unconditionally attach to an incompatible world."""
    prompt = asyncio.run(build_server().get_prompt("diagnose_carla"))
    assert isinstance(prompt, GetPromptResult)
    content = cast("TextContent", prompt.messages[0].content)

    assert fragment in content.text


@pytest.mark.parametrize(
    ("fragment", "present"),
    [
        ("api.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)", True),
        ('["previous_settings"]', True),
        ("finally", True),
        ("api.restore_world_settings(previous_settings)", True),
        ("persistent session", True),
        ("owns ticking", True),
        ("restores", True),
        ("close", True),
        ("deterministic", False),
        ("reproducible", False),
    ],
)
def test_synchronous_stepping_prompt_restores_settings_and_explains_tick_ownership(
    fragment: str, *, present: bool
) -> None:
    """The stepping workflow must restore its clock configuration before returning."""
    prompt = asyncio.run(build_server().get_prompt("setup_synchronous_stepping"))
    assert isinstance(prompt, GetPromptResult)
    content = cast("TextContent", prompt.messages[0].content)

    assert (fragment in content.text) is present


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
        "server_name": "carla-agentic-toolkit",
        "server_version": "0.1.0",
        "is_error": False,
        "result": 1,
    }


def test_server_can_return_capture_as_image_and_resource_link(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An opt-in capture should use native MCP visual and resource content."""
    capture = tmp_path / "front.png"
    capture.write_bytes(b"\x89PNG\r\n\x1a\nimage")
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(server_module, "execute_script", _capture_script("front.png"))

    _, _, result = asyncio.run(_call_script_tool(build_server()))

    image = cast("ImageContent", result.content[1])
    resource = cast("ResourceLink", result.content[2])
    assert {
        "is_error": result.is_error,
        "content_types": [type(item) for item in result.content],
        "mime_type": image.mime_type,
        "resource_scheme": str(resource.uri).startswith("carla-output://capture/"),
    } == {
        "is_error": False,
        "content_types": [TextContent, ImageContent, ResourceLink],
        "mime_type": "image/png",
        "resource_scheme": True,
    }


def test_server_keeps_json_only_fallback_when_images_are_not_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Clients that do not request visual content should retain the old result shape."""
    (tmp_path / "front.png").write_bytes(b"\x89PNG\r\n\x1a\nimage")
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(
        server_module,
        "execute_script",
        _capture_script("front.png", publish=False),
    )

    _, _, result = asyncio.run(_call_script_tool(build_server()))

    assert [type(item) for item in result.content] == [TextContent]
    assert result.is_error is False


def test_server_rejects_capture_path_outside_output_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Image publication errors should be structured MCP tool failures."""
    secret = tmp_path.parent / "secret.png"
    secret.write_bytes(b"\x89PNG\r\n\x1a\nsecret")
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(server_module, "execute_script", _capture_script("../secret.png"))

    _, _, result = asyncio.run(_call_script_tool(build_server()))

    assert result.is_error is True
    assert result.structured_content["error_type"] == "image_path_rejected"


def test_server_serializes_concurrent_script_executions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent tool calls must not overlap against shared simulator state."""
    active = 0
    maximum_active = 0
    state_lock = threading.Lock()

    def controlled_script(*_args: object, **_kwargs: object) -> ScriptOutcome:
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.05)
        with state_lock:
            active -= 1
        return _successful_script()

    async def call_concurrently() -> None:
        server = build_server()
        await asyncio.gather(
            server.call_tool("execute_carla_script", {"code": "result = 1"}),
            server.call_tool("execute_carla_script", {"code": "result = 2"}),
        )

    monkeypatch.setattr(server_module, "execute_script", controlled_script)

    asyncio.run(call_concurrently())

    assert maximum_active == 1


def test_server_marks_failed_script_as_mcp_tool_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fatal sandbox failures should set MCP isError without losing diagnostics."""
    monkeypatch.setattr(server_module, "execute_script", _failed_script)

    _, _, result = asyncio.run(_call_script_tool(build_server()))

    assert result.is_error is True
    assert result.structured_content is not None
    assert result.structured_content["error_type"] == "sandbox_error"
    assert result.structured_content["stdout"] == "partial output"
