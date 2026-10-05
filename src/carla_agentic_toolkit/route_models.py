"""Immutable numerical route observations with no future scenario intentions."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class RouteActor:
    """One snapshot's world kinematics and route-relative position, in SI units."""

    actor_id: int
    kind: str
    x: float
    y: float
    yaw_degrees: float
    speed_mps: float
    vx: float
    vy: float
    length_m: float = 4.8
    width_m: float = 2.0
    progress_m: float = 0.0
    lateral_m: float = 0.0
    lane_id: int = -1
    z: float = 0.5


@dataclass(frozen=True, slots=True)
class TrafficEvidence:
    """Measured separation and an explicitly approximate constant-velocity projection."""

    actor: RouteActor
    clearance_m: float
    predicted_overlap_seconds: float | None
    ahead_gap_m: float | None


@dataclass(frozen=True, slots=True)
class RouteObservation:
    """The provider sees only current numerical observations and bounded past history."""

    run_id: str
    world_generation: str
    frame: int
    simulation_seconds: float
    policy: RouteActor
    route_length_m: float
    traffic: tuple[TrafficEvidence, ...] = ()
    sensors: tuple[dict[str, object], ...] = ()
    history: tuple[dict[str, object], ...] = ()
    collision: bool = False
    tracking_error_m: float = 0.0
    traffic_light: str = "None"
    phase: str = "route_following"

    def to_dict(self) -> dict[str, object]:
        """Expose units and limitations next to the data, including visible cross traffic."""
        return {
            **asdict(self),
            "actor_id": self.policy.actor_id,
            "speed_mps": self.policy.speed_mps,
            "route_progress_m": self.policy.progress_m,
            "route_remaining_m": max(0.0, self.route_length_m - self.policy.progress_m),
            "observation_mode": "range_filtered_ground_truth",
            "occlusion_model": "none",
            "known_driver_intentions": False,
            "prediction": "constant world velocity and yaw, padded 2D boxes, 4s horizon at 0.1s",
        }
