"""Windows host launcher for the WSL2-hosted MCP server."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class WslLaunchConfig:
    """Configuration for one WSL2-hosted server process."""

    wsl_command: str
    distribution: str
    project_dir: PurePosixPath
    uv: PurePosixPath
    output_dir: PurePosixPath
    recorder_dir: str | None
    default_host: str | None = None
    default_port: str | None = None

    @classmethod
    def from_environment(cls) -> WslLaunchConfig:
        """Load the launcher configuration from the process environment."""
        required = _required_environment(
            "CARLA_AGENTIC_TOOLKIT_WSL_DISTRO",
            "CARLA_AGENTIC_TOOLKIT_WSL_PROJECT",
            "CARLA_AGENTIC_TOOLKIT_WSL_UV",
        )
        project_dir = _absolute_linux_path(
            "CARLA_AGENTIC_TOOLKIT_WSL_PROJECT",
            required["CARLA_AGENTIC_TOOLKIT_WSL_PROJECT"],
        )
        uv = _absolute_linux_path(
            "CARLA_AGENTIC_TOOLKIT_WSL_UV", required["CARLA_AGENTIC_TOOLKIT_WSL_UV"]
        )
        output_dir = _absolute_linux_path(
            "CARLA_AGENTIC_TOOLKIT_WSL_OUTPUT_DIR",
            os.environ.get(
                "CARLA_AGENTIC_TOOLKIT_WSL_OUTPUT_DIR",
                str(project_dir / "carla-agentic-toolkit-output"),
            ),
        )
        return cls(
            wsl_command=os.environ.get("CARLA_AGENTIC_TOOLKIT_WSL_COMMAND", "wsl.exe"),
            distribution=required["CARLA_AGENTIC_TOOLKIT_WSL_DISTRO"],
            project_dir=project_dir,
            uv=uv,
            output_dir=output_dir,
            recorder_dir=os.environ.get("CARLA_AGENTIC_TOOLKIT_RECORDER_DIR"),
            default_host=os.environ.get("CARLA_AGENTIC_TOOLKIT_HOST"),
            default_port=os.environ.get("CARLA_AGENTIC_TOOLKIT_PORT"),
        )

    def command(self, program: Sequence[str]) -> list[str]:
        """Return a direct-exec WSL command for a toolkit program."""
        project_dir = str(self.project_dir)
        recorder_environment = (
            [f"CARLA_AGENTIC_TOOLKIT_RECORDER_DIR={self.recorder_dir}"] if self.recorder_dir else []
        )
        return [
            self.wsl_command,
            "--distribution",
            self.distribution,
            "--cd",
            project_dir,
            "--exec",
            "/usr/bin/env",
            f"CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR={self.output_dir}",
            *recorder_environment,
            *self._endpoint_environment(),
            str(self.uv),
            "--directory",
            project_dir,
            "run",
            *program,
        ]

    def _endpoint_environment(self) -> list[str]:
        """Forward exactly two optional defaults as data, preserving empty invalid values."""
        return [
            f"{name}={value}"
            for name, value in (
                ("CARLA_AGENTIC_TOOLKIT_HOST", self.default_host),
                ("CARLA_AGENTIC_TOOLKIT_PORT", self.default_port),
            )
            if value is not None
        ]


def _required_environment(*names: str) -> dict[str, str]:
    """Return required environment values or report every missing name."""
    missing_names = [name for name in names if not os.environ.get(name)]
    if missing_names:
        missing = ", ".join(missing_names)
        message = f"missing required configuration: {missing}"
        raise ValueError(message)
    return {name: os.environ[name] for name in names}


def _absolute_linux_path(name: str, value: str) -> PurePosixPath:
    """Return one validated absolute Linux path."""
    path = PurePosixPath(value)
    if not path.is_absolute():
        message = f"{name} must be an absolute Linux path."
        raise ValueError(message)
    return path


def run(argv: Sequence[str] | None = None) -> int:
    """Run the requested WSL2-hosted toolkit program with inherited stdio."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify WSL2 sandbox readiness")
    options = parser.parse_args(argv)
    program = (
        ("carla-agentic-toolkit-preflight", "--expect-wsl2")
        if options.check
        else ("carla-agentic-toolkit",)
    )
    try:
        config = WslLaunchConfig.from_environment()
    except ValueError as error:
        sys.stderr.write(f"carla-agentic-toolkit-windows: {error}\n")
        return 2
    try:
        completed = subprocess.run(config.command(program), check=False)
    except FileNotFoundError:
        sys.stderr.write(
            f"carla-agentic-toolkit-windows: WSL command not found: {config.wsl_command}\n"
        )
        return 1
    return completed.returncode


def main() -> None:
    """Run the Windows launcher console entrypoint."""
    raise SystemExit(run())


if __name__ == "__main__":
    main()
