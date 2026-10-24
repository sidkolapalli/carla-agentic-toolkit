"""Rendering preflight for camera sensors whose data requires the GPU renderer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import CarlaAdapterError

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaWorld


def require_sensor_rendering(world: CarlaWorld, sensor_type: str) -> None:
    """Refuse cameras when rendering is disabled or cannot be verified."""
    if not sensor_type.startswith("sensor.camera."):
        return
    try:
        no_rendering = world.get_settings().no_rendering_mode
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        message = f"Could not verify WorldSettings.no_rendering_mode for {sensor_type}: {exc}"
        raise CarlaAdapterError(message) from exc
    if not isinstance(no_rendering, bool):
        message = f"WorldSettings.no_rendering_mode must be a boolean for {sensor_type}."
        raise CarlaAdapterError(message)
    if no_rendering:
        message = (
            f"Camera sensor {sensor_type} requires rendering; "
            "WorldSettings.no_rendering_mode=True is unsupported."
        )
        raise CarlaAdapterError(message)
