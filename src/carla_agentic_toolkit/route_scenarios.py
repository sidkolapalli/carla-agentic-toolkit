"""Reviewed physical hazard schedules; this module's state is never provider input."""

from dataclasses import dataclass

SCENARIO_VERSION = "route-hazards-v1"
TRIGGER_PROGRESS_M = 12.0
DURATIONS = {"lead_brake": 3.0, "cut_in": 2.5, "pedestrian_crossing": 6.0}


@dataclass(frozen=True, slots=True)
class HazardState:
    """One progress-triggered event measured in simulation time."""

    started_seconds: float | None = None
    elapsed_seconds: float = 0.0
    active: bool = False


def update_hazard(
    state: HazardState, scenario: str, *, progress_m: float, seconds: float
) -> HazardState:
    """Activate after 12m of actual progress, then release without retriggering."""
    started = state.started_seconds
    if started is None and progress_m >= TRIGGER_PROGRESS_M:
        started = seconds
    if started is None:
        return state
    elapsed = seconds - started
    return HazardState(started, elapsed, elapsed < DURATIONS[scenario])
