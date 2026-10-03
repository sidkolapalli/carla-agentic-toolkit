"""Run the no-key managed baseline through the public Python lifecycle API."""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Protocol

from carla_agentic_toolkit.managed_control import ManagedController
from carla_agentic_toolkit.managed_spec import ExperimentSpec

POLL_SECONDS = 0.1
CLEANUP_ALLOWANCE_SECONDS = 45.0


class Lifecycle(Protocol):
    """The same public operations used by the local CLI and MCP lifecycle tool."""

    def start(self, spec: ExperimentSpec) -> dict[str, object]:
        """Start a validated run and return its opaque local run ID."""
        ...

    def status(self, run_id: str) -> dict[str, object]:
        """Read bounded lifecycle evidence without waiting for simulator work."""
        ...

    def stop(self, run_id: str) -> dict[str, object]:
        """Request cancellation; the supervisor remains responsible for cleanup."""
        ...


def run_baseline(spec: ExperimentSpec, *, controller: Lifecycle | None = None) -> dict[str, object]:
    """Return machine-readable results, preserving cancellation and cleanup truth."""
    if spec.policy != "rules":
        message = "This no-key example requires policy='rules'."
        raise ValueError(message)
    owner = controller or ManagedController()
    initial = owner.start(spec)
    run_id = str(initial["run_id"])
    try:
        return _wait(owner, run_id, initial, spec.max_wall_seconds)
    except BaseException:
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            owner.stop(run_id)
        raise


def _wait(
    owner: Lifecycle, run_id: str, initial: dict[str, object], wall_seconds: float
) -> dict[str, object]:
    deadline = time.monotonic() + wall_seconds + CLEANUP_ALLOWANCE_SECONDS
    result = initial
    while time.monotonic() < deadline:
        result = _poll(owner, run_id)
        if _finished(result):
            return result
    owner.stop(run_id)
    return {**result, "ok": False, "example_wait_expired": True}


def _poll(owner: Lifecycle, run_id: str) -> dict[str, object]:
    try:
        time.sleep(POLL_SECONDS)
        return owner.status(run_id)
    except KeyboardInterrupt:
        return owner.stop(run_id)


def _finished(result: dict[str, object]) -> bool:
    if result.get("supervisor_lost") is True and result.get("state") == "failed":
        return True
    return result.get("terminated") is True and result.get("state") != "recovering"


def main() -> None:
    """Accept numerical rules configuration and print one JSON result."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, help="rules-only ExperimentSpec JSON")
    options = parser.parse_args()
    values = json.loads(options.spec.read_text()) if options.spec else {}
    result = run_baseline(ExperimentSpec.model_validate(values))
    json.dump(result, sys.stdout, allow_nan=False)
    sys.stdout.write("\n")
    raise SystemExit(0 if result.get("ok") is True else 1)


if __name__ == "__main__":
    main()
