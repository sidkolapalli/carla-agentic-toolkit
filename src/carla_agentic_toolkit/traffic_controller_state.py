"""Immutable controller observations and the incomplete-reset state transition."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from carla_agentic_toolkit.models import TrafficControllerStartRequest, TrafficDensityRequest
    from carla_agentic_toolkit.traffic_controller_step import TrafficControllerStep
    from carla_agentic_toolkit.traffic_frame_wait import ResetProgress


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """One generation's desired configuration and last applied observations.

    Only a matching revision may consume its desired one-shot reset. Applied
    observations never replace a newer request, and stopping generations may
    not publish completed work. Registration never grants deletion ownership.
    """

    request: TrafficControllerStartRequest
    active: bool
    vehicle_count: int = 0
    moving_vehicle_count: int = 0
    last_error: str | None = None
    stopping: bool = False
    generation: int = 0
    revision: int = 0
    applied_revision: int | None = None
    applied_density: TrafficDensityRequest | None = None
    registered_actor_ids: frozenset[int] = frozenset()
    owned_actor_ids: frozenset[int] = frozenset()
    error_type: str | None = None
    conflict: dict[str, object] | None = None
    frame_wait_phase: str | None = None
    reset_progress: dict[str, object] | None = None


def commit_completed_step(
    state: RuntimeState, result: TrafficControllerStep, revision: int
) -> RuntimeState:
    """Keep desired and applied observations distinct for a completed maintenance pass."""
    request = result.request if revision == state.revision else state.request
    conflict = result.conflict
    return replace(
        state,
        request=request,
        vehicle_count=result.vehicle_count,
        moving_vehicle_count=result.moving_vehicle_count,
        registered_actor_ids=result.registered_actor_ids,
        owned_actor_ids=result.owned_actor_ids,
        applied_density=result.request.density,
        applied_revision=revision,
        conflict=conflict,
        error_type="density_conflict" if conflict else None,
        last_error="Protected vehicles exceed the density target." if conflict else None,
        frame_wait_phase=None,
        reset_progress=None,
    )


def commit_reset_progress(
    state: RuntimeState, progress: ResetProgress, revision: int
) -> RuntimeState:
    """Preserve prior observations and consume only the completed matching reset."""
    request = state.request
    if progress.destroy_phase_completed and revision == state.revision:
        request = replace(request, density=replace(request.density, reset_existing=False))
    destroyed = frozenset(progress.destroyed_actor_ids)
    return replace(
        state,
        request=request,
        registered_actor_ids=state.registered_actor_ids - destroyed,
        owned_actor_ids=state.owned_actor_ids - destroyed,
        reset_progress=progress.to_dict(),
    )
