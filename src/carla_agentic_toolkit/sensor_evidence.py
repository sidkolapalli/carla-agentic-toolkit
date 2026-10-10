"""Persist sensor evidence without conflating raw measurements and display copies."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import CaptureInfo
from carla_agentic_toolkit.output_content import OutputContentError, image_mime_type

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaImage

ENCODED_CAMERAS = frozenset(
    {
        "sensor.camera.depth",
        "sensor.camera.semantic_segmentation",
        "sensor.camera.instance_segmentation",
    }
)
COLOR_CONVERTERS = frozenset({"Raw", "Depth", "LogarithmicDepth", "CityScapesPalette"})


def require_publication_converter(*, publish: bool, name: str | None) -> None:
    """Keep display-only conversion explicitly coupled to publication opt-in."""
    if name is not None and not publish:
        message = "color_converter is display-only and requires publish=True."
        raise CarlaAdapterError(message)


def capture_converter(sensor_type: str, path: Path, name: str | None) -> object | None:
    """Validate local output and converter choices before installing a listener."""
    _require_raw_path(sensor_type, path)
    if name is None:
        return None
    _require_converter_output(sensor_type, path, name)
    try:
        return getattr(import_module("carla").ColorConverter, name)
    except (ImportError, AttributeError) as exc:
        message = f"CARLA color_converter {name!r} is unavailable."
        raise CarlaAdapterError(message) from exc


def _require_raw_path(sensor_type: str, path: Path) -> None:
    if sensor_type in ENCODED_CAMERAS and path.suffix.lower() != ".png":
        message = "Encoded depth and segmentation captures require a lossless PNG output."
        raise CarlaAdapterError(message)
    if sensor_type.startswith("sensor.lidar.") and path.suffix.lower() != ".ply":
        message = "LiDAR captures require a .ply output path."
        raise CarlaAdapterError(message)


def _require_converter_output(sensor_type: str, path: Path, name: str) -> None:
    if name not in COLOR_CONVERTERS:
        message = "color_converter must be Raw, Depth, LogarithmicDepth, or CityScapesPalette."
        raise CarlaAdapterError(message)
    if not sensor_type.startswith("sensor.camera.") or path.suffix.lower() != ".png":
        message = "color_converter requires a camera capture with a lossless PNG raw path."
        raise CarlaAdapterError(message)


def save_frame(frame: object, path: Path, converter: object | None = None) -> None:
    """Require a callable native writer and the durable file it acknowledges."""
    writer = getattr(frame, "save_to_disk", None)
    if not callable(writer):
        message = "CARLA sensor frame has no callable save_to_disk writer."
        raise CarlaAdapterError(message)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        acknowledged = _write_frame(cast("Callable[..., object]", writer), path, converter)
        if acknowledged is False:
            message = f"CARLA refused to save the sensor evidence file: {path}"
            raise CarlaAdapterError(message)
        if not path.is_file():
            message = f"CARLA did not write the requested sensor evidence file: {path}"
            raise CarlaAdapterError(message)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc


def _write_frame(writer: Callable[..., object], path: Path, converter: object | None) -> object:
    # Native writers require a nonempty parent even for a basename in the current directory.
    if converter is None:
        return writer(str(path.absolute()))
    return writer(str(path.absolute()), converter)


def capture_mime_type(path: Path) -> str:
    """Detect the saved bytes through the same PNG/JPEG signature check as publication."""
    try:
        with path.open("rb") as stream:
            signature = stream.read(8)
        if signature.startswith((b"ply\n", b"ply\r\n")):
            return "application/octet-stream"
        return image_mime_type(signature)
    except (OSError, OutputContentError) as exc:
        raise CarlaAdapterError(str(exc)) from exc


def save_capture(
    frame: CarlaImage, sensor_id: int, path: Path, converter: object | None
) -> CaptureInfo:
    """Save raw ground truth first and an optional separate converted PNG for publication."""
    save_frame(frame, path)
    mime_type = capture_mime_type(path)
    publication_path = _save_publication_copy(frame, path, converter)
    return CaptureInfo(
        capture_id=f"capture-{sensor_id:06d}",
        sensor_id=sensor_id,
        path=path,
        frame=int(frame.frame),
        mime_type=mime_type,
        publication_path=publication_path,
    )


def _save_publication_copy(frame: object, raw: Path, converter: object | None) -> Path | None:
    if converter is None:
        return None
    path = raw.with_name(f"{raw.stem}-display.png")
    save_frame(frame, path, converter)
    if capture_mime_type(path) != "image/png":
        message = "The converted publication copy must be a lossless PNG."
        raise CarlaAdapterError(message)
    return path
