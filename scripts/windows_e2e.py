"""Verify the Windows launcher, WSL2, MCP stdio, and real Landlock runner."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def main() -> int:
    """Run the complete Windows-to-WSL2 path without requiring CARLA."""
    launcher = _launcher()
    preflight = subprocess.run(
        [launcher, "--check"],
        env=dict(os.environ),
        text=True,
        capture_output=True,
        check=False,
    )
    if preflight.returncode != 0:
        sys.stderr.write(preflight.stderr or preflight.stdout)
        return preflight.returncode
    try:
        preflight_report = _mapping(json.loads(preflight.stdout), "preflight report")
        mcp_report = asyncio.run(_verify_mcp(launcher))
    except (json.JSONDecodeError, RuntimeError, TypeError) as error:
        sys.stderr.write(f"Windows E2E failed: {error}\n")
        return 1
    payload = {"ok": True, "preflight": preflight_report, "mcp": mcp_report}
    sys.stdout.write(json.dumps(payload) + "\n")
    return 0


async def _verify_mcp(launcher: Path) -> dict[str, object]:
    server = StdioServerParameters(
        command=str(launcher),
        args=[],
        env=dict(os.environ),
    )
    async with (
        stdio_client(server) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        initialized = await session.initialize()
        tools = await session.list_tools()
        tool_names = [tool.name for tool in tools.tools]
        _require(
            f"unexpected tools: {tool_names}",
            condition=tool_names == ["execute_carla_script"],
        )

        safe = await session.call_tool(
            "execute_carla_script",
            {"code": "result = api.describe_api()", "timeout_seconds": 5},
        )
        safe_result = _mapping(safe.structured_content, "safe tool result")
        sandbox = _mapping(safe_result.get("sandbox"), "sandbox metadata")
        landlock = _mapping(sandbox.get("landlock"), "Landlock metadata")
        _require(
            f"safe script failed: {safe_result}",
            condition=safe_result.get("ok") is True,
        )
        _require(
            "Landlock was not fully enforced",
            condition=landlock.get("ruleset_enforced") is True,
        )

        project_dir = os.environ["CARLA_MCP_WSL_PROJECT"]
        _require(
            f"WSL project is not writable by its user: {project_dir}",
            condition=_wsl_test("-w", project_dir),
        )
        probe_path = str(PurePosixPath(project_dir) / f".carla-mcp-landlock-probe-{uuid4().hex}")
        probe_code = f"result = api.export_evidence_packet({json.dumps(probe_path)})"
        blocked = await session.call_tool(
            "execute_carla_script",
            {"code": probe_code, "timeout_seconds": 5},
        )
        blocked_result = _mapping(blocked.structured_content, "blocked write result")
        _require(
            f"write outside the output directory was not denied: {blocked_result}",
            condition=blocked_result.get("error_type") == "PermissionError",
        )
        artifact_exists = _wsl_test("-e", probe_path)
        if artifact_exists:
            _remove_wsl_probe(probe_path)
        _require(
            f"blocked write left an artifact: {probe_path}",
            condition=not artifact_exists,
        )

    return {
        "server": initialized.server_info.name,
        "tools": tool_names,
        "landlock_enforced": True,
        "filesystem_write_blocked": True,
    }


def _launcher() -> Path:
    name = "carla-mcp-windows.exe" if os.name == "nt" else "carla-mcp-windows"
    return Path(sys.executable).with_name(name)


def _wsl_test(operator: str, path: str) -> bool:
    """Run one trusted filesystem predicate inside the selected WSL2 distribution."""
    completed = subprocess.run(
        [
            os.environ.get("CARLA_MCP_WSL_COMMAND", "wsl.exe"),
            "--distribution",
            os.environ["CARLA_MCP_WSL_DISTRO"],
            "--exec",
            "/usr/bin/test",
            operator,
            path,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode not in (0, 1):
        message = completed.stderr.strip() or "WSL filesystem check failed"
        raise RuntimeError(message)
    return completed.returncode == 0


def _remove_wsl_probe(path: str) -> None:
    """Remove only the unique probe path after an unexpected sandbox escape."""
    subprocess.run(
        [
            os.environ.get("CARLA_MCP_WSL_COMMAND", "wsl.exe"),
            "--distribution",
            os.environ["CARLA_MCP_WSL_DISTRO"],
            "--exec",
            "/usr/bin/rm",
            "-rf",
            "--",
            path,
        ],
        check=False,
    )


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        message = f"{label} was not an object: {value!r}"
        raise TypeError(message)
    return {str(key): item for key, item in value.items()}


def _require(message: str, *, condition: bool) -> None:
    if not condition:
        raise RuntimeError(message)


if __name__ == "__main__":
    raise SystemExit(main())
