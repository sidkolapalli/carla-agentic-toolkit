"""Perception sensor runtime helpers for script-only CARLA experiments."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.experiment_common import (
    float_attr,
    nested_id,
    optional_transform_dict,
    sensor_actor,
)
from carla_agentic_toolkit.sensor_rendering import require_sensor_rendering
from carla_agentic_toolkit.sensor_subscription import SensorSubscription, validate_capacity
from carla_agentic_toolkit.world_timing import require_world_mode

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaSensor, CarlaWorld


def read_sensor_stream(
    world: CarlaWorld,
    *,
    sensor_id: int,
    frame_count: int,
    output_dir: Path | None,
    after_rendering_check: Callable[[], None] | None = None,
) -> dict[str, object]:
    """Read several frames from a CARLA sensor and optionally persist captures."""
    sensor = sensor_actor(world, sensor_id)
    require_sensor_rendering(world, sensor.type_id)
    if sensor.type_id.startswith("sensor.camera.") and after_rendering_check is not None:
        after_rendering_check()
    require_async_sensor_read(world)
    frames = collect_sensor_frames(sensor, frame_count)
    saved_paths = save_sensor_frames(frames, sensor_id, output_dir)
    return {
        "sensor_id": sensor_id,
        "requested_frames": frame_count,
        "received_frames": len(frames),
        "frames": [sensor_frame_digest(frame) for frame in frames],
        "paths": [str(path) for path in saved_paths],
    }


def detach_sensor(world: CarlaWorld, sensor_id: int) -> dict[str, object]:
    """Stop and destroy a sensor actor."""
    sensor = sensor_actor(world, sensor_id)
    return detach_sensor_handle(sensor)


def detach_sensor_handle(sensor: CarlaSensor) -> dict[str, object]:
    """Release a created sensor even before its first world snapshot exists."""
    stop_sensor_handle(sensor)
    try:
        destroyed = bool(sensor.destroy())
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc
    return {"sensor_id": sensor.id, "destroyed": destroyed}


def stop_sensor_handle(sensor: CarlaSensor) -> None:
    """Stop only a remaining listener before authoritative sensor destruction."""
    try:
        state = getattr(sensor, "is_listening", True)
        listening = state() if callable(state) else state
        if listening:
            sensor.stop()
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc


def collect_sensor_frames(sensor: CarlaSensor, frame_count: int) -> list[object]:
    """Collect bounded asynchronous frames using the shared listener lifetime."""
    if frame_count == 0:
        return []
    validate_capacity(frame_count)
    subscription = SensorSubscription(sensor, capacity=frame_count)
    try:
        return [subscription.next_frame() for _ in range(frame_count)]
    finally:
        subscription.close()


def require_async_sensor_read(world: CarlaWorld) -> None:
    """Fail fast when a blocking read would prevent its synchronous owner ticking."""
    message = (
        "Blocking sensor collection is unavailable in synchronous mode; use "
        "subscribe_sensor, tick as the world owner, drain_sensor, "
        "then close_sensor_subscription."
    )
    require_world_mode(world, synchronous_mode=False, message=message)


def save_sensor_frames(
    frames: list[object],
    sensor_id: int,
    output_dir: Path | None,
) -> list[Path]:
    """Save frames that expose save_to_disk and return paths."""
    if output_dir is None:
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    return [save_sensor_frame(frame, sensor_id, output_dir) for frame in frames]


def save_sensor_frame(frame: object, sensor_id: int, output_dir: Path) -> Path:
    """Save one frame when the frame supports CARLA image persistence."""
    frame_id = int(getattr(frame, "frame", 0))
    path = output_dir / f"sensor-{sensor_id}-{frame_id}.png"
    save_to_disk = getattr(frame, "save_to_disk", None)
    if callable(save_to_disk):
        # A current-directory capture still needs a nonempty native parent path.
        save_to_disk(str(path.absolute()))
    return path


def sensor_frame_digest(frame: object) -> dict[str, object]:
    """Return compact metadata for a sensor frame or event."""
    return {
        "frame": int_attr(frame, "frame"),
        "timestamp": float_attr(frame, "timestamp"),
        "type": type(frame).__name__,
        "actor_id": nested_id(frame, "actor"),
        "other_actor_id": nested_id(frame, "other_actor"),
        "transform": optional_transform_dict(getattr(frame, "transform", None)),
    }


def int_attr(target: object, name: str) -> int | None:
    """Return an integer attribute when present."""
    value = getattr(target, name, None)
    if isinstance(value, int):
        return value
    return None
