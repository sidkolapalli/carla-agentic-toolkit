"""MCP image publication failures preserve the already completed execution result."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest
from mcp import Client
from mcp.types import TextContent

from carla_agentic_toolkit import server as server_module
from carla_agentic_toolkit.sandbox import ScriptOutcome
from carla_agentic_toolkit.server import build_server

if TYPE_CHECKING:
    from pathlib import Path

    from mcp.types import CallToolResult

PNG_BYTES = b"\x89PNG\r\n\x1a\nimage"


async def _call_tool() -> CallToolResult:
    async with Client(build_server()) as client:
        return await client.call_tool("execute_carla_script", {"code": "result = 1"})


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [(b"not an image", "unsupported_image"), (PNG_BYTES + b"x" * 524288, "image_too_large")],
    ids=["unsupported", "oversized"],
)
def test_publication_failure_retains_successful_script_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: bytes, error_type: str
) -> None:
    """A completed simulator mutation cannot be mislabeled as failed execution."""
    path = tmp_path / "front.png"
    path.write_bytes(payload)
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path))
    outcome = ScriptOutcome(
        ok=True,
        result={"spawned": [7], "capture": "front.png"},
        stdout="mutation complete",
        snapshots={"carla-snapshot://captures/one": {"path": "front.png", "publish": True}},
        cleanup={"destroyed": [7], "failed": []},
    )
    monkeypatch.setattr(server_module, "execute_script", lambda *_args, **_kwargs: outcome)

    response = asyncio.run(_call_tool())
    result = response.structured_content

    assert result is not None
    _assert_text_only_success(response)
    _assert_execution_evidence(result, outcome)
    publication_error = cast("dict[str, object]", result["publication_error"])
    assert publication_error["error_type"] == error_type
    assert isinstance(publication_error["error"], str)


def _assert_text_only_success(response: CallToolResult) -> None:
    assert response.is_error is False
    assert [type(item) for item in response.content] == [TextContent]


def _assert_execution_evidence(result: dict[str, object], outcome: ScriptOutcome) -> None:
    assert {key: result[key] for key in outcome.to_dict()} == outcome.to_dict()


def test_failed_execution_retains_its_original_error_without_publication_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The publication layer never rewrites a genuine execution failure."""
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path))
    outcome = ScriptOutcome(
        ok=False,
        result=None,
        stdout="partial",
        error="script failed",
        error_type="script_error",
        snapshots={"carla-snapshot://captures/one": {"path": "missing.png", "publish": True}},
    )
    monkeypatch.setattr(server_module, "execute_script", lambda *_args, **_kwargs: outcome)

    response = asyncio.run(_call_tool())

    assert response.is_error is True
    assert response.structured_content == outcome.to_dict()
