"""Fatal density frame waits and truthful evidence for incomplete reset phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import CarlaAdapterError, OwnershipError, UnsupportedFeatureError

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaWorld
    from carla_agentic_toolkit.traffic_controller_step import TrafficControllerStep


@dataclass(frozen=True, slots=True)
class ResetProgress:
    """Confirmed reset actions, separate from completed density observations."""

    phase: str
    world_id: int | None
    destroy_phase_completed: bool
    destroyed_actor_ids: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Expose JSON-safe acknowledgements without inventing vehicle counts."""
        return {
            "phase": self.phase,
            "world_id": self.world_id,
            "destroy_phase_completed": self.destroy_phase_completed,
            "destroyed_actor_ids": list(self.destroyed_actor_ids),
        }


class FrameWaitError(CarlaAdapterError):
    """Stop maintenance while retaining actual lifecycle work before the timeout."""

    def __init__(self, message: str, *, phase: str) -> None:
        """Keep native objects out of public error details."""
        super().__init__(message, details={"error_type": "frame_wait_failed", "phase": phase})
        self.phase = phase
        self.completed_step: TrafficControllerStep | None = None
        self.reset_progress: ResetProgress | None = None

    def record_reset(self, progress: ResetProgress) -> None:
        """Retain typed internal evidence and its public JSON representation."""
        self.reset_progress = progress
        self.details["reset_progress"] = progress.to_dict()

    def preserve_evidence(self, record: Callable[[], None]) -> None:
        """Keep a native wait fatal even when its trusted lifecycle callback fails."""
        try:
            record()
        except (OwnershipError, UnsupportedFeatureError):
            raise
        except (AttributeError, CarlaAdapterError, RuntimeError, TypeError, ValueError) as exc:
            self.details["evidence_error"] = str(exc)
            self.args = (f"{self}; lifecycle evidence failed: {exc}",)


def wait_for_density_frame(world: CarlaWorld, *, phase: str) -> None:
    """One missed frame is fatal; do not sleep, reconnect, or retry the wait."""
    try:
        world.wait_for_tick(1.0)
    except (OwnershipError, UnsupportedFeatureError):
        raise
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        message = f"Traffic density wait_for_tick failed during {phase}: {exc}"
        raise FrameWaitError(message, phase=phase) from exc
