"""Immutable frame observations, numerical settings, and local maneuver state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from carla_agentic_toolkit.actor_boxes import ProjectedBox

TERMINAL_PHASES = frozenset({"completed", "aborted"})
COMMITTED_PHASES = frozenset({"committed", "settling"})


@dataclass(frozen=True, slots=True)
class CorridorBox:
    """World-space box geometry and its conservative longitudinal corridor support."""

    geometry: ProjectedBox
    longitudinal_m: float
    longitudinal_radius_m: float


@dataclass(frozen=True, slots=True)
class ActorObservation:
    """One actor's snapshot kinematics in the verified corridor's metre frame."""

    actor_id: int
    frame: int
    longitudinal_m: float
    lateral_m: float
    speed_mps: float
    yaw_error_degrees: float
    length_m: float
    width_m: float
    lane_id: int
    position_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    velocity_mps: tuple[float, float, float] = (0.0, 0.0, 0.0)
    longitudinal_speed_mps: float | None = None
    box: CorridorBox | None = None


@dataclass(frozen=True, slots=True)
class LaneGeometry:
    """Numerical and semantic lane constraints verified for this corridor only."""

    road_id: int
    source_lane_id: int
    target_lane_id: int
    width_m: float
    target_offset_m: float
    remaining_m: float
    adjacent: bool
    same_direction: bool
    lane_change_allowed: bool
    source_marking: str
    target_marking: str


@dataclass(frozen=True, slots=True)
class MergeObservation:
    """Frame-consistent controlled actors and range-filtered simulator ground truth.

    Controlled actor state remains available for local tracking. Neighbors are
    range filtered; this does not model occlusion or disclose driver intentions.
    """

    run_id: str
    world_generation: str
    frame: int
    simulation_seconds: float
    policy: ActorObservation
    target: ActorObservation
    lane: LaneGeometry
    neighbors: tuple[ActorObservation, ...] = ()
    sensors: tuple[dict[str, object], ...] = ()
    phase: str = "following"
    history: tuple[dict[str, object], ...] = ()
    collision: bool = False
    tracking_error_m: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """Preserve numerical evidence and explicit observation assumptions."""
        return {
            **asdict(self),
            "actor_id": self.policy.actor_id,
            "speed_mps": self.policy.speed_mps,
            "longitudinal_speed_mps": self.policy.longitudinal_speed_mps,
            "observation_mode": "range_filtered_ground_truth",
            "occlusion_model": "none",
            "known_driver_intentions": False,
        }


@dataclass(frozen=True, slots=True)
class PlannerSettings:
    """Versioned finite maneuver, gap, and simulation-time bounds."""

    fixed_delta_seconds: float = 0.05
    target_speed_mps: float = 6.0
    target_vehicle_speed_mps: float = 5.0
    preparation_steps: int = 10
    settling_steps: int = 10
    expiry_frames: int = 20
    crossing_seconds: float = 3.0
    maneuver_timeout_seconds: float = 8.0
    waiting_timeout_seconds: float = 15.0
    minimum_gap_m: float = 8.0
    minimum_ttc_seconds: float = 3.0
    following_headway_seconds: float = 1.5
    tracker_heading_gain: float = 0.9


@dataclass(frozen=True, slots=True)
class ManeuverState:
    """Explicit state; commitment cannot be silently reversed by a deferred reply."""

    phase: str = "following"
    maneuver_generation: int = 0
    first_frame: int | None = None
    committed_frame: int | None = None
    settling_frame: int | None = None
    last_frame: int = -1
    outcome: str | None = None


@dataclass(frozen=True, slots=True)
class LocalControl:
    """Bounded actuator command; all target speeds are metres per second."""

    throttle: float
    brake: float
    steer: float
