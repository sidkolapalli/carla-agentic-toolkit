"""Read-only, frame-coherent geometry and measured camera metadata."""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.actor_boxes import bounding_box_metadata
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.experiment_common import (
    actor,
    call_required,
    transform_dict,
    vector_dict,
)
from carla_agentic_toolkit.tool_inputs import parse_location

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from carla_agentic_toolkit.carla_protocols import CarlaTelemetrySnapshot, CarlaWorld
    from carla_agentic_toolkit.models import Location

MAX_GROUND_TRUTH_ACTORS = 1000
RAW_IMAGE_REPRESENTATION = "carla.Image.raw_data (32-bit BGRA)"
BOX_VERTEX_COUNT = 8
MAX_CAMERA_FOV = 180


def query_origin(payload: dict[str, object] | None) -> Location | None:
    """Use the shared location parser and normalize only this query's input errors."""
    if payload is None:
        return None
    try:
        origin = parse_location(payload)
        finite_vector(origin)
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc
    else:
        return origin


def finite_vector(value: object) -> dict[str, object]:
    """Serialize measured coordinates, refusing nonfinite native geometry."""
    result = vector_dict(value)
    _require_finite(result.values())
    return result


def _require_finite(values: Iterable[object]) -> None:
    if not all(math.isfinite(cast("float", value)) for value in values):
        message = "Ground-truth coordinates must be finite."
        raise CarlaAdapterError(message)


def actor_bounding_boxes(world: CarlaWorld, actor_ids: tuple[int, ...]) -> dict[str, object]:
    """Transform native local boxes with actor transforms from one selected frame."""
    _validate_actor_ids(actor_ids)
    try:
        snapshot = cast("CarlaTelemetrySnapshot", world.get_snapshot())
        if type(snapshot.frame) is not int or snapshot.frame < 0:
            message = "World snapshot frame must be a nonnegative integer."
            raise CarlaAdapterError(message)
        return {
            "frame": snapshot.frame,
            "coordinate_space": "world",
            "bounding_boxes": [_actor_box(world, snapshot, actor_id) for actor_id in actor_ids],
        }
    except (AttributeError, OverflowError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc


def _validate_actor_ids(actor_ids: tuple[int, ...]) -> None:
    if not 1 <= len(actor_ids) <= MAX_GROUND_TRUTH_ACTORS:
        message = f"actor_ids must contain 1-{MAX_GROUND_TRUTH_ACTORS} IDs."
        raise CarlaAdapterError(message)
    if any(type(value) is not int or value <= 0 for value in actor_ids):
        message = "actor_ids must contain positive integers."
        raise CarlaAdapterError(message)


def _actor_box(
    world: CarlaWorld, snapshot: CarlaTelemetrySnapshot, actor_id: int
) -> dict[str, object]:
    state = snapshot.find(actor_id)
    if state is None:
        message = f"Actor {actor_id} was not found in world snapshot frame {snapshot.frame}."
        raise CarlaAdapterError(message)
    transform = state.get_transform()
    measured_transform = transform_dict(transform)
    for components in measured_transform.values():
        _require_finite(cast("dict[str, float]", components).values())
    bounds = cast("Any", actor(world, actor_id)).bounding_box
    metadata = bounding_box_metadata(bounds)
    corners = cast("Iterable[object]", call_required(bounds, "get_world_vertices", transform))
    vertices = [finite_vector(value) for value in corners]
    if len(vertices) != BOX_VERTEX_COUNT:
        message = "Native bounding box must supply eight world vertices."
        raise CarlaAdapterError(message)
    return {"actor_id": actor_id, "bounding_box": metadata, "vertices": vertices}


def camera_intrinsics(world: CarlaWorld, sensor_id: int) -> dict[str, object]:
    """Build the tutorial K matrix from actual camera attributes, without Listen."""
    try:
        camera = cast("Any", actor(world, sensor_id))
        if not camera.type_id.startswith("sensor.camera."):
            message = f"Actor {sensor_id} is not a camera sensor."
            raise CarlaAdapterError(message)
        attributes = cast("Mapping[str, str]", camera.attributes)
        width = _dimension_attribute(attributes, "image_size_x")
        height = _dimension_attribute(attributes, "image_size_y")
        fov = camera_fov(attributes)
        focal = _focal_length(width, fov)
        _require_finite((focal, width / 2.0, height / 2.0))
        return {
            "sensor_id": sensor_id,
            "width": width,
            "height": height,
            "fov": fov,
            "intrinsic_matrix": [
                [focal, 0.0, width / 2.0],
                [0.0, focal, height / 2.0],
                [0.0, 0.0, 1.0],
            ],
        }
    except (AttributeError, KeyError, OverflowError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc


def _focal_length(width: int, fov: float) -> float:
    denominator = 2.0 * math.tan(math.radians(fov) / 2.0)
    if denominator == 0:
        message = "Camera FOV produces an unrepresentable focal length."
        raise CarlaAdapterError(message)
    return width / denominator


def _dimension_attribute(attributes: Mapping[str, str], name: str) -> int:
    value = attributes[name]
    if not isinstance(value, str):
        message = f"Camera attribute {name} must be an integer string."
        raise CarlaAdapterError(message)
    return _positive_dimension(int(value), name)


def _positive_dimension(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        message = f"Image {name} must be a positive integer."
        raise CarlaAdapterError(message)
    return value


def camera_fov(attributes: Mapping[str, str]) -> float:
    """Read the actual horizontal field of view, never a guessed default."""
    value = attributes["fov"]
    if not isinstance(value, str):
        message = "Camera attribute fov must be a finite numeric string."
        raise CarlaAdapterError(message)
    fov = float(value)
    if not math.isfinite(fov) or not 0 < fov < MAX_CAMERA_FOV:
        message = "Camera attribute fov must be finite and between 0 and 180 degrees."
        raise CarlaAdapterError(message)
    return fov


def image_metadata(
    frame: object, camera_attributes: Mapping[str, str] | None, sensor_type: str | None
) -> dict[str, object]:
    """Hash image measurements, retaining unknown FOV when no camera context exists."""
    if not _is_bgra_measurement(frame, sensor_type):
        return {}
    try:
        width, height, raw = _image_measurements(frame)
        fov = camera_fov(camera_attributes) if camera_attributes is not None else None
        return {
            "width": width,
            "height": height,
            "fov": fov,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "sha256_representation": RAW_IMAGE_REPRESENTATION,
        }
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc


def _is_bgra_measurement(frame: object, sensor_type: str | None) -> bool:
    if sensor_type in {"sensor.camera.dvs", "sensor.camera.optical_flow"}:
        return False
    return hasattr(frame, "width") or hasattr(frame, "height")


def _image_measurements(frame: object) -> tuple[int, int, memoryview]:
    image = cast("Any", frame)
    width = _positive_dimension(image.width, "width")
    height = _positive_dimension(image.height, "height")
    raw = _bgra_buffer(image.raw_data, width * height * 4)
    return width, height, raw


def _bgra_buffer(raw: object, expected_bytes: int) -> memoryview:
    if not isinstance(raw, bytes | bytearray | memoryview):
        message = "Image raw_data must contain complete 32-bit BGRA pixels."
        raise CarlaAdapterError(message)
    view = memoryview(raw)
    if not _complete_byte_view(view, expected_bytes):
        message = "Image raw_data must contain contiguous complete 32-bit BGRA bytes."
        raise CarlaAdapterError(message)
    return view


def _complete_byte_view(view: memoryview, expected_bytes: int) -> bool:
    return (
        view.c_contiguous
        and view.ndim == 1
        and view.itemsize == 1
        and view.nbytes == expected_bytes
    )
