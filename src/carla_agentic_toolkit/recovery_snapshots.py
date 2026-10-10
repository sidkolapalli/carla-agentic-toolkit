"""Fresh frame publication shared only by trusted actor cleanup paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaWorld


def fresh_cleanup_frame(world: CarlaWorld, *, failure_message: str) -> int:
    """Obtain a fresh cleanup frame using the world's current timing mode."""
    before = world.get_snapshot().frame
    if world.get_settings().synchronous_mode:
        frame = world.tick()
    else:
        frame = world.wait_for_tick(5.0).frame
    if frame <= before or world.get_snapshot().frame < frame:
        raise RuntimeError(failure_message)
    return frame
