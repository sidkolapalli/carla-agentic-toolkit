"""Local no-key baseline and bounded managed-experiment lifecycle commands."""

import argparse
import json
import sys
import time
from pathlib import Path

from carla_agentic_toolkit.managed_control import ManagedController
from carla_agentic_toolkit.managed_control_io import read_control
from carla_agentic_toolkit.managed_spec import ExperimentSpec


def main() -> None:
    """Run start/status/stop/result or wait for a reproducible foreground baseline."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("start", "run"):
        command = commands.add_parser(action)
        command.add_argument("--spec", type=Path, help="validated numerical experiment JSON")
    for action in ("status", "stop", "result", "recover"):
        commands.add_parser(action).add_argument("run_id")
    options = parser.parse_args()
    try:
        result = _dispatch(options, ManagedController())
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        result = {"ok": False, "error": str(error)}
    json.dump(result, sys.stdout, allow_nan=False)
    sys.stdout.write("\n")
    raise SystemExit(1 if result.get("ok") is False else 0)


def _dispatch(options: argparse.Namespace, controller: ManagedController) -> dict[str, object]:
    if options.action in {"start", "run"}:
        values = read_control(options.spec) if options.spec else {}
        result = controller.start(ExperimentSpec.model_validate(values))
        return _wait(controller, str(result["run_id"])) if options.action == "run" else result
    handlers = {
        "status": controller.status,
        "stop": controller.stop,
        "result": controller.result,
        "recover": controller.recover,
    }
    return handlers[options.action](options.run_id)


def _wait(controller: ManagedController, run_id: str) -> dict[str, object]:
    while True:
        try:
            status = controller.status(run_id)
            if _finished(status):
                return status
            time.sleep(0.1)
        except KeyboardInterrupt:
            controller.stop(run_id)


def _finished(status: dict[str, object]) -> bool:
    if status.get("supervisor_lost") is True and status.get("state") == "failed":
        return True
    return status.get("terminated") is True and status.get("state") != "recovering"


if __name__ == "__main__":
    main()
