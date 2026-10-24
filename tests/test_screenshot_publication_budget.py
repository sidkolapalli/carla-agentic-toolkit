"""A default screenshot fits publication even with uncompressed RGBA PNG bytes."""

from __future__ import annotations

import struct
import zlib
from typing import TYPE_CHECKING
from unittest.mock import Mock

from carla_agentic_toolkit.output_content import MAX_IMAGE_BYTES, published_captures
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api

if TYPE_CHECKING:
    from pathlib import Path


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload))
    )


def _uncompressed_rgba_png(width: int, height: int) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    rows = (b"\0" + bytes(width * 4)) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(rows, level=0))
        + _chunk(b"IEND", b"")
    )


def test_default_screenshot_fits_per_image_and_combined_publication(tmp_path: Path) -> None:
    """The default dimensions leave enough room for full RGBA bytes and PNG overhead."""

    def save(*, output_path: Path, attributes: dict[str, str]) -> dict[str, object]:
        width, height = int(attributes["image_size_x"]), int(attributes["image_size_y"])
        output_path.write_bytes(_uncompressed_rgba_png(width, height))
        return {"capture_id": "screenshot", "path": str(output_path), "mime_type": "image/png"}

    adapter = Mock()
    adapter.save_screenshot.side_effect = save
    report = build_api(adapter, RunSnapshots()).save_screenshot(
        str(tmp_path / "screenshot.png"), publish=True
    )

    captures = published_captures(
        {"carla-snapshot://captures/screenshot": report}, tmp_path, text_bytes=1024
    )

    assert len(captures) == 1
    assert captures[0].size <= MAX_IMAGE_BYTES
    assert adapter.save_screenshot.call_args.kwargs["attributes"] == {
        "image_size_x": "480",
        "image_size_y": "270",
    }


def test_custom_screenshot_dimensions_are_not_silently_reduced(tmp_path: Path) -> None:
    """Explicit caller dimensions remain independent of the bounded default."""
    adapter = Mock()
    adapter.save_screenshot.return_value = {"capture_id": "screenshot", "path": "view.png"}
    attributes = {"image_size_x": "1920", "image_size_y": "1080"}

    build_api(adapter, RunSnapshots()).save_screenshot(str(tmp_path / "view.png"), attributes)

    assert adapter.save_screenshot.call_args.kwargs["attributes"] == attributes
