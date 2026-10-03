"""Explicit local Linux/WSL2 recovery of a quarantined script endpoint."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from carla_agentic_toolkit.script_recovery import recover_script_ownership

if TYPE_CHECKING:
    from collections.abc import Sequence

MAX_PORT = 65_535
MAX_ERROR_LENGTH = 512


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= MAX_PORT:
        message = "Port must be between 1 and 65535."
        raise argparse.ArgumentTypeError(message)
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recover verified script ownership under its exclusive local simulator lease.",
        epilog="Requires Linux or WSL2 and the same private state directory as the original run.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=_port, default=2000)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Print bounded cleanup truth and return nonzero while recovery is incomplete."""
    args = _parser().parse_args(argv)
    try:
        result = recover_script_ownership(
            args.host, args.port, timeout_seconds=args.timeout_seconds
        )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        result = {
            "ok": False,
            "recovery_required": True,
            "error_type": type(error).__name__,
            "error": str(error)[:MAX_ERROR_LENGTH],
        }
    sys.stdout.write(json.dumps(result, allow_nan=False) + "\n")
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    sys.exit(main())
