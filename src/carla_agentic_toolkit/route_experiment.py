"""Route driving with owned traffic, controlled hazards and separate semantic/local evidence."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.merge_models import LocalControl
from carla_agentic_toolkit.route_actors import RouteActors
from carla_agentic_toolkit.route_fixture import (
    GOAL_DISTANCE_M,
    VEHICLE_BLUEPRINT,
    WALKER_BLUEPRINT,
    select_route,
)
from carla_agentic_toolkit.route_geometry import TRACKER_VERSION, tracking_control
from carla_agentic_toolkit.route_models import RouteActor, RouteObservation
from carla_agentic_toolkit.route_policy import (
    PLANNER_VERSION,
    SPEED_FACTORS,
    RouteSelection,
    build_request,
)
from carla_agentic_toolkit.route_risk import emergency_reason, traffic_evidence
from carla_agentic_toolkit.route_scenarios import SCENARIO_VERSION, HazardState, update_hazard

if TYPE_CHECKING:
    from carla_agentic_toolkit.managed_policy import PolicyDecision, PolicyRequest
    from carla_agentic_toolkit.managed_spec import ExperimentSpec
    from carla_agentic_toolkit.route_geometry import RoutePath
    from carla_agentic_toolkit.route_policy import SelectedAction


MAX_TRACKING_ERROR_M = 2.0
STOPPED_MPS = 0.4


class RouteExperiment:
    """The managed session alone ticks, owns the lease, journals actors and restores settings."""

    def __init__(self, session: object) -> None:
        """Bind existing ownership and keep every actor and sensor in that lifecycle."""
        self.session = cast("Any", session)
        self.spec: ExperimentSpec = self.session.spec
        self.path: RoutePath
        self.actors: RouteActors
        self.selection = RouteSelection()
        self.hazard = HazardState()
        self.values: dict[str, RouteActor] = {}
        self.history: deque[dict[str, object]] = deque(maxlen=8)
        self.last_frame = -1
        self.closed = False

    def prepare(self) -> None:
        """Validate the full route before spawning the reviewed scene."""
        self.path = select_route(self.session.map)
        self.actors = RouteActors(self.session, self.path)
        self.session.on_close(self.close)
        self.actors.spawn("policy", self.path.sample(0.0))
        self.actors.spawn("traffic", self.path.sample(32.0, 3.5))
        self._spawn_hazard()

    def _spawn_hazard(self) -> None:
        if self.spec.scenario == "pedestrian_crossing":
            self.actors.spawn("pedestrian", self.path.sample(32.0, 6.0), walker=True)
            self.actors.spawn("lead", self.path.sample(40.0))
            return
        offset = 3.5 if self.spec.scenario == "cut_in" else 0.0
        progress = 14.0 if self.spec.scenario == "cut_in" else 20.0
        self.actors.spawn("lead", self.path.sample(progress, offset))

    def fixture_metadata(self) -> dict[str, object]:
        """Keep exact route and hazard ground truth in the trace, outside provider inputs."""
        return {
            "fixture_version": self.spec.fixture,
            "scenario": self.spec.scenario,
            "scenario_version": SCENARIO_VERSION,
            "trigger_progress_m": 12.0,
            "route_goal_m": GOAL_DISTANCE_M,
            "route": [asdict(point) for point in self.path.points],
            "planner_version": PLANNER_VERSION,
            "controller_version": TRACKER_VERSION,
            "vehicle_blueprint": VEHICLE_BLUEPRINT,
            "walker_blueprint": WALKER_BLUEPRINT,
            "traffic_control": "owned scripted vehicles; no Traffic Manager",
            "actor_ids": {role: int(actor.id) for role, actor in self.actors.handles.items()},
            "observation_mode": self.spec.observation_mode,
        }

    def observe(self, snapshot: object) -> RouteObservation:
        """All dynamic traffic measurements come from this one authoritative frame."""
        frame = int(cast("Any", snapshot).frame)
        if frame <= self.last_frame:
            message = "Route observations require increasing snapshot frames."
            raise CarlaAdapterError(message)
        self.values = self.actors.observe(snapshot)
        policy = self.values["policy"]
        neighbors = self._visible_neighbors(policy)
        sensors = tuple(sensor.drain(frame) for sensor in self.actors.sensors)
        value = RouteObservation(
            self.session.run_id,
            self.session.world_generation,
            frame,
            float(cast("Any", snapshot).timestamp.elapsed_seconds),
            policy,
            GOAL_DISTANCE_M,
            traffic_evidence(policy, neighbors),
            sensors,
            tuple(self.history),
            collision=any(sensor["samples"] for sensor in sensors),
            tracking_error_m=self.path.project(policy.x, policy.y).distance_m,
            traffic_light=self.actors.traffic_light(),
        )
        self.last_frame = frame
        self._remember_observation(value)
        return value

    def _visible_neighbors(self, policy: RouteActor) -> tuple[RouteActor, ...]:
        neighbors = (actor for role, actor in self.values.items() if role != "policy")
        return tuple(
            actor
            for actor in neighbors
            if math.hypot(actor.x - policy.x, actor.y - policy.y) <= self.spec.observation_range_m
        )

    def _remember_observation(self, value: RouteObservation) -> None:
        if value.frame % self.spec.decision_interval_steps == 0:
            self.history.append(
                {
                    "frame": value.frame,
                    "speed_mps": value.policy.speed_mps,
                    "progress_m": value.policy.progress_m,
                    "traffic": [_history_actor(item.actor) for item in value.traffic],
                }
            )

    def policy_request(
        self, observation: RouteObservation, *, revision: int, deadline_monotonic: float
    ) -> PolicyRequest | None:
        """Request repeat tactical choices while the route remains active."""
        if observation.traffic_light in {"Red", "Yellow"}:
            return None
        request = build_request(
            observation,
            self.spec.target_speed_mps,
            self.spec.decision_interval_steps + 5,
            revision=revision,
            deadline_monotonic=deadline_monotonic,
        )
        self.selection.remember(request)
        return request

    def advance(
        self, observation: RouteObservation, decision: PolicyDecision | None = None
    ) -> dict[str, object]:
        """Retain the selected tactic, recheck the guard, then actuate exactly once per actor."""
        action = self.selection.choose(observation, decision, now=time.monotonic())
        self.hazard = update_hazard(
            self.hazard,
            str(self.spec.scenario),
            progress_m=observation.policy.progress_m,
            seconds=observation.simulation_seconds,
        )
        outcome = _outcome(observation)
        reason = emergency_reason(observation)
        control = self._policy_control(observation, action, reason, outcome)
        self.actors.vehicle_control("policy", control)
        background = self._traffic_control(terminal=outcome != "running")
        return {
            "frame": observation.frame,
            "phase": observation.phase,
            "requested_choice": action.choice,
            "choice_source": action.source,
            "decision_observation_frame": action.observation_frame,
            "executed_choice": "local_stop" if reason or outcome != "running" else action.choice,
            "controls": {"policy": asdict(control), **background},
            "intervention": {
                "fallback": action.source == "fallback",
                "reason": reason or action.reason,
            },
            "hazard": {"scenario": self.spec.scenario, **asdict(self.hazard)},
            "route_progress_m": observation.policy.progress_m,
            "tracking_error_m": observation.tracking_error_m,
            "terminal": outcome != "running",
            "outcome": {"completed": outcome == "route_completed", "status": outcome},
        }

    def _policy_control(
        self, value: RouteObservation, action: SelectedAction, reason: str | None, outcome: str
    ) -> LocalControl:
        speed = self.spec.target_speed_mps * SPEED_FACTORS[action.choice]
        remaining = max(0.0, GOAL_DISTANCE_M - value.policy.progress_m - 0.5)
        speed = min(speed, math.sqrt(2.0 * 2.0 * remaining))
        control = tracking_control(self.path, value.policy, target_speed_mps=speed)
        if reason or outcome != "running":
            return LocalControl(0.0, 1.0, control.steer)
        return control

    def _traffic_control(self, *, terminal: bool) -> dict[str, object]:
        controls: dict[str, object] = {}
        for role in ("lead", "traffic"):
            control = self._background_control(role)
            if terminal:
                control = LocalControl(0.0, 1.0, 0.0)
            self.actors.vehicle_control(role, control)
            controls[role] = asdict(control)
        self._pedestrian_control(terminal=terminal)
        return controls

    def _pedestrian_control(self, *, terminal: bool) -> None:
        if "pedestrian" in self.values:
            self.actors.walk(speed_mps=2.0 if self.hazard.active and not terminal else 0.0)

    def _background_control(self, role: str) -> LocalControl:
        actor = self.values[role]
        if self.actors.traffic_light(role) in {"Red", "Yellow"}:
            return LocalControl(0.0, 1.0, 0.0)
        if role == "traffic":
            speed = min(5.0, math.sqrt(4.0 * max(0.0, 45.0 - actor.progress_m)))
            return tracking_control(self.path, actor, target_speed_mps=speed, lateral_m=3.5)
        return self._lead_control(actor)

    def _lead_control(self, actor: RouteActor) -> LocalControl:
        if self.spec.scenario == "lead_brake" and self.hazard.active:
            return LocalControl(0.0, 1.0, 0.0)
        offset = self._lead_offset()
        speed = 7.0 if self.spec.scenario == "pedestrian_crossing" else 5.0
        speed = min(speed, math.sqrt(4.0 * max(0.0, self.path.length_m - 8.0 - actor.progress_m)))
        return tracking_control(self.path, actor, target_speed_mps=speed, lateral_m=offset)

    def _lead_offset(self) -> float:
        if self.spec.scenario != "cut_in":
            return 0.0
        if self.hazard.started_seconds is None:
            return 3.5
        fraction = min(self.hazard.elapsed_seconds / 2.5, 1.0)
        return 3.5 * (1.0 - fraction * fraction * (3.0 - 2.0 * fraction))

    def close(self) -> dict[str, object]:
        """Drain trailing collision events before the session destroys owned native handles."""
        if self.closed:
            return {"sensors": [], "already_closed": True}
        self.closed = True
        return {
            "sensors": [sensor.close(max(0, self.last_frame)) for sensor in self.actors.sensors],
            "frame": self.last_frame,
        }


def _outcome(value: RouteObservation) -> str:
    if value.collision:
        return "collision"
    if value.tracking_error_m > MAX_TRACKING_ERROR_M:
        return "off_route"
    if value.policy.progress_m >= GOAL_DISTANCE_M - 1.0 and value.policy.speed_mps < STOPPED_MPS:
        return "route_completed"
    return "running"


def _history_actor(actor: RouteActor) -> dict[str, object]:
    return {
        "actor_id": actor.actor_id,
        "progress_m": actor.progress_m,
        "lateral_m": actor.lateral_m,
        "speed_mps": actor.speed_mps,
        "vx": actor.vx,
        "vy": actor.vy,
    }
