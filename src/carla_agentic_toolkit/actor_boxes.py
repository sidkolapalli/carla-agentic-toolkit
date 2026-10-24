"""Project actor-relative CARLA bounds without changing actor control coordinates."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

from carla_agentic_toolkit.errors import CarlaAdapterError

type Vector3 = tuple[float, float, float]
type Axis2 = tuple[float, float]
MIN_FORWARD_PROJECTION = 1e-12


@dataclass(frozen=True, slots=True)
class ProjectedBox:
    """An oriented XY enclosure of all eight native world-space box corners."""

    center_m: Vector3
    yaw_degrees: float
    length_m: float
    width_m: float

    @property
    def axes(self) -> tuple[Axis2, Axis2]:
        """Return orthonormal planar axes, including for a tilted native box."""
        yaw = math.radians(self.yaw_degrees)
        return (math.cos(yaw), math.sin(yaw)), (-math.sin(yaw), math.cos(yaw))

    def radius(self, axis: Axis2, *, padding: float = 0.0) -> float:
        """Measure support along an axis, with optional per-side planar padding."""
        forward, side = self.axes
        return abs(_dot2(forward, axis)) * (self.length_m / 2 + padding) + abs(
            _dot2(side, axis)
        ) * (self.width_m / 2 + padding)


def _triplet(value: object, names: tuple[str, str, str]) -> Vector3:
    values = cast("Vector3", tuple(float(getattr(value, name)) for name in names))
    if not all(math.isfinite(item) for item in values):
        message = "native coordinates must be finite"
        raise ValueError(message)
    return values


def bounding_box_metadata(bounds: object) -> dict[str, dict[str, float]]:
    """Retain measured native local offsets, rotations and half-extents, never defaults."""
    try:
        native = cast("Any", bounds)
        location = _triplet(native.location, ("x", "y", "z"))
        rotation = _triplet(native.rotation, ("pitch", "yaw", "roll"))
        extent = _triplet(native.extent, ("x", "y", "z"))
        _require_extents(extent)
    except (AttributeError, RuntimeError, TypeError, ValueError) as error:
        message = f"Native bounding box geometry is unavailable or invalid: {error}"
        raise CarlaAdapterError(message) from error
    return {
        "location": dict(zip(("x", "y", "z"), location, strict=True)),
        "rotation": dict(zip(("pitch", "yaw", "roll"), rotation, strict=True)),
        "extent": dict(zip(("x", "y", "z"), extent, strict=True)),
    }


def _require_extents(extent: Vector3) -> None:
    if any(value < 0 for value in extent):
        message = "native box half-extents must be nonnegative"
        raise ValueError(message)


def _rotate(vector: Vector3, rotation: Vector3) -> Vector3:
    pitch, yaw, roll = (math.radians(value) for value in rotation)
    x, y, z = vector
    # CARLA's left-handed basis: negative roll/pitch, then positive yaw.
    y, z = math.cos(roll) * y + math.sin(roll) * z, -math.sin(roll) * y + math.cos(roll) * z
    x, z = math.cos(pitch) * x - math.sin(pitch) * z, math.sin(pitch) * x + math.cos(pitch) * z
    return math.cos(yaw) * x - math.sin(yaw) * y, math.sin(yaw) * x + math.cos(yaw) * y, z


def _dot2(first: tuple[float, ...], second: Axis2) -> float:
    return first[0] * second[0] + first[1] * second[1]


def _planar_heading(axes: tuple[Vector3, ...]) -> float:
    forward, side = axes[:2]
    if math.hypot(forward[0], forward[1]) < MIN_FORWARD_PROJECTION:
        return math.degrees(math.atan2(-side[0], side[1]))
    return math.degrees(math.atan2(forward[1], forward[0]))


def project_actor_box(transform: object, bounds: object) -> ProjectedBox:
    """Compose full actor/box rotations; conservatively retain projected height."""
    measured = bounding_box_metadata(bounds)
    runtime = cast("Any", transform)
    origin = _triplet(runtime.location, ("x", "y", "z"))
    rotation = _triplet(runtime.rotation, ("pitch", "yaw", "roll"))
    local_rotation = cast("Vector3", tuple(measured["rotation"].values()))
    offset = _rotate(cast("Vector3", tuple(measured["location"].values())), rotation)
    center = cast("Vector3", tuple(a + b for a, b in zip(origin, offset, strict=True)))
    axes = tuple(
        _rotate(_rotate(unit, local_rotation), rotation)
        for unit in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    )
    heading = _planar_heading(axes)
    yaw = math.radians(heading)
    extent = tuple(measured["extent"].values())
    length = _projected_size(axes, extent, (math.cos(yaw), math.sin(yaw)))
    width = _projected_size(axes, extent, (-math.sin(yaw), math.cos(yaw)))
    return ProjectedBox(center, heading, length, width)


def _projected_size(axes: tuple[Vector3, ...], extent: tuple[float, ...], axis: Axis2) -> float:
    return 2 * sum(
        abs(_dot2(direction, axis)) * half for direction, half in zip(axes, extent, strict=True)
    )
