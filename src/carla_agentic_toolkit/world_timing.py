"""Mode checks and bounded pacing for clients that own or observe world frames."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import CarlaAdapterError

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaWorld

TICK_MODE_MESSAGE = "Ticking requires synchronous mode; use wait in asynchronous mode."
WAIT_MODE_MESSAGE = (
    "Blocking frame waits are unavailable in synchronous mode; tick as the world owner."
)


def require_world_mode(world: CarlaWorld, *, synchronous_mode: bool, message: str) -> None:
    """Reject an unreadable or incompatible mode before native timing operations."""
    if world_synchronous_mode(world) is not synchronous_mode:
        raise CarlaAdapterError(message)


def world_synchronous_mode(world: CarlaWorld) -> bool:
    """Read and validate mode without inspecting actors or unrelated settings."""
    try:
        current = world.get_settings().synchronous_mode
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc
    if not isinstance(current, bool):
        error = "CARLA synchronous_mode must be a boolean."
        raise CarlaAdapterError(error)
    return current


def wait_for_frames(
    world: CarlaWorld, seconds: float, *, rpc_timeout_seconds: Callable[[], float]
) -> float:
    """Observe asynchronous frames for a clamped wall-time window without ticking."""
    if math.isnan(seconds):
        message = "Wait duration must not be NaN."
        raise CarlaAdapterError(message)
    bounded_seconds = min(max(seconds, 0.0), 60.0)
    rpc_timeout_seconds()
    require_world_mode(world, synchronous_mode=False, message=WAIT_MODE_MESSAGE)
    deadline = time.monotonic() + bounded_seconds
    while time.monotonic() < deadline:
        _wait_for_frame(world, deadline, rpc_timeout_seconds)
    return bounded_seconds


def _wait_for_frame(
    world: CarlaWorld, deadline: float, rpc_timeout_seconds: Callable[[], float]
) -> None:
    rpc_timeout_seconds()
    require_world_mode(world, synchronous_mode=False, message=WAIT_MODE_MESSAGE)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return
    timeout = min(1.0, remaining, rpc_timeout_seconds())
    try:
        world.wait_for_tick(timeout)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc
