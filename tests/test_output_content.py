"""Bounded publication of durable CARLA image captures."""

from __future__ import annotations

from pathlib import Path

import pytest

from carla_mcp.output_content import (
    OutputContentError,
    capture_resource_uri,
    published_captures,
    read_capture_resource,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"image"


def test_capture_is_published_with_image_data_and_resource_uri(tmp_path: Path) -> None:
    """A valid output capture should be embeddable and durably addressable."""
    capture = tmp_path / "captures" / "front.png"
    capture.parent.mkdir()
    capture.write_bytes(PNG_BYTES)

    images = published_captures(_snapshots("captures/front.png"), tmp_path, text_bytes=10)

    assert {
        "count": len(images),
        "mime_type": images[0].mime_type,
        "name": images[0].name,
        "size": images[0].size,
        "resource_scheme": images[0].resource_uri.startswith("carla-output://capture/"),
        "resource_bytes": read_capture_resource(images[0].resource_uri.rsplit("/", 1)[1], tmp_path),
    } == {
        "count": 1,
        "mime_type": "image/png",
        "name": "front.png",
        "size": len(PNG_BYTES),
        "resource_scheme": True,
        "resource_bytes": PNG_BYTES,
    }


def test_capture_path_cannot_escape_output_directory(tmp_path: Path) -> None:
    """A script result must not turn the MCP server into an arbitrary file reader."""
    secret = tmp_path.parent / "secret.png"
    secret.write_bytes(PNG_BYTES)

    with pytest.raises(OutputContentError, match="outside") as raised:
        published_captures(_snapshots("../secret.png"), tmp_path, text_bytes=0)

    assert raised.value.error_type == "image_path_rejected"


def test_capture_rejects_symlink_escape(tmp_path: Path) -> None:
    """A symlink under the output root must not expose a file outside it."""
    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)
    try:
        (output / "linked.png").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(OutputContentError) as raised:
        published_captures(_snapshots("linked.png"), output, text_bytes=0)

    assert raised.value.error_type == "image_path_rejected"


def test_capture_rejects_non_image_content(tmp_path: Path) -> None:
    """A .png suffix alone must not publish arbitrary output bytes as an image."""
    capture = tmp_path / "not-image.png"
    capture.write_text("secret text", encoding="utf-8")

    with pytest.raises(OutputContentError) as raised:
        published_captures(_snapshots("not-image.png"), tmp_path, text_bytes=0)

    assert raised.value.error_type == "unsupported_image"


def test_capture_enforces_combined_encoded_output_limit(tmp_path: Path) -> None:
    """Base64 image data must fit the complete MCP result budget."""
    capture = tmp_path / "large.png"
    capture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 400_000)

    with pytest.raises(OutputContentError) as raised:
        published_captures(_snapshots("large.png"), tmp_path, text_bytes=700_000)

    assert raised.value.error_type == "image_output_too_large"


def test_capture_count_is_bounded(tmp_path: Path) -> None:
    """One response should not embed an unbounded camera stream."""
    snapshots: dict[str, object] = {}
    for index in range(5):
        path = tmp_path / f"{index}.png"
        path.write_bytes(PNG_BYTES)
        snapshots[f"carla-snapshot://captures/{index}"] = {
            "path": path.name,
            "mime_type": "image/png",
            "publish": True,
        }

    with pytest.raises(OutputContentError) as raised:
        published_captures(snapshots, tmp_path, text_bytes=0)

    assert raised.value.error_type == "too_many_images"


def test_capture_resource_uri_round_trips_nested_relative_path(tmp_path: Path) -> None:
    """Resource tokens should not require a mutable in-memory registry."""
    capture = tmp_path / "nested" / "frame.jpg"
    capture.parent.mkdir()
    capture.write_bytes(b"\xff\xd8\xff" + b"jpeg")
    uri = capture_resource_uri(Path("nested/frame.jpg"))

    assert read_capture_resource(uri.rsplit("/", 1)[1], tmp_path) == capture.read_bytes()


def _snapshots(path: str) -> dict[str, object]:
    return {
        "carla-snapshot://captures/capture-1": {
            "capture_id": "capture-1",
            "path": path,
            "mime_type": "image/png",
            "publish": True,
        }
    }
