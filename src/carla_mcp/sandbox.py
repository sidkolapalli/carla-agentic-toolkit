"""Rust-backed sandbox launcher for agent-authored CARLA scripts."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, cast

from carla_mcp.adapter import PythonCarlaAdapter
from carla_mcp.ownership import (
    OWNERSHIP_FILENAME,
    RunOwnership,
    cleanup_owned_actors,
    cleanup_report,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_TRAFFIC_MANAGER_PORT = 8000
MAX_TCP_PORT = 65535
MAX_CARLA_BASE_PORT = MAX_TCP_PORT - 2
MAX_TIMEOUT_SECONDS = 3600.0


@dataclass(frozen=True, slots=True)
class ScriptOutcome:
    """Outcome of one sandboxed CARLA script run."""

    ok: bool
    result: object
    stdout: str
    snapshots: dict[str, object] | None = None
    error: str | None = None
    error_type: str | None = None
    sandbox: dict[str, object] | None = None
    cleanup: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation."""
        return {
            "ok": self.ok,
            "result": self.result,
            "stdout": self.stdout,
            "snapshots": self.snapshots or {},
            "error": self.error,
            "error_type": self.error_type,
            "sandbox": self.sandbox or {},
            "cleanup": self.cleanup or cleanup_report(),
        }


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    """Validated public execution values."""

    host: str
    port: int
    timeout_seconds: float
    traffic_manager_ports: Sequence[int]


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
    request = ExecutionRequest(host, port, timeout_seconds, traffic_manager_ports)
    invalid = _validate_execution(request)
    if invalid is not None:
        return _failure("invalid_request", invalid)
    runner = _sandbox_runner()
    if runner is None:
        return _failure(
            "sandbox_runner_missing",
            "Rust sandbox runner is not built. Run `cargo build --release` in sandbox-runner.",
        )
    return _execute_with_runner(
        code,
        runner=runner,
        request=request,
    )


def _execute_with_runner(
    code: str,
    *,
    runner: Path,
    request: ExecutionRequest,
) -> ScriptOutcome:
    """Set up and launch one already-validated execution."""
    try:
        output_dir = output_dir_path()
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
                    host=request.host,
                    port=request.port,
                    timeout_seconds=request.timeout_seconds,
                    traffic_manager_ports=request.traffic_manager_ports,
                    recorder_dir=os.environ.get("CARLA_MCP_RECORDER_DIR"),
                )
            )
            try:
                completed = subprocess.run(
                    command,
                    check=False,
                    text=True,
                    capture_output=True,
                    timeout=request.timeout_seconds + 5.0,
                )
            except subprocess.TimeoutExpired:
                return _failure(
                    "sandbox_watchdog_error",
                    "Sandbox wrapper exceeded its cleanup deadline.",
                    runner,
                )
            except OSError as exc:
                return _failure("sandbox_launcher_error", str(exc), runner)
            outcome = _decode_runner_output(completed, runner)
            if outcome.ok:
                return outcome
            return _cleanup_failed_execution(
                outcome,
                ownership_path=work_dir / OWNERSHIP_FILENAME,
                request=request,
            )
    except OSError as exc:
        return _failure("sandbox_setup_error", str(exc), runner)


def _validate_execution(request: ExecutionRequest) -> str | None:
    """Return the first invalid public execution value."""
    for error in (
        _host_error(request.host),
        _port_error(request.port),
        _timeout_error(request.timeout_seconds),
        _traffic_ports_error(request.traffic_manager_ports),
    ):
        if error is not None:
            return error
    return None


def _host_error(host: object) -> str | None:
    if not isinstance(host, str) or not host.strip():
        return "host must be a non-empty string."
    return None


def _port_error(port: object) -> str | None:
    if isinstance(port, bool) or not isinstance(port, int):
        return f"port must be an integer in 1..{MAX_CARLA_BASE_PORT}."
    if not 1 <= port <= MAX_CARLA_BASE_PORT:
        return f"port must be an integer in 1..{MAX_CARLA_BASE_PORT}."
    return None


def _timeout_error(timeout_seconds: object) -> str | None:
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int | float):
        return f"timeout_seconds must be finite and in (0, {MAX_TIMEOUT_SECONDS:g}]."
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        return f"timeout_seconds must be finite and in (0, {MAX_TIMEOUT_SECONDS:g}]."
    return None


def _traffic_ports_error(traffic_manager_ports: Sequence[object]) -> str | None:
    for port in traffic_manager_ports:
        if isinstance(port, bool) or not isinstance(port, int):
            return f"traffic_manager_ports must contain integers in 1..{MAX_TCP_PORT}."
        if not 1 <= port <= MAX_TCP_PORT:
            return f"traffic_manager_ports must contain integers in 1..{MAX_TCP_PORT}."
    return None


def _failure(error_type: str, error: str, runner: Path | None = None) -> ScriptOutcome:
    """Return one normalized launcher failure."""
    return ScriptOutcome(
        ok=False,
        result=None,
        stdout="",
        snapshots={},
        error=error,
        error_type=error_type,
        sandbox={"runner": str(runner) if runner is not None else None},
        cleanup=cleanup_report(),
    )


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
        "--ownership-file",
        str(request.work_dir / OWNERSHIP_FILENAME),
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
            snapshots={},
            error=completed.stderr or "Rust sandbox runner did not return JSON.",
            error_type="sandbox_protocol_error",
            sandbox={"runner": str(runner), "exit_code": completed.returncode},
            cleanup=cleanup_report(),
        )
    if not isinstance(payload, dict):
        return ScriptOutcome(
            ok=False,
            result=None,
            stdout="",
            snapshots={},
            error="Rust sandbox runner returned a non-object JSON payload.",
            error_type="sandbox_protocol_error",
            sandbox={"runner": str(runner), "exit_code": completed.returncode},
            cleanup=cleanup_report(),
        )
    return ScriptOutcome(
        ok=bool(payload.get("ok")),
        result=payload.get("result"),
        stdout=str(payload.get("stdout", "")),
        snapshots=_object_mapping(payload.get("snapshots")),
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
        cleanup=_object_mapping(payload.get("cleanup")),
    )


def _cleanup_failed_execution(
    outcome: ScriptOutcome,
    *,
    ownership_path: Path,
    request: ExecutionRequest,
) -> ScriptOutcome:
    """Cleanup a failed run while its ownership journal still exists."""
    try:
        adapter = PythonCarlaAdapter(host=request.host, port=request.port, timeout=5.0)
        report = cleanup_owned_actors(adapter, RunOwnership(ownership_path))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        failure: dict[str, object] = {"actor_id": None, "error": str(exc)}
        report = cleanup_report(failures=(failure,))
    if not any(report.values()):
        return outcome
    previous = outcome.cleanup or cleanup_report()
    combined = {
        "attempted_actor_ids": [
            *_list_value(previous, "attempted_actor_ids"),
            *_list_value(report, "attempted_actor_ids"),
        ],
        "destroyed_actor_ids": [
            *_list_value(previous, "destroyed_actor_ids"),
            *_list_value(report, "destroyed_actor_ids"),
        ],
        "failures": [
            *_list_value(previous, "failures"),
            *_list_value(report, "failures"),
        ],
    }
    return replace(outcome, cleanup=combined)


def _list_value(payload: dict[str, object], key: str) -> list[object]:
    value = payload.get(key)
    return cast("list[object]", value) if isinstance(value, list) else []


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
        Path("/etc/nsswitch.conf"),
        Path("/etc/host.conf"),
        Path("/etc/hosts"),
        Path("/etc/resolv.conf"),
        Path("/etc/gai.conf"),
    ]
    return tuple(path for path in candidates if path.exists())


def output_dir_path() -> Path:
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
