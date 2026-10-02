"""Pure six-phase maneuver progression and its bounded lateral reference."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from carla_agentic_toolkit.merge_models import (
    COMMITTED_PHASES,
    TERMINAL_PHASES,
    ManeuverState,
    MergeObservation,
    PlannerSettings,
)
from carla_agentic_toolkit.merge_safety import merge_safe, observation_error

if TYPE_CHECKING:
    from collections.abc import Callable

SETTLED_LATERAL_ERROR_M = 0.3
SETTLED_HEADING_ERROR_DEGREES = 5.0


def transition(
    state: ManeuverState, value: MergeObservation, choice: str, settings: PlannerSettings
) -> ManeuverState:
    """Advance a bounded maneuver using only its current frame observation."""
    if state.phase in TERMINAL_PHASES:
        return state
    error = observation_error(value, state)
    if error is not None:
        return replace(state, phase="aborted", outcome=error, last_frame=value.frame)
    prepared = replace(state, first_frame=_first_frame(state, value), last_frame=value.frame)
    return _phase_transition(prepared, value, choice, settings)


def _first_frame(state: ManeuverState, value: MergeObservation) -> int:
    return value.frame if state.first_frame is None else state.first_frame


def _phase_transition(
    state: ManeuverState, value: MergeObservation, choice: str, settings: PlannerSettings
) -> ManeuverState:
    handlers: dict[str, Callable[..., ManeuverState]] = {
        "following": _following,
        "preparing": _preparing,
        "committed": _committed,
        "settling": _settling,
    }
    handler = handlers.get(state.phase)
    if handler is None:
        return replace(state, phase="aborted", outcome="invalid_phase")
    return handler(state, value, choice, settings)


def _following(
    state: ManeuverState, value: MergeObservation, _choice: str, settings: PlannerSettings
) -> ManeuverState:
    if value.frame - _first_frame(state, value) >= settings.preparation_steps:
        return replace(state, phase="preparing")
    return state


def _preparing(
    state: ManeuverState, value: MergeObservation, choice: str, settings: PlannerSettings
) -> ManeuverState:
    if (
        value.frame - _first_frame(state, value)
    ) * settings.fixed_delta_seconds > settings.waiting_timeout_seconds:
        return replace(state, phase="aborted", outcome="merge_wait_timeout")
    if choice == "merge" and merge_safe(value, settings):
        return replace(
            state,
            phase="committed",
            committed_frame=value.frame,
            maneuver_generation=state.maneuver_generation + 1,
        )
    return state


def _committed(
    state: ManeuverState, value: MergeObservation, choice: str, settings: PlannerSettings
) -> ManeuverState:
    if choice == "abort":
        return replace(state, phase="aborted", outcome="policy_abort")
    elapsed = _committed_elapsed(state, value, settings)
    if elapsed > settings.maneuver_timeout_seconds:
        return replace(state, phase="aborted", outcome="maneuver_timeout")
    return _try_settle(state, value, elapsed, settings)


def _try_settle(
    state: ManeuverState, value: MergeObservation, elapsed: float, settings: PlannerSettings
) -> ManeuverState:
    if elapsed >= settings.crossing_seconds and _on_target(value):
        return replace(state, phase="settling", settling_frame=value.frame)
    return state


def _settling(
    state: ManeuverState, value: MergeObservation, choice: str, settings: PlannerSettings
) -> ManeuverState:
    checked = _committed(state, value, choice, settings)
    if checked.phase == "aborted":
        return checked
    return _settle_completion(state, value, settings)


def _settle_completion(
    state: ManeuverState, value: MergeObservation, settings: PlannerSettings
) -> ManeuverState:
    if not _on_target(value):
        return replace(state, settling_frame=value.frame)
    settled_frame = value.frame if state.settling_frame is None else state.settling_frame
    if value.frame - settled_frame >= settings.settling_steps:
        return replace(state, phase="completed", outcome="completed")
    return state


def _on_target(value: MergeObservation) -> bool:
    return (
        abs(value.policy.lateral_m - value.lane.target_offset_m) <= SETTLED_LATERAL_ERROR_M
        and abs(value.policy.yaw_error_degrees) <= SETTLED_HEADING_ERROR_DEGREES
    )


def _committed_elapsed(
    state: ManeuverState, value: MergeObservation, settings: PlannerSettings
) -> float:
    start = value.frame if state.committed_frame is None else state.committed_frame
    return max(0, value.frame - start) * settings.fixed_delta_seconds


def lateral_target(
    state: ManeuverState, value: MergeObservation, settings: PlannerSettings
) -> float:
    """Use a bounded smooth lateral trajectory instead of a behavior controller."""
    if state.phase == "completed":
        return value.lane.target_offset_m
    if state.phase not in COMMITTED_PHASES:
        return 0.0
    progress = min(_committed_elapsed(state, value, settings) / settings.crossing_seconds, 1.0)
    smooth = progress * progress * (3 - 2 * progress)
    return value.lane.target_offset_m * smooth
