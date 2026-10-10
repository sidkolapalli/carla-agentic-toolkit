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
from carla_agentic_toolkit.experiment_ground_truth import image_metadata
from carla_agentic_toolkit.sensor_evidence import save_frame
from carla_agentic_toolkit.sensor_rendering import require_sensor_rendering
from carla_agentic_toolkit.sensor_subscription import SensorSubscription, validate_capacity
from carla_agentic_toolkit.world_timing import require_world_mode

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaSensor, CarlaWorld
    from carla_agentic_toolkit.sensor_memory import SensorQueueBudget


def read_sensor_stream(  # noqa: PLR0913 -- Keep operation and lifecycle/memory guards explicit.
    world: CarlaWorld,
    *,
    sensor_id: int,
    frame_count: int,
    output_dir: Path | None,
    after_rendering_check: Callable[[], None] | None = None,
    byte_budget: SensorQueueBudget | None = None,
) -> dict[str, object]:
    """Read several frames from a CARLA sensor and optionally persist captures."""
    sensor = sensor_actor(world, sensor_id)
    require_sensor_rendering(world, sensor.type_id)
    if sensor.type_id.startswith("sensor.camera.") and after_rendering_check is not None:
        after_rendering_check()
    require_async_sensor_read(world)
    frames = collect_sensor_frames(sensor, frame_count, byte_budget=byte_budget)
    saved_paths = save_sensor_frames(frames, sensor_id, output_dir, sensor_type=sensor.type_id)
    return {
        "sensor_id": sensor_id,
        "requested_frames": frame_count,
        "received_frames": len(frames),
        "frames": sensor_frame_digests(frames, sensor),
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


def collect_sensor_frames(
    sensor: CarlaSensor, frame_count: int, *, byte_budget: SensorQueueBudget | None = None
) -> list[object]:
    """Collect bounded asynchronous frames using the shared listener lifetime."""
    validate_collection_count(frame_count)
    if frame_count == 0:
        return []
    subscription = SensorSubscription(sensor, capacity=frame_count, byte_budget=byte_budget)
    try:
        return _collect_bounded(subscription, frame_count)
    finally:
        subscription.close()


def _collect_bounded(subscription: SensorSubscription, frame_count: int) -> list[object]:
    """Keep returned raw data inside the same ceiling, not only pending queue data."""
    frames: list[object] = []
    retained_bytes = 0
    for _ in range(frame_count):
        sample = subscription.next_sample()
        retained_bytes += sample.size_bytes
        if retained_bytes > subscription.byte_limit:
            message = "Collected sensor frames exceed the queue byte budget."
            raise CarlaAdapterError(message)
        frames.append(sample.data)
    return frames


def validate_collection_count(frame_count: int) -> None:
    """Accept an empty collection or a strictly bounded positive count."""
    if type(frame_count) is int and frame_count == 0:
        return
    validate_capacity(frame_count)


def validate_save_frames(*, save_frames: bool) -> None:
    """Require explicit boolean opt-in before consuming a drain's raw samples."""
    if type(save_frames) is not bool:
        message = "Sensor save_frames must be a boolean."
        raise CarlaAdapterError(message)


def save_drained_frames(
    frames: list[object],
    sensor_id: int,
    output_dir: Path | None,
    *,
    sensor_type: str,
    save_frames: bool,
) -> list[Path]:
    """Do no image encoding or directory writes without explicit drain opt-in."""
    return save_sensor_frames(
        frames, sensor_id, output_dir if save_frames else None, sensor_type=sensor_type
    )


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
    *,
    sensor_type: str = "sensor.camera.rgb",
) -> list[Path]:
    """Save frames that expose save_to_disk and return paths."""
    if output_dir is None:
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    return [
        path
        for frame in frames
        if (path := save_sensor_frame(frame, sensor_id, output_dir, sensor_type=sensor_type))
        is not None
    ]


def save_sensor_frame(
    frame: object, sensor_id: int, output_dir: Path, *, sensor_type: str = "sensor.camera.rgb"
) -> Path | None:
    """Save one frame when the frame supports CARLA image persistence."""
    if not callable(getattr(frame, "save_to_disk", None)):
        return None
    frame_id = int(getattr(frame, "frame", 0))
    suffix = ".ply" if sensor_type.startswith("sensor.lidar.") else ".png"
    path = output_dir / f"sensor-{sensor_id}-{frame_id}{suffix}"
    save_frame(frame, path)
    return path


def sensor_frame_digests(frames: list[object], sensor: CarlaSensor) -> list[dict[str, object]]:
    """Share retained sensor context between stream and non-ticking drain results."""
    attributes = getattr(sensor, "attributes", None)
    return [
        sensor_frame_digest(frame, camera_attributes=attributes, sensor_type=sensor.type_id)
        for frame in frames
    ]


def sensor_frame_digest(
    frame: object,
    *,
    camera_attributes: Mapping[str, str] | None = None,
    sensor_type: str | None = None,
) -> dict[str, object]:
    """Return compact metadata for a sensor frame or event."""
    return {
        "frame": int_attr(frame, "frame"),
        "timestamp": float_attr(frame, "timestamp"),
        "type": type(frame).__name__,
        "actor_id": nested_id(frame, "actor"),
        "other_actor_id": nested_id(frame, "other_actor"),
        "transform": optional_transform_dict(getattr(frame, "transform", None)),
        **image_metadata(frame, camera_attributes, sensor_type),
    }


def int_attr(target: object, name: str) -> int | None:
    """Return an integer attribute when present."""
    value = getattr(target, name, None)
    if isinstance(value, int):
        return value
    return None
