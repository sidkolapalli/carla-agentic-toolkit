"""CARLA backend for the managed, no-background-traffic merge experiment."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import asdict, replace
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.actor_boxes import bounding_box_metadata, project_actor_box
from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.merge_fixture import (
    FIXTURE_VEHICLES,
    MergeCorridor,
    Pose,
    select_corridor,
    yaw_difference,
)
from carla_agentic_toolkit.merge_models import CorridorBox
from carla_agentic_toolkit.merge_planner import (
    PLANNER_VERSION,
    TERMINAL_PHASES,
    TRACKER_VERSION,
    ActorObservation,
    LaneGeometry,
    LocalControl,
    ManeuverState,
    MergeObservation,
    PlannerSettings,
    build_policy_request,
    fallback_choice,
    lateral_target,
    tracking_control,
    transition,
)
from carla_agentic_toolkit.merge_sensor_evidence import MergeSensor
from carla_agentic_toolkit.sensor_subscription import SensorSubscription

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaSensor
    from carla_agentic_toolkit.managed_policy import DecisionContext, PolicyDecision, PolicyRequest
    from carla_agentic_toolkit.managed_spec import ExperimentSpec

MAX_RETAINED_POLICY_REQUESTS = 32


class MergeExperiment:
    """Spawn protected actors, observe snapshots, and apply two exclusive trackers.

    This backend never ticks, reloads maps, changes world settings, or enables
    Traffic Manager. The managed session alone owns timing and cleanup.
    """

    def __init__(self, session: object) -> None:
        """Bind the established session contract without acquiring a second owner."""
        self.session = cast("Any", session)
        self.spec: ExperimentSpec = self.session.spec
        self.settings = PlannerSettings(
            fixed_delta_seconds=self.spec.fixed_delta_seconds,
            target_speed_mps=self.spec.target_speed_mps,
            ego_speed_mps=self.spec.ego_speed_mps,
            expiry_frames=max(1, round(1.0 / self.spec.fixed_delta_seconds)),
            tracker_heading_gain=1.8 if self.spec.fixture == "town10-merge-ue5-v1" else 0.9,
        )
        self.state = ManeuverState()
        self.corridor: MergeCorridor | None = None
        self._actors: dict[str, object] = {}
        self._sensors: list[MergeSensor] = []
        self._history: deque[dict[str, object]] = deque(maxlen=20)
        self._requests: dict[DecisionContext, PolicyRequest] = {}
        self._last_frame = -1
        self._closed = False

    def prepare(self) -> None:
        """Verify the fixture before spawning, then journal actors before control."""
        if self._actors:
            message = "Merge fixture is already prepared."
            raise CarlaAdapterError(message)
        self.corridor = select_corridor(self.session.map, fixture_version=self.spec.fixture)
        self.session.on_close(self.close)
        self._actors["policy"] = self._spawn_vehicle("policy", self.corridor.policy_start)
        self._actors["ego"] = self._spawn_vehicle("ego", self.corridor.ego_start)
        for role, actor in self._actors.items():
            self._attach_sensors(role, actor)

    def fixture_metadata(self) -> dict[str, object]:
        """Expose exact poses, replicate labels, units, and reviewed controller versions."""
        return {
            **self._require_corridor().to_dict(),
            "replicate_index": self.spec.replicate_index,
            "planner_version": PLANNER_VERSION,
            "controller_version": TRACKER_VERSION,
            "settings": asdict(self.settings),
            "observation_mode": self.spec.observation_mode,
            "vehicle_blueprint": FIXTURE_VEHICLES[self.spec.fixture],
            "actor_ids": {role: int(cast("Any", actor).id) for role, actor in self._actors.items()},
            "actor_bounding_boxes": {
                role: bounding_box_metadata(cast("Any", actor).bounding_box)
                for role, actor in self._actors.items()
            },
        }

    def _spawn_vehicle(self, role: str, pose: Pose) -> object:
        blueprint_id = FIXTURE_VEHICLES[self.spec.fixture]
        try:
            blueprint = self.session.world.get_blueprint_library().find(blueprint_id)
        except (RuntimeError, IndexError) as error:
            message = (
                f"{self.spec.fixture} requires {blueprint_id}, which this simulator does not "
                "provide. Select a fixture matching the simulator's vehicle catalog."
            )
            raise UnsupportedFeatureError(message) from error
        blueprint.set_attribute("role_name", f"managed:{self.session.run_id}:{role}")
        actor = self.session.world.spawn_actor(blueprint, _transform(pose))
        self.session.own(actor, controller=f"{role}:{TRACKER_VERSION}", protected=True)
        return actor

    def _attach_sensors(self, role: str, actor: object) -> None:
        for kind in ("collision", "lane_invasion", "gnss"):
            self._attach_sensor(role, actor, kind)

    def _attach_sensor(self, role: str, actor: object, kind: str) -> None:
        blueprint = self.session.world.get_blueprint_library().find(f"sensor.other.{kind}")
        blueprint.set_attribute("role_name", f"managed:{self.session.run_id}:{role}-{kind}")
        if blueprint.has_attribute("sensor_tick"):
            blueprint.set_attribute("sensor_tick", str(self.spec.fixed_delta_seconds))
        sensor = self.session.world.spawn_actor(
            blueprint, import_module("carla").Transform(), attach_to=actor
        )
        self.session.own(sensor, controller="sensor-listener", protected=True)
        subscription = SensorSubscription(cast("CarlaSensor", sensor), event_sensor=kind != "gnss")
        self._sensors.append(
            MergeSensor(int(sensor.id), int(cast("Any", actor).id), role, kind, subscription)
        )

    def observe(self, snapshot: object) -> MergeObservation:
        """Read every controlled actor from exactly the supplied WorldSnapshot."""
        frame = int(cast("Any", snapshot).frame)
        if frame <= self._last_frame:
            message = "Merge observations require a new, increasing snapshot frame."
            raise CarlaAdapterError(message)
        corridor = self._require_corridor()
        policy = self._actor_observation("policy", snapshot)
        ego = self._actor_observation("ego", snapshot)
        sensors = tuple(sensor.drain(frame) for sensor in self._sensors)
        value = MergeObservation(
            run_id=str(self.session.run_id),
            world_generation=str(self.session.world_generation),
            frame=frame,
            simulation_seconds=float(cast("Any", snapshot).timestamp.elapsed_seconds),
            policy=policy,
            ego=ego,
            lane=_lane(corridor, policy),
            neighbors=_visible_neighbors(policy, ego, self.spec.observation_range_m),
            sensors=sensors,
            phase=self.state.phase,
            history=tuple(self._history),
            collision=_has_collision(sensors),
        )
        value = replace(
            value,
            tracking_error_m=abs(
                policy.lateral_m - lateral_target(self.state, value, self.settings)
            ),
        )
        self._last_frame = frame
        self._history.append(
            {
                "frame": frame,
                "lateral_m": policy.lateral_m,
                "speed_mps": policy.speed_mps,
                "phase": self.state.phase,
            }
        )
        return value

    def _actor_observation(self, role: str, snapshot: object) -> ActorObservation:
        actor = cast("Any", self._actors[role])
        frozen = cast("Any", snapshot).find(actor.id)
        if frozen is None:
            message = f"Actor {actor.id} is missing from the owner snapshot."
            raise CarlaAdapterError(message)
        return _actor_value(
            actor, frozen, int(cast("Any", snapshot).frame), self._require_corridor()
        )

    def policy_request(
        self, observation: MergeObservation, *, revision: int, deadline_monotonic: float
    ) -> PolicyRequest | None:
        """Freeze valid candidates for rules, replay, or an optional asynchronous provider."""
        request = build_policy_request(
            observation,
            self.state,
            self.settings,
            revision=revision,
            deadline_monotonic=deadline_monotonic,
        )
        if request is not None:
            self._requests[request.context] = request
            self._bound_requests()
        return request

    def _bound_requests(self) -> None:
        while len(self._requests) > MAX_RETAINED_POLICY_REQUESTS:
            del self._requests[next(iter(self._requests))]

    def advance(
        self, observation: MergeObservation, decision: PolicyDecision | None = None
    ) -> dict[str, object]:
        """Apply one validated choice and one local actuator update per actor."""
        choice, reason = self._validated_choice(observation, decision)
        self.state = transition(self.state, observation, choice, self.settings)
        controls = self._controls(observation)
        for role, control in controls.items():
            cast("Any", self._actors[role]).apply_control(
                import_module("carla").VehicleControl(**asdict(control))
            )
        return self._step_report(observation, decision, choice, reason, controls)

    def _validated_choice(
        self, value: MergeObservation, decision: PolicyDecision | None
    ) -> tuple[str, str | None]:
        fallback = fallback_choice(self.state.phase)
        if decision is None:
            return fallback, None
        request = self._requests.get(decision.context)
        if request is None or request.context != decision.context:
            return fallback, "unknown_request_identity"
        return self._current_choice(value, decision, request)

    def _current_choice(
        self, value: MergeObservation, decision: PolicyDecision, request: PolicyRequest
    ) -> tuple[str, str | None]:
        fallback = fallback_choice(self.state.phase)
        current = build_policy_request(
            value,
            self.state,
            self.settings,
            revision=decision.context.revision,
            deadline_monotonic=decision.context.deadline_monotonic,
        )
        if current is None or current.context != _current_context(decision, value):
            return fallback, "stale_or_inapplicable_decision"
        if not _choice_fresh(decision, request, value.frame):
            return fallback, "expired_or_unknown_choice"
        reason = decision.reason if decision.source == "fallback" else None
        return decision.choice_id, reason

    def _controls(self, value: MergeObservation) -> dict[str, LocalControl]:
        if self.state.phase in TERMINAL_PHASES:
            return dict.fromkeys(("policy", "ego"), LocalControl(0.0, 1.0, 0.0))
        return {
            "policy": tracking_control(
                value.policy,
                target_speed_mps=self.settings.target_speed_mps,
                target_lateral_m=lateral_target(self.state, value, self.settings),
                heading_gain=self.settings.tracker_heading_gain,
            ),
            "ego": tracking_control(
                value.ego,
                target_speed_mps=self.settings.ego_speed_mps,
                target_lateral_m=value.lane.target_offset_m,
                heading_gain=self.settings.tracker_heading_gain,
            ),
        }

    def _step_report(
        self,
        value: MergeObservation,
        decision: PolicyDecision | None,
        choice: str,
        reason: str | None,
        controls: dict[str, LocalControl],
    ) -> dict[str, object]:
        return {
            "frame": value.frame,
            "phase": self.state.phase,
            "maneuver_generation": self.state.maneuver_generation,
            "requested_choice": decision.choice_id if decision else None,
            "executed_choice": choice,
            "controls": {key: asdict(item) for key, item in controls.items()},
            "intervention": {"fallback": reason is not None, "reason": reason},
            "tracking_error_m": abs(
                value.policy.lateral_m - lateral_target(self.state, value, self.settings)
            ),
            "terminal": self.state.phase in TERMINAL_PHASES,
            "outcome": {
                "completed": self.state.phase == "completed",
                "status": self.state.outcome or "running",
            },
        }

    def close(self) -> dict[str, object]:
        """Retain trailing sensor evidence before closing every bounded listener."""
        if self._closed:
            return {"sensors": [], "already_closed": True}
        self._closed = True
        trailing = [sensor.close(max(self._last_frame, 0)) for sensor in self._sensors]
        return {"sensors": trailing, "frame": self._last_frame}

    def _require_corridor(self) -> MergeCorridor:
        if self.corridor is None:
            message = "Merge fixture must be prepared before observation or actuation."
            raise CarlaAdapterError(message)
        return self.corridor


def _transform(pose: Pose) -> object:
    carla = cast("Any", import_module("carla"))
    location = carla.Location(x=pose.x, y=pose.y, z=pose.z + 0.4)
    rotation = carla.Rotation(yaw=pose.yaw)
    return carla.Transform(location, rotation)


def _actor_value(
    actor: object, frozen: object, frame: int, corridor: MergeCorridor
) -> ActorObservation:
    runtime = cast("Any", frozen)
    transform, velocity = runtime.get_transform(), runtime.get_velocity()
    along, lateral = corridor.project(float(transform.location.x), float(transform.location.y))
    extent = cast("Any", actor).bounding_box.extent
    target_distance = abs(lateral - corridor.target_offset_m)
    lane_id = (
        corridor.target_lane_id
        if target_distance < corridor.width_m / 2
        else corridor.source_lane_id
    )
    return ActorObservation(
        actor_id=int(cast("Any", actor).id),
        frame=frame,
        longitudinal_m=along,
        lateral_m=lateral,
        speed_mps=math.sqrt(
            float(velocity.x) ** 2 + float(velocity.y) ** 2 + float(velocity.z) ** 2
        ),
        yaw_error_degrees=yaw_difference(float(transform.rotation.yaw), corridor.policy_start.yaw),
        length_m=float(extent.x) * 2,
        width_m=float(extent.y) * 2,
        lane_id=lane_id,
        position_m=(
            float(transform.location.x),
            float(transform.location.y),
            float(transform.location.z),
        ),
        velocity_mps=(float(velocity.x), float(velocity.y), float(velocity.z)),
        longitudinal_speed_mps=(
            float(velocity.x) * math.cos(math.radians(corridor.policy_start.yaw))
            + float(velocity.y) * math.sin(math.radians(corridor.policy_start.yaw))
        ),
        box=_corridor_box(transform, cast("Any", actor).bounding_box, corridor),
    )


def _corridor_box(transform: object, bounds: object, corridor: MergeCorridor) -> CorridorBox:
    box = project_actor_box(transform, bounds)
    yaw = math.radians(corridor.policy_start.yaw)
    return CorridorBox(
        box,
        corridor.project(box.center_m[0], box.center_m[1])[0],
        box.radius((math.cos(yaw), math.sin(yaw))),
    )


def _lane(corridor: MergeCorridor, policy: ActorObservation) -> LaneGeometry:
    return LaneGeometry(
        road_id=corridor.road_id,
        source_lane_id=corridor.source_lane_id,
        target_lane_id=corridor.target_lane_id,
        width_m=corridor.width_m,
        target_offset_m=corridor.target_offset_m,
        remaining_m=corridor.length_m - policy.longitudinal_m,
        adjacent=True,
        same_direction=True,
        lane_change_allowed=True,
        source_marking=corridor.source_marking,
        target_marking=corridor.target_marking,
    )


def _visible_neighbors(
    policy: ActorObservation, ego: ActorObservation, radius: float
) -> tuple[ActorObservation, ...]:
    distance = math.hypot(
        ego.longitudinal_m - policy.longitudinal_m, ego.lateral_m - policy.lateral_m
    )
    return (ego,) if distance <= radius else ()


def _has_collision(sensors: tuple[dict[str, object], ...]) -> bool:
    return any(sensor["kind"] == "collision" and sensor["samples"] for sensor in sensors)


def _current_context(decision: PolicyDecision, value: MergeObservation) -> object:
    return replace(decision.context, observation_frame=value.frame)


def _choice_fresh(decision: PolicyDecision, request: PolicyRequest, frame: int) -> bool:
    candidates = {item.candidate_id: item for item in request.candidates}
    candidate = candidates.get(decision.choice_id)
    if candidate is None:
        return False
    return (
        candidate.expires_frame >= frame and time.monotonic() <= decision.context.deadline_monotonic
    )
