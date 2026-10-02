"""Trusted experiment worker; only its supervisor may confirm process termination."""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from carla_agentic_toolkit.managed_control import ManagedController
from carla_agentic_toolkit.managed_control_io import (
    MAX_CONTROL_BYTES,
    create_stop_marker,
    read_control,
    write_control,
)
from carla_agentic_toolkit.managed_liveness import record_worker
from carla_agentic_toolkit.managed_process import parent_death_guard
from carla_agentic_toolkit.managed_spec import ExperimentSpec

if TYPE_CHECKING:
    from collections.abc import Callable


class ExperimentEngine(Protocol):
    """Trusted synchronous engine entrypoint with cooperative cancellation."""

    def __call__(
        self,
        spec: ExperimentSpec,
        run_id: str,
        /,
        *,
        state_root: Path,
        cancelled: Callable[[], bool],
        publish_status: Callable[[dict[str, object]], None],
    ) -> dict[str, object]:
        """Run reviewed numerical work with finally-based session cleanup."""
        ...


def execute_worker(job: Path, *, engine: ExperimentEngine | None = None) -> None:
    """Write bounded intermediate evidence; sanitize any unexpected engine failure."""

    def publish(status: dict[str, object]) -> None:
        value = {**status, "run_id": job.name, "terminated": False}
        if len(json.dumps(value, allow_nan=False).encode()) > MAX_CONTROL_BYTES // 2:
            message = "Worker status exceeded the bounded telemetry allowance."
            raise ValueError(message)
        write_control(job / "worker.json", value)

    try:
        spec = ExperimentSpec.model_validate(read_control(job / "spec.json"))
        selected_engine = engine or _engine()
        result = selected_engine(
            spec,
            job.name,
            state_root=job.parent.parent,
            cancelled=(job / "stop").exists,
            publish_status=publish,
        )
        publish(result)
    except Exception as error:  # noqa: BLE001
        publish(
            {
                "state": "failed",
                "cleanup": None,
                "error_type": type(error).__name__,
                "error": "trusted_worker_failed",
            }
        )


def _engine() -> ExperimentEngine:
    from carla_agentic_toolkit.managed_engine import run_experiment  # noqa: PLC0415

    return run_experiment


def main() -> None:
    """Run only an existing private job created by the local control plane."""
    path = Path(sys.argv[1])
    job = ManagedController(path.parent.parent).job_path(path.name)
    parent_death_guard()
    record_worker(job, os.getpid())
    signal.signal(signal.SIGTERM, lambda *_args: create_stop_marker(job))
    execute_worker(job)


if __name__ == "__main__":
    main()
