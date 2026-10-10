"""Image digests identify actual dimensions and unencoded BGRA measurements."""

from __future__ import annotations

import hashlib
import math
from types import SimpleNamespace
from typing import Any, cast

import pytest

from carla_agentic_toolkit import experiment_perception
from carla_agentic_toolkit.errors import CarlaAdapterError
from tests.ground_truth_helpers import make_case

RAW_BGRA = bytes((1, 2, 3, 255, 4, 5, 6, 255))
RAW_REPRESENTATION = "carla.Image.raw_data (32-bit BGRA)"
IMAGE_WIDTH = 2
IMAGE_FRAME = 41
CAMERA_FOV = 73.0


def _image() -> SimpleNamespace:
    return SimpleNamespace(frame=41, timestamp=2.05, width=2, height=1, raw_data=RAW_BGRA)


def test_image_digest_has_actual_dimensions_attribute_fov_and_raw_sha() -> None:
    """Image dimensions are delivered values, while FOV comes from real sensor context."""
    image = _image()
    digest = cast("Any", experiment_perception.sensor_frame_digest)(
        image, camera_attributes={"image_size_x": "800", "image_size_y": "600", "fov": "73"}
    )
    _assert_image_metadata(digest)


def _assert_image_metadata(digest: dict[str, object]) -> None:
    assert {
        "width": digest["width"],
        "height": digest["height"],
        "fov": digest["fov"],
        "sha256": digest["sha256"],
        "representation": digest["sha256_representation"],
    } == {
        "width": IMAGE_WIDTH,
        "height": 1,
        "fov": CAMERA_FOV,
        "sha256": hashlib.sha256(RAW_BGRA).hexdigest(),
        "representation": RAW_REPRESENTATION,
    }


def test_image_without_camera_context_reports_unknown_fov_not_default() -> None:
    """A standalone image is still hashable but cannot invent missing calibration context."""
    digest = experiment_perception.sensor_frame_digest(_image())
    assert digest["fov"] is None
    assert digest["sha256"] == hashlib.sha256(RAW_BGRA).hexdigest()


@pytest.mark.parametrize(("field", "value"), [("width", 0), ("height", True), ("width", math.nan)])
def test_invalid_image_dimensions_are_not_silently_serialized(field: str, value: object) -> None:
    """Wrong native dimensions cannot label an incomplete pixel buffer as valid evidence."""
    image = _image()
    setattr(image, field, value)
    with pytest.raises(CarlaAdapterError):
        experiment_perception.sensor_frame_digest(image)


@pytest.mark.parametrize("value", ["nan", "inf", "0", "180", "bad"])
def test_invalid_attribute_fov_is_a_digest_error(value: str) -> None:
    """Provided but invalid camera context is an error, not unknown or a default."""
    with pytest.raises(CarlaAdapterError):
        cast("Any", experiment_perception.sensor_frame_digest)(
            _image(), camera_attributes={"fov": value}
        )


@pytest.mark.parametrize("raw", [None, "not-bytes", bytes((1, 2, 3))])
def test_image_digest_requires_complete_raw_bgra(raw: object) -> None:
    """The raw representation label requires a complete four-byte-per-pixel payload."""
    image = _image()
    image.raw_data = raw
    with pytest.raises(CarlaAdapterError):
        experiment_perception.sensor_frame_digest(image)


def test_non_image_sensor_digest_remains_compatible() -> None:
    """CPU measurements retain their existing digest without image-only requirements."""
    sample = SimpleNamespace(frame=7, timestamp=0.35, latitude=42.0, longitude=3.0)
    assert experiment_perception.sensor_frame_digest(sample) == {
        "frame": 7,
        "timestamp": 0.35,
        "type": "SimpleNamespace",
        "actor_id": None,
        "other_actor_id": None,
        "transform": None,
    }


@pytest.mark.parametrize("sensor_type", ["sensor.camera.dvs", "sensor.camera.optical_flow"])
def test_non_bgra_camera_measurements_are_not_mislabelled_as_images(sensor_type: str) -> None:
    """DVS events and 64-bit optical-flow pixels retain their old non-image digests."""
    sample = _image()
    sample.raw_data = bytes(range(16))
    digest = cast("Any", experiment_perception.sensor_frame_digest)(
        sample, camera_attributes={"fov": "90"}, sensor_type=sensor_type
    )
    assert digest["frame"] == IMAGE_FRAME
    assert "sha256_representation" not in digest


@pytest.mark.parametrize("operation", ["stream", "drain"])
def test_public_image_digests_use_the_resolved_sensor_fov(operation: str) -> None:
    """Both stream and drain attach the resolved sensor's actual calibration context."""
    case = make_case()
    camera = case.world.actors[19]
    camera.samples = [_image()]
    camera.attributes["fov"] = "73"
    if operation == "stream":
        payload = case.api.read_sensor_stream(19, 1)
    else:
        case.api.subscribe_sensor(19)
        payload = case.api.drain_sensor(19, 41)
        case.api.close_sensor_subscription(19)
    digest = cast("list[dict[str, object]]", payload["frames"])[0]
    _assert_image_metadata(digest)
    assert {"listeners": camera.listen_calls, "stops": camera.stop_calls} == {
        "listeners": 1,
        "stops": 1,
    }


@pytest.mark.parametrize("operation", ["standalone", "stream", "drain"])
@pytest.mark.parametrize("buffer_kind", ["bytes", "bytearray", "readonly-view", "mutable-view"])
def test_native_style_byte_buffers_preserve_exact_bgra_hash(
    operation: str,
    buffer_kind: str,
) -> None:
    """Actual LibCarla memoryviews and other complete byte buffers are valid images."""
    image = _image()
    buffers = {
        "bytes": RAW_BGRA,
        "bytearray": bytearray(RAW_BGRA),
        "readonly-view": memoryview(RAW_BGRA),
        "mutable-view": memoryview(bytearray(RAW_BGRA)),
    }
    image.raw_data = buffers[buffer_kind]
    digest = _buffer_digest(image, operation)
    _assert_image_metadata(digest)


def _buffer_digest(image: SimpleNamespace, operation: str) -> dict[str, object]:
    if operation == "standalone":
        return experiment_perception.sensor_frame_digest(image, camera_attributes={"fov": "73"})
    case = make_case()
    camera = case.world.actors[19]
    camera.samples = [image]
    camera.attributes["fov"] = "73"
    if operation == "stream":
        payload = case.api.read_sensor_stream(19, 1)
    else:
        case.api.subscribe_sensor(19)
        payload = case.api.drain_sensor(19, IMAGE_FRAME)
        case.api.close_sensor_subscription(19)
    return cast("list[dict[str, object]]", payload["frames"])[0]


@pytest.mark.parametrize("buffer_kind", ["incomplete", "noncontiguous", "nonbyte", "released"])
def test_malformed_buffer_views_cannot_receive_raw_bgra_labels(buffer_kind: str) -> None:
    """Buffer protocol support must not weaken completeness or byte-layout validation."""
    released = memoryview(RAW_BGRA)
    released.release()
    image = _image()
    image.raw_data = {
        "incomplete": memoryview(RAW_BGRA[:-1]),
        "noncontiguous": memoryview(RAW_BGRA * 2)[::2],
        "nonbyte": memoryview(RAW_BGRA).cast("I"),
        "released": released,
    }[buffer_kind]
    with pytest.raises(CarlaAdapterError):
        experiment_perception.sensor_frame_digest(image)
