"""Perception sensor runtime helpers for script-only CARLA experiments."""

from __future__ import annotations

from queue import Empty, Queue
from typing import TYPE_CHECKING

from carla_mcp.errors import CarlaAdapterError
from carla_mcp.experiment_common import (
    float_attr,
    nested_id,
    optional_transform_dict,
    sensor_actor,
)

if TYPE_CHECKING:
    from pathlib import Path

    from carla_mcp.carla_protocols import CarlaSensor, CarlaWorld


def read_sensor_stream(
    world: CarlaWorld,
    *,
    sensor_id: int,
    frame_count: int,
    output_dir: Path | None,
) -> dict[str, object]:
    """Read several frames from a CARLA sensor and optionally persist captures."""
    sensor = sensor_actor(world, sensor_id)
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
    try:
        sensor.stop()
        destroyed = bool(sensor.destroy())
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc
    return {"sensor_id": sensor_id, "destroyed": destroyed}


def collect_sensor_frames(sensor: CarlaSensor, frame_count: int) -> list[object]:
    """Collect a bounded number of sensor frames server-side."""
    frames: Queue[object] = Queue(maxsize=max(frame_count, 1))
    sensor.listen(frames.put)
    try:
        return [next_sensor_frame(frames) for _ in range(max(frame_count, 0))]
    finally:
        sensor.stop()


def next_sensor_frame(frames: Queue[object]) -> object:
    """Return one sensor frame or raise a CARLA adapter error."""
    try:
        return frames.get(timeout=5.0)
    except Empty as exc:
        msg = "Timed out waiting for a sensor frame."
        raise CarlaAdapterError(msg) from exc


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
        save_to_disk(str(path))
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
