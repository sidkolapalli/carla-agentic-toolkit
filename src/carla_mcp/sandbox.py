"""Rust-backed sandbox launcher for agent-authored CARLA scripts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_TRAFFIC_MANAGER_PORT = 8000
MAX_TCP_PORT = 65535


@dataclass(frozen=True, slots=True)
class ScriptOutcome:
    """Outcome of one sandboxed CARLA script run."""

    ok: bool
    result: object
    stdout: str
    resources: dict[str, object] | None = None
    error: str | None = None
    error_type: str | None = None
    sandbox: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation."""
        return {
            "ok": self.ok,
            "result": self.result,
            "stdout": self.stdout,
            "resources": self.resources or {},
            "error": self.error,
            "error_type": self.error_type,
            "sandbox": self.sandbox or {},
        }


@dataclass(frozen=True, slots=True)
class RunnerCommandRequest:
    """Inputs needed to build a Rust sandbox command."""

    runner: Path
    script_path: Path
    work_dir: Path
    output_dir: Path
    host: str
    port: int
    timeout_seconds: float
    traffic_manager_ports: Sequence[int]
    recorder_dir: str | None


def execute_script(
    code: str,
    *,
    host: str = "127.0.0.1",
    port: int = 2000,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    traffic_manager_ports: Sequence[int] = (),
) -> ScriptOutcome:
    """Run a CARLA script in the Rust sandbox process."""
    runner = _sandbox_runner()
    if runner is None:
        return ScriptOutcome(
            ok=False,
            result=None,
            stdout="",
            resources={},
            error=(
                "Rust sandbox runner is not built. "
                "Run `cargo build --release` in sandbox-runner."
            ),
            error_type="sandbox_runner_missing",
            sandbox={"runner": None},
        )
    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="carla-mcp-script-") as tmp_name:
        work_dir = Path(tmp_name)
        script_path = work_dir / "script.py"
        script_path.write_text(code, encoding="utf-8")
        command = _runner_command(
            RunnerCommandRequest(
                runner=runner,
                script_path=script_path,
                work_dir=work_dir,
                output_dir=output_dir,
                host=host,
                port=port,
                timeout_seconds=timeout_seconds,
                traffic_manager_ports=traffic_manager_ports,
                recorder_dir=os.environ.get("CARLA_MCP_RECORDER_DIR"),
            )
        )
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            timeout=timeout_seconds + 5.0,
        )
    return _decode_runner_output(completed, runner)


def _runner_command(request: RunnerCommandRequest) -> list[str]:
    """Build the Rust sandbox command."""
    tcp_ports = _allowed_tcp_ports(
        port=request.port,
        traffic_manager_ports=request.traffic_manager_ports,
    )
    command = [
        str(request.runner),
        "--python",
        sys.executable,
        "--module",
        "carla_mcp.script_runner",
        "--script",
        str(request.script_path),
        "--host",
        request.host,
        "--port",
        str(request.port),
        "--timeout-seconds",
        str(request.timeout_seconds),
        "--work-dir",
        str(request.work_dir),
        "--output-dir",
        str(request.output_dir),
    ]
    if request.recorder_dir:
        command.extend(("--recorder-dir", request.recorder_dir))
    for path in _read_only_paths():
        command.extend(("--read-only", str(path)))
    command.extend(("--read-write", str(request.work_dir)))
    command.extend(("--read-write", str(request.output_dir)))
    for tcp_port in tcp_ports:
        command.extend(("--tcp-connect", str(tcp_port)))
        command.extend(("--tcp-bind", str(tcp_port)))
    return command


def _decode_runner_output(
    completed: subprocess.CompletedProcess[str],
    runner: Path,
) -> ScriptOutcome:
    """Decode the Rust runner JSON output."""
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return ScriptOutcome(
            ok=False,
            result=None,
            stdout=completed.stdout,
            resources={},
            error=completed.stderr or "Rust sandbox runner did not return JSON.",
            error_type="sandbox_protocol_error",
            sandbox={"runner": str(runner), "exit_code": completed.returncode},
        )
    if not isinstance(payload, dict):
        return ScriptOutcome(
            ok=False,
            result=None,
            stdout="",
            resources={},
            error="Rust sandbox runner returned a non-object JSON payload.",
            error_type="sandbox_protocol_error",
            sandbox={"runner": str(runner), "exit_code": completed.returncode},
        )
    return ScriptOutcome(
        ok=bool(payload.get("ok")),
        result=payload.get("result"),
        stdout=str(payload.get("stdout", "")),
        resources=_object_mapping(payload.get("resources")),
        error=_optional_string(payload.get("error")),
        error_type=_optional_string(payload.get("error_type")),
        sandbox={
            "runner": str(runner),
            "exit_code": completed.returncode,
            "landlock": payload.get("landlock"),
            "timed_out": payload.get("timed_out", False),
            "child_exit_status": payload.get("exit_status"),
            "child_signal": payload.get("signal"),
            "stderr": completed.stderr,
        },
    )


def _optional_string(value: object) -> str | None:
    """Return a string value or None."""
    if value is None:
        return None
    return str(value)


def _object_mapping(value: object) -> dict[str, object]:
    """Return a string-keyed object mapping."""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _allowed_tcp_ports(*, port: int, traffic_manager_ports: Sequence[int]) -> tuple[int, ...]:
    """Return CARLA-related TCP ports allowed by Landlock."""
    ports = {port, port + 1, port + 2, DEFAULT_TRAFFIC_MANAGER_PORT}
    ports.update(int(item) for item in traffic_manager_ports)
    return tuple(sorted(item for item in ports if 0 < item <= MAX_TCP_PORT))


def _read_only_paths() -> tuple[Path, ...]:
    """Return host paths the child Python process may read/execute."""
    project_root = Path(__file__).resolve().parents[2]
    candidates = [
        project_root,
        Path(sys.executable).resolve().parent.parent,
        Path("/usr"),
        Path("/lib"),
        Path("/lib64"),
        Path("/dev"),
        Path("/sys"),
    ]
    return tuple(path for path in candidates if path.exists())


def _output_dir() -> Path:
    """Return the persistent directory exposed for script outputs."""
    configured = os.environ.get("CARLA_MCP_OUTPUT_DIR")
    path = Path(configured).expanduser() if configured else Path.cwd() / "carla-mcp-output"
    return path.resolve()


def _sandbox_runner() -> Path | None:
    """Return the Rust sandbox runner path when available."""
    env_path = os.environ.get("CARLA_MCP_SANDBOX")
    if env_path:
        candidate = Path(env_path)
        return candidate if candidate.exists() else None
    project_root = Path(__file__).resolve().parents[2]
    candidates = (
        project_root / "sandbox-runner" / "target" / "release" / "carla-mcp-sandbox",
        project_root / "sandbox-runner" / "target" / "debug" / "carla-mcp-sandbox",
    )
    return next((path for path in candidates if path.exists()), None)
