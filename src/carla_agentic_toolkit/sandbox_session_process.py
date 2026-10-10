"""Trusted persistent sandbox owner; lease survives through actor and settings cleanup."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING

from carla_agentic_toolkit.connection_journal import CONNECTION_FILENAME, ConnectionJournal
from carla_agentic_toolkit.ownership import OWNERSHIP_FILENAME, RunOwnership
from carla_agentic_toolkit.sandbox import (
    ExecutionRequest,
    RunnerCommandRequest,
    _decode_runner_output,
    _failure,
    _runner_command,
    _sandbox_runner,
    output_dir_path,
)
from carla_agentic_toolkit.script_recovery import cleanup_script_ownership
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
from carla_agentic_toolkit.session_protocol import SessionConfig, write_message
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from carla_agentic_toolkit.sandbox import ScriptOutcome


class SessionProcess:
    """Keep durable recovery evidence and the kernel lease until verified child cleanup."""

    def __init__(self, session_id: str, config: SessionConfig) -> None:
        """Create private paths only after a lease is successfully acquired."""
        config.validate()
        self.config = config
        self.absolute_deadline = time.monotonic() + config.absolute_timeout_seconds
        self.session_id = session_id
        self.lease = SimulatorLease(config.host, config.port)
        self.root = Path(tempfile.mkdtemp(prefix="carla-script-session-"))
        self.work = self.root / "work"
        self.control = self.root / "control"
        try:
            self.runner = _sandbox_runner()
            self.work.mkdir(mode=0o700)
            self.control.mkdir(mode=0o700)
            self.lease.__enter__()
        except BaseException:
            shutil.rmtree(self.root)
            raise
        self.cancel_path = self.control / "cancel"
        self.process: subprocess.Popen[str] | None = None
        self.request = ExecutionRequest(
            config.host,
            config.port,
            config.absolute_timeout_seconds,
            config.traffic_manager_ports,
            streaming_port=config.streaming_port,
            secondary_port=config.secondary_port,
        )

    def start(self) -> None:
        """Mark ownership dirty before starting a sandbox with the inherited lease descriptor."""
        if self.runner is None:
            message = "Rust sandbox runner is not built."
            raise RuntimeError(message)
        config_path = self.work / "session.json"
        RunOwnership(self.work / OWNERSHIP_FILENAME).clear()
        RunSettings(self.work / SETTINGS_FILENAME).initialize()
        ConnectionJournal(self.work / CONNECTION_FILENAME).initialize()
        write_message(
            config_path,
            {
                "session_id": self.session_id,
                **asdict(self.config),
                "absolute_deadline_monotonic": self.absolute_deadline,
            },
        )
        output = output_dir_path()
        output.mkdir(parents=True, exist_ok=True)
        request = RunnerCommandRequest(
            self.runner,
            config_path,
            self.work,
            output,
            self.config.host,
            self.config.port,
            self.config.absolute_timeout_seconds,
            self.config.traffic_manager_ports,
            os.environ.get("CARLA_AGENTIC_TOOLKIT_RECORDER_DIR"),
            streaming_port=self.config.streaming_port,
            secondary_port=self.config.secondary_port,
        )
        command = _runner_command(request)
        command[command.index("--module") + 1] = "carla_agentic_toolkit.persistent_runner"
        command.extend(("--cancel-file", str(self.cancel_path), "--read-only", str(self.control)))
        self.lease.mark_dirty(self._recovery())
        self.process = subprocess.Popen(  # noqa: S603 - fixed Rust runner, validated separate argv.
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(self.lease.descriptor,),
        )

    def cancel(self) -> None:
        """Signal Rust without relying on cooperative Python or a native RPC returning."""
        self.cancel_path.touch(exist_ok=True)

    def finish(self, stdout: str, stderr: str) -> ScriptOutcome:
        """Reap, restore settings, and clean session-created actors on every terminal outcome."""
        if self.process is None or self.runner is None:
            message = "Session process was not started."
            raise RuntimeError(message)
        completed = subprocess.CompletedProcess(
            self.process.args, self.process.returncode, stdout, stderr
        )
        outcome = _decode_runner_output(completed, self.runner)
        return self._cleanup(outcome)

    def failed_start(self, error: str) -> ScriptOutcome:
        """Release setup-only ownership through the same cleanup and evidence contract."""
        return self._cleanup(_failure("session_setup_error", error, self.runner))

    def _cleanup(self, outcome: ScriptOutcome) -> ScriptOutcome:
        try:
            report = cleanup_script_ownership(
                host=self.config.host,
                port=self.config.port,
                ownership_path=self.work / OWNERSHIP_FILENAME,
                lease_descriptor=self.lease.descriptor,
                timeout_seconds=5.0,
                require_settings=True,
                require_connection=True,
            )
            outcome = replace(outcome, cleanup=report)
            self._save_cleanup(outcome)
        except (OSError, ValueError, RuntimeError, TypeError) as exc:
            return replace(
                outcome,
                ok=False,
                error_type="session_cleanup_failed",
                error=str(exc),
                cleanup={
                    "failures": [{"actor_id": None, "error": str(exc)}],
                    "settings_restored": False,
                },
            )
        else:
            return outcome
        finally:
            self.lease.__exit__(None, None, None)

    def _save_cleanup(self, outcome: ScriptOutcome) -> None:
        cleanup = outcome.cleanup or {}
        if cleanup.get("failures") or cleanup.get("settings_restored") is False:
            self.lease.mark_dirty({**self._recovery(), "cleanup": outcome.cleanup})
        else:
            self.lease.mark_clean()
            with contextlib.suppress(OSError):
                shutil.rmtree(self.root)

    def _recovery(self) -> dict[str, object]:
        return {
            "kind": "script_session",
            "session_id": self.session_id,
            "ownership_path": str(self.work / OWNERSHIP_FILENAME),
            "settings_path": str(self.work / SETTINGS_FILENAME),
            "connection_path": str(self.work / CONNECTION_FILENAME),
        }
