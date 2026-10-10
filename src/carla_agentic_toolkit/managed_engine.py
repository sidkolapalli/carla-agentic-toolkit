"""One trusted owner schedules frames, policy work, actuation, and authoritative evidence."""

from __future__ import annotations

import asyncio
import time
from importlib import import_module
from typing import TYPE_CHECKING, Any, Protocol, cast

from carla_agentic_toolkit.carla_versions import read_version_info, require_matching_release
from carla_agentic_toolkit.experiment_trace import (
    RecordedPolicy,
    TraceStore,
    load_trace,
    write_trace_report,
)
from carla_agentic_toolkit.managed_evidence import sensor_records, trailing_drains
from carla_agentic_toolkit.managed_jev import create_jev_policy
from carla_agentic_toolkit.managed_jev_config import JevConfig
from carla_agentic_toolkit.managed_metadata import run_metadata
from carla_agentic_toolkit.managed_observations import prepare_background, record_background
from carla_agentic_toolkit.managed_selection import DecisionScheduler
from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.merge_planner import rules_decision
from carla_agentic_toolkit.route_policy import rules_decision as route_rules_decision
from carla_agentic_toolkit.simulator_lease import SimulatorLease

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaSnapshot
    from carla_agentic_toolkit.managed_policy import PolicyDecision, PolicyRequest
    from carla_agentic_toolkit.managed_spec import ExperimentSpec


class Policy(Protocol):
    """Trusted selectors share code-generated candidate identities."""

    async def choose(self, request: PolicyRequest, fallback_id: str) -> PolicyDecision:
        """Select without numerical actuation or world access."""

    async def aclose(self) -> None:
        """Cancel outstanding inference and close retained resources."""


class Experiment[ObservationT](Protocol):
    """A fixture can observe/actuate but never owns frame advancement."""

    def prepare(self) -> None:
        """Spawn and subscribe without ticking."""

    def observe(self, snapshot: CarlaSnapshot) -> ObservationT:
        """Read actor state from exactly this snapshot."""

    def policy_request(
        self,
        observation: ObservationT,
        *,
        revision: int,
        deadline_monotonic: float,
    ) -> PolicyRequest | None:
        """Generate current numerical choices or explicitly decline a request."""

    def advance(
        self,
        observation: ObservationT,
        decision: PolicyDecision | None = None,
    ) -> dict[str, object]:
        """Apply local controls and return evidence without advancing the world."""


class RulesPolicy:
    """No-key selector using the exact same candidate contract as Jev."""

    async def choose(self, request: PolicyRequest, fallback_id: str) -> PolicyDecision:
        """Choose the deterministic baseline action."""
        del fallback_id
        if request.context.phase == "route_following":
            return route_rules_decision(request)
        return rules_decision(request)

    async def aclose(self) -> None:
        """Complete the resource-free rules lifecycle."""


def connect_client(spec: ExperimentSpec) -> CarlaClient:
    """Create one retained trusted client with bounded simulator RPCs."""
    client = cast("CarlaClient", import_module("carla").Client(spec.host, spec.port))
    client.set_timeout(spec.rpc_timeout_seconds)
    return client


def build_experiment(session: ManagedSession) -> Experiment[Any]:
    """Load only the reviewed fixed experiment implementation."""
    if session.spec.fixture == "town10-route-ue5-v1":
        return import_module("carla_agentic_toolkit.route_experiment").RouteExperiment(session)
    return import_module("carla_agentic_toolkit.merge_experiment").MergeExperiment(session)


def create_policy(spec: ExperimentSpec, root: Path) -> Policy:
    """Keep provider credentials and configuration in the trusted worker."""
    if spec.policy == "rules":
        return RulesPolicy()
    if spec.policy == "jev":
        config = JevConfig(
            timeout_seconds=spec.decision_timeout_seconds, max_requests=spec.max_requests
        )
        return create_jev_policy(config, state_root=root)
    return RecordedPolicy(load_trace(root / "runs" / str(spec.replay_run_id) / "events.jsonl"))


class ExperimentRun:
    """Cohesive single-run lifecycle; the outer process supervisor confirms death."""

    def __init__(
        self,
        spec: ExperimentSpec,
        run_id: str,
        *,
        state_root: Path,
        cancelled: Callable[[], bool],
        publish_status: Callable[[dict[str, object]], None],
    ) -> None:
        """Bind bounded trusted inputs; retain no generated code."""
        spec.require_live_policy()
        self.spec, self.run_id, self.root = spec, run_id, state_root
        self.cancelled, self.publish_status = cancelled, publish_status
        self.trace = TraceStore(run_id, root=state_root, max_bytes=spec.max_trace_bytes)
        self.started = time.monotonic()
        self.pace_origin = self.started
        self.session: ManagedSession | None = None
        self.experiment: Experiment[Any] | None = None
        self.policy: Policy | None = None
        self.selection: DecisionScheduler | None = None
        self.frame: int | None = None
        self.actor_id: int | None = None
        self.state = "starting"
        self.error: str | None = None
        self.outcome: dict[str, object] = {"completed": False, "status": "partial"}
        self.cleanup: dict[str, object] = {"ok": True, "not_started": True}
        self.fixture_summary: dict[str, object] = {}

    @property
    def world_generation(self) -> str:
        """Give unconnected failures a valid explicit trace identity."""
        return self.session.world_generation if self.session else "unconnected"

    def event(self, kind: str, data: dict[str, object]) -> None:
        """Use one authoritative envelope for all adapters and reports."""
        self.trace.append(
            kind,
            world_generation=self.world_generation,
            frame=self.frame,
            actor_id=self.actor_id,
            data=data,
        )

    def status(self, state: str) -> None:
        """Publish without waiting on a simulator or provider."""
        self.state = state
        self.publish_status(
            {
                "run_id": self.run_id,
                "state": state,
                "frame": self.frame,
                "terminated": False,
                "cancellation_requested": self.cancelled(),
            }
        )

    async def run(self) -> dict[str, object]:
        """Keep the lease held until policy, sensors, actors and settings are finished."""
        self.status("starting")
        try:
            await self._owned_run()
        except Exception as exc:  # noqa: BLE001
            self.error = f"{type(exc).__name__}: {exc}"
            self.state = "failed"
        return self._finish_trace()

    async def _owned_run(self) -> None:
        with SimulatorLease(self.spec.host, self.spec.port, state_root=self.root) as lease:
            try:
                if self.cancelled():
                    self.state = "cancelled"
                    return
                self._prepare(lease)
                await self._loop()
            finally:
                await self._cleanup()

    def _prepare(self, lease: SimulatorLease) -> None:
        client = connect_client(self.spec)
        versions = read_version_info(client)
        self.event("metadata", run_metadata(self.spec, versions))
        require_matching_release(versions)
        self.policy = create_policy(self.spec, self.root)
        self.session = ManagedSession(self.spec, client, lease, self.run_id)
        self.session.open()
        self.event("setup_frames", self.session.setup_frame_barrier)
        self.experiment = build_experiment(self.session)
        self.experiment.prepare()
        prepare_background(self)
        self.selection = DecisionScheduler(
            self.spec, self.experiment, self.policy, self.event, self._should_stop
        )
        metadata = getattr(self.experiment, "fixture_metadata", None)
        if callable(metadata):
            self.event("fixture", metadata())
        self.status("running")
        self.event("lifecycle", {"state": "running"})

    async def _loop(self) -> None:
        session = cast("ManagedSession", self.session)
        self.pace_origin = time.monotonic()
        for step in range(self.spec.max_steps):
            if self._should_stop():
                return
            snapshot = session.step()
            if await self._iteration(snapshot, step):
                self.state = "completed"
                return
            await self._pace(step)
        if self._should_stop():
            return
        self.state = "completed"
        self.outcome = {"completed": False, "status": "frame_limit"}
        self.event("outcome", self.outcome)

    def _should_stop(self) -> bool:
        if self.cancelled():
            self.state = "cancelled"
            return True
        if time.monotonic() - self.started >= self.spec.max_wall_seconds:
            self.state, self.error = "failed", "Experiment exceeded its wall-clock deadline."
            return True
        return False

    async def _iteration(self, snapshot: CarlaSnapshot, step: int) -> bool:
        record_background(self)
        experiment = cast("Experiment[Any]", self.experiment)
        observation = experiment.observe(snapshot)
        self.frame, self.actor_id = observation.frame, observation.policy.actor_id
        data = observation.to_dict()
        self.event("observation", data)
        for sensor in observation.sensors:
            self._sensor_events(sensor)
        decision = await cast("DecisionScheduler", self.selection).choose(observation, step)
        if self._should_stop():
            return False
        cast("ManagedSession", self.session).assert_current()
        applied = experiment.advance(observation, decision)
        self._execution_events(applied)
        self.status("running")
        return applied.get("terminal") is True

    def _sensor_events(self, drain: dict[str, object]) -> None:
        for record in sensor_records(drain):
            self.trace.append(
                "sensor",
                world_generation=self.world_generation,
                frame=self.frame,
                actor_id=cast("int", drain["actor_id"]),
                data=record,
            )

    def _execution_events(self, applied: dict[str, object]) -> None:
        self.event("execution", applied)
        intervention = applied.get("intervention")
        if isinstance(intervention, dict) and _intervened(cast("dict[str, object]", intervention)):
            self.event("intervention", cast("dict[str, object]", intervention))
        outcome = applied.get("outcome")
        if isinstance(outcome, dict) and applied.get("terminal") is True:
            self.outcome = cast("dict[str, object]", outcome)
            self.event("outcome", self.outcome)

    async def _pace(self, step: int) -> None:
        delay = 0.0
        if self.spec.timing_mode == "paced":
            due = self.pace_origin + (step + 1) * self.spec.fixed_delta_seconds
            delay = max(0.0, due - time.monotonic())
        await asyncio.sleep(delay)

    async def _cleanup(self) -> None:
        terminal = self.state
        try:
            try:
                self.status("stopping")
            finally:
                await self._close_policy()
        finally:
            try:
                self._close_session()
            finally:
                self.state = terminal
                if self.cleanup.get("ok") is not True:
                    self.state = "failed"
                    self.error = "Experiment cleanup did not complete."

    def _close_session(self) -> None:
        if self.session is None:
            return
        self.cleanup = {"ok": False, "error": "Session cleanup did not return a verified result."}
        self.cleanup = self.session.close()
        for drain in trailing_drains(self.cleanup):
            self._sensor_events(drain)

    async def _close_policy(self) -> None:
        if self.selection is not None:
            await self.selection.close()
        elif self.policy is not None:
            await self.policy.aclose()

    def _finish_trace(self) -> dict[str, object]:
        try:
            self._final_events()
        except (OSError, ValueError, RuntimeError) as error:
            self.state = "failed"
            self.error = f"Trace finalization failed: {type(error).__name__}"
        finally:
            self.trace.close()
        reports = self._report_paths()
        result = {
            **self.fixture_summary,
            "ok": self._successful(),
            "run_id": self.run_id,
            "state": self.state,
            "error": self.error,
            "cleanup": self.cleanup,
            "outcome": self.outcome,
            "terminated": False,
            "trace_path": str(self.trace.path),
            "reports": reports,
        }
        self.publish_status(result)
        return result

    def _successful(self) -> bool:
        return (
            self.state == "completed"
            and self.outcome.get("completed") is True
            and _valid_trial(self.fixture_summary)
        )

    def _final_events(self) -> None:
        if self.error is not None:
            self.event("infrastructure_error", {"error": self.error})
        if self.state == "cancelled":
            self.outcome = {"completed": False, "status": "cancelled"}
            self.event("outcome", self.outcome)
        self._finalize_fixture()
        self.event("cleanup", self.cleanup)
        self.event("lifecycle", {"state": self.state, "error": self.error})

    def _finalize_fixture(self) -> None:
        summarize = getattr(self.experiment, "final_summary", None)
        if callable(summarize):
            termination = (
                self.state if self.state in {"cancelled", "failed"} else str(self.outcome["status"])
            )
            self.fixture_summary = summarize(termination=termination)
            self.event("fixture_summary", self.fixture_summary)

    def _report_paths(self) -> dict[str, str]:
        try:
            reports = write_trace_report(self.trace.path)
        except (OSError, ValueError, RuntimeError) as error:
            self.state = "failed"
            self.error = f"Trace report failed: {type(error).__name__}"
            return {}
        return {key: str(path) for key, path in reports.items()}


def _intervened(intervention: dict[str, object]) -> bool:
    return intervention.get("fallback") is True or bool(intervention.get("reason"))


def _valid_trial(summary: dict[str, object]) -> bool:
    trial = summary.get("hazard_trial")
    return not isinstance(trial, dict) or trial.get("valid") is not False


async def run_experiment_async(
    spec: ExperimentSpec,
    run_id: str,
    *,
    state_root: Path,
    cancelled: Callable[[], bool],
    publish_status: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    """Run one trusted experiment; cancellation is independent of provider completion."""
    run = ExperimentRun(
        spec, run_id, state_root=state_root, cancelled=cancelled, publish_status=publish_status
    )
    return await run.run()


def run_experiment(
    spec: ExperimentSpec,
    run_id: str,
    *,
    state_root: Path,
    cancelled: Callable[[], bool],
    publish_status: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    """Worker entrypoint used identically by CLI and opt-in MCP lifecycle controls."""
    return asyncio.run(
        run_experiment_async(
            spec,
            run_id,
            state_root=state_root,
            cancelled=cancelled,
            publish_status=publish_status,
        )
    )
