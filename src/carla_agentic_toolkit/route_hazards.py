"""Actor-origin exposure evidence from owned snapshots, separate from control intent."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from carla_agentic_toolkit.route_scenarios import DURATIONS

if TYPE_CHECKING:
    from carla_agentic_toolkit.route_geometry import RoutePath
    from carla_agentic_toolkit.route_models import RouteActor
    from carla_agentic_toolkit.route_scenarios import HazardState

MEASUREMENT_VERSION = "route-hazard-measurements-v1"
HAZARD_ROLES = {"pedestrian_crossing": "pedestrian", "cut_in": "lead"}
HAZARD_PROGRESS_M = {"pedestrian_crossing": 32.0, "cut_in": 14.0}


@dataclass(frozen=True, slots=True)
class HazardSample:
    """One owned actor's numerical state at an authoritative simulation frame."""

    actor_id: int
    frame: int
    simulation_seconds: float
    x: float
    y: float
    z: float
    vx: float
    vy: float
    speed_mps: float
    lateral_m: float

    def event(self) -> dict[str, object]:
        """Label the first observed sample, without interpolating an exact crossing time."""
        return {"frame": self.frame, "simulation_seconds": self.simulation_seconds}


class HazardMeasurements:
    """Keep a fixed lane tangent and predeclared deadline; never actuate an actor."""

    def __init__(self, path: RoutePath, scenario: str) -> None:
        """Bind geometry before the first command or physical observation."""
        self.scenario = scenario
        self.role = HAZARD_ROLES.get(scenario)
        self.anchor = path.sample(HAZARD_PROGRESS_M.get(scenario, 0.0))
        self.duration = DURATIONS[scenario]
        self.started: float | None = None
        self.initial: HazardSample | None = None
        self.latest: HazardSample | None = None
        self.entry: HazardSample | None = None
        self.crossing: HazardSample | None = None
        self.commanded: dict[str, object] = {"duration_seconds": self.duration}

    def observe(self, values: dict[str, RouteActor], frame: int, seconds: float) -> None:
        """Read full owned state even when it is outside the provider's sensing range."""
        if self.role is None:
            return
        previous = self.latest
        self.latest = self._sample(values[self.role], frame, seconds)
        if self.started is not None and previous is not None:
            self._measure_events(previous, self.latest)

    def command(self, state: HazardState, values: dict[str, object]) -> None:
        """Record declared control intent and bind its initial observed position once."""
        self.commanded = {"duration_seconds": self.duration, **values}
        if self.started is None and state.started_seconds is not None:
            self.started = state.started_seconds
            self.initial = self.latest

    def _sample(self, actor: RouteActor, frame: int, seconds: float) -> HazardSample:
        yaw = math.radians(self.anchor.yaw_degrees)
        lateral = -(actor.x - self.anchor.x) * math.sin(yaw) + (actor.y - self.anchor.y) * math.cos(
            yaw
        )
        return HazardSample(
            actor.actor_id,
            frame,
            seconds,
            actor.x,
            actor.y,
            actor.z,
            actor.vx,
            actor.vy,
            actor.speed_mps,
            lateral,
        )

    def _measure_events(self, previous: HazardSample, current: HazardSample) -> None:
        if not self._started_outside():
            return
        self._record_entry(previous, current)
        self._record_crossing(previous, current)

    def _record_entry(self, previous: HazardSample, current: HazardSample) -> None:
        if self.entry is None and self._entered_lane(previous, current):
            self.entry = current

    def _record_crossing(self, previous: HazardSample, current: HazardSample) -> None:
        if self.crossing is None and previous.lateral_m > 0.0 >= current.lateral_m:
            self.crossing = current

    def _started_outside(self) -> bool:
        return self.initial is not None and self.initial.lateral_m > self.anchor.width_m / 2.0

    def _entered_lane(self, previous: HazardSample, current: HazardSample) -> bool:
        half_width = self.anchor.width_m / 2.0
        return previous.lateral_m > half_width and abs(current.lateral_m) <= half_width

    @property
    def deadline(self) -> float | None:
        """The existing command duration is also the declared physical exposure deadline."""
        return None if self.started is None else self.started + self.duration

    def summary(self, *, termination: str | None = None) -> dict[str, object]:
        """Report intent, observed geometry and trial validity independently of route success."""
        valid, status, reason = self._classification(termination)
        return {
            "measurement_version": MEASUREMENT_VERSION,
            "scenario": self.scenario,
            "valid": valid,
            "status": status,
            "reason": reason,
            "termination": termination,
            "deadline_seconds": self.deadline,
            "criterion": self._criterion(),
            "geometry": {
                "reference": "snapshot actor-origin position against fixed local lane tangent",
                "x": self.anchor.x,
                "y": self.anchor.y,
                "yaw_degrees": self.anchor.yaw_degrees,
                "lane_width_m": self.anchor.width_m,
                "road_id": self.anchor.road_id,
                "lane_id": self.anchor.lane_id,
            },
            "commanded": self.commanded.copy(),
            "measured": self._measured(),
        }

    def _criterion(self) -> str | None:
        return {
            "cut_in": "snapshot actor-origin outside-to-inside lane entry",
            "pedestrian_crossing": "snapshot actor-origin lane entry then centreline crossing",
        }.get(self.scenario)

    def _classification(self, termination: str | None) -> tuple[bool | None, str, str | None]:
        if self.role is None:
            return None, "unverified", "physical_criterion_not_defined"
        if self.started is None:
            return self._untriggered(termination)
        if self._achieved():
            return True, "achieved", None
        if self._expired():
            return False, "invalid", self._missing_reason()
        return self._unfinished(termination)

    @staticmethod
    def _untriggered(termination: str | None) -> tuple[bool | None, str, str | None]:
        if termination is not None:
            return False, "invalid", "hazard_not_triggered"
        return None, "not_triggered", None

    @staticmethod
    def _unfinished(termination: str | None) -> tuple[bool | None, str, str | None]:
        if termination is not None:
            return False, "invalid", "trial_ended_before_deadline"
        return None, "pending", None

    def _achieved(self) -> bool:
        completed = self._completion_seconds()
        deadline = self.deadline
        return completed is not None and deadline is not None and completed <= deadline

    def _completion_seconds(self) -> float | None:
        if self.entry is None:
            return None
        if self.scenario == "cut_in":
            return self.entry.simulation_seconds
        return self._crossing_seconds()

    def _crossing_seconds(self) -> float | None:
        if self.crossing is None or self.entry is None:
            return None
        if self.crossing.frame < self.entry.frame:
            return None
        return self.crossing.simulation_seconds

    def _expired(self) -> bool:
        deadline = self.deadline
        return (
            self.latest is not None
            and deadline is not None
            and self.latest.simulation_seconds >= deadline
        )

    def _missing_reason(self) -> str:
        if not self._started_outside():
            return "hazard_not_outside_lane_at_trigger"
        if self.entry is None or self.scenario == "cut_in":
            return "lane_entry_not_observed_by_deadline"
        return "centreline_crossing_not_observed_by_deadline"

    def _measured(self) -> dict[str, object]:
        values = {} if self.latest is None else asdict(self.latest)
        return {
            **values,
            "displacement_m": self._displacement(),
            "first_lane_entry": None if self.entry is None else self.entry.event(),
            "first_centreline_crossing": None if self.crossing is None else self.crossing.event(),
        }

    def _displacement(self) -> float | None:
        if self.initial is None or self.latest is None:
            return None
        return math.hypot(self.latest.x - self.initial.x, self.latest.y - self.initial.y)
