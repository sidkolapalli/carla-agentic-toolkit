"""Post-termination cleanup recovery under the same private simulator lease."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.managed_control import ManagedController
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.managed_process import parent_death_guard, spawn_trusted, terminate_tree
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient

RECOVERY_TIMEOUT_SECONDS = 30.0


def recover_job(job: Path, spec: ExperimentSpec) -> dict[str, object]:
    """Recover only this run's dirty state after its worker tree is confirmed dead."""
    _ = spec
    try:
        (job / "recovery.json").unlink(missing_ok=True)
        process = spawn_recovery(job)
    except OSError:
        return {"ok": False, "error": "recovery_launch_failed", "recovery_required": True}
    return _await_recovery(job, process)


def _await_recovery(job: Path, process: subprocess.Popen[bytes]) -> dict[str, object]:
    try:
        if process.wait(timeout=RECOVERY_TIMEOUT_SECONDS) != 0:
            return {"ok": False, "error": "recovery_process_failed", "recovery_required": True}
        return read_control(job / "recovery.json")
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "recovery_timeout", "recovery_required": True}
    except (OSError, ValueError, TypeError):
        return {"ok": False, "error": "recovery_failed", "recovery_required": True}
    finally:
        terminate_tree(process)


def spawn_recovery(job: Path) -> subprocess.Popen[bytes]:
    """Isolate native cleanup calls so a stalled recovery still has a hard timeout."""
    return spawn_trusted("carla_agentic_toolkit.managed_recovery", job)


def _recover(job: Path, spec: ExperimentSpec) -> dict[str, object]:
    try:
        with SimulatorLease(
            spec.host, spec.port, state_root=job.parent.parent, recovering=True
        ) as lease:
            return _recover_owned(job, spec, lease)
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error_type": type(error).__name__, "recovery_required": True}


def _recover_owned(job: Path, spec: ExperimentSpec, lease: SimulatorLease) -> dict[str, object]:
    if not lease.recovery_state:
        return {"ok": True, "recovery_required": False}
    if lease.recovery_state.get("run_id") != job.name:
        return {"ok": False, "error": "different_run_owns_dirty_state", "recovery_required": True}
    return _recover_session(job, spec, lease)


def _recover_session(job: Path, spec: ExperimentSpec, lease: SimulatorLease) -> dict[str, object]:
    import carla  # noqa: PLC0415

    from carla_agentic_toolkit.managed_session import recover_managed_session  # noqa: PLC0415

    world_id = str(lease.recovery_state.get("world_generation", "unknown"))
    client = carla.Client(spec.host, spec.port)
    client.set_timeout(spec.rpc_timeout_seconds)
    result = recover_managed_session(cast("CarlaClient", client), lease, spec)
    _record_recovery(job, spec, world_id, result)
    return result


def _record_recovery(
    job: Path,
    spec: ExperimentSpec,
    world_id: str,
    result: dict[str, object],
) -> None:
    from carla_agentic_toolkit.experiment_trace import TraceStore  # noqa: PLC0415

    path = job.parent.parent / "runs" / job.name / "events.jsonl"
    if not path.exists():
        return
    with TraceStore.resume(
        job.name, root=job.parent.parent, max_bytes=spec.max_trace_bytes
    ) as trace:
        trace.append(
            "recovery",
            world_generation=world_id,
            frame=None,
            data={"worker_terminated": True, "cleanup": result},
        )


def main() -> None:
    """Recover under lease only as a supervised, parent-bound trusted process."""
    path = Path(sys.argv[1])
    job = ManagedController(path.parent.parent).job_path(path.name)
    parent_death_guard()
    spec = ExperimentSpec.model_validate(read_control(job / "spec.json"))
    write_control(job / "recovery.json", _recover(job, spec))


if __name__ == "__main__":
    main()
