"""Demo readers verify the named hash bytes without reinterpreting historical receipts."""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import zlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from scripts import render_experiment_demo as merge
from scripts import render_route_demo as route

if TYPE_CHECKING:
    import io
    from pathlib import Path

RAW_REPRESENTATION = "carla.Image.raw_data (32-bit BGRA)"
RAW_BGRA = b"\x10\x20\x30\x7f"
RGBA = b"\x30\x20\x10\x7f"
LEGACY = object()
RUN_ID = "a" * 32
FRAME = 10


def _png() -> bytes:
    """Encode one lossless RGBA pixel with only the standard library."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b"\x00" + RGBA))
        + chunk(b"IEND", b"")
    )


def _inputs(tmp_path: Path, representation: object, digest: str) -> tuple[Path, Path, Path]:
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / f"{FRAME}.png").write_bytes(_png())
    projection = {
        "schema_version": 1,
        "run_id": RUN_ID,
        "trace_sha256": "b" * 64,
        "code_sha256": "c" * 64,
        "replicate_index": 7,
        "outcome": {},
        "cleanup": {"ok": True},
        "samples": [{"frame": FRAME, "simulation_seconds": 0.5, "decision": None}],
    }
    camera: dict[str, Any] = {"images": [{"frame": FRAME, "sha256": digest}]}
    if representation is not LEGACY:
        camera["sha256_representation"] = representation
    capture = {
        "result": {"run_id": RUN_ID, "trace_path": str(tmp_path / "trace.jsonl"), "outcome": {}},
        "spec": {"scenario": "lead_brake", "policy": "rules"},
        "capture_ok": True,
        "camera": camera,
    }
    projection_path = tmp_path / "projection.json"
    capture_path = tmp_path / "result.json"
    projection_path.write_text(json.dumps(projection), encoding="utf-8")
    capture_path.write_text(json.dumps(capture), encoding="utf-8")
    return projection_path, frames, capture_path


def _read(reader: str, paths: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch) -> bytes:
    if reader == "merge":
        case = merge.load_case(*paths)
        return merge.camera_bytes(case.joined[0])
    observation = {
        "simulation_seconds": 0.5,
        "speed_mps": 1.0,
        "route_progress_m": 2.0,
        "collision": False,
    }
    execution = {
        "hazard": {},
        "requested_choice": "cruise",
        "choice_source": "rules",
        "executed_choice": "cruise",
        "intervention": {},
        "controls": {"policy": {}},
    }
    events = tuple(
        {"run_id": RUN_ID, "frame": FRAME, "kind": kind, "data": data}
        for kind, data in (("observation", observation), ("execution", execution))
    )
    trace = paths[0].parent / "trace.jsonl"
    trace.write_bytes(b"trace bytes are independent of camera hashing")
    monkeypatch.setattr(
        route, "load_trace", lambda _path: SimpleNamespace(complete=True, errors=(), events=events)
    )
    monkeypatch.setattr(route, "summarize_trace", lambda _read: {"metrics": {}})
    case = route.project_case(paths[0].parent)
    sample = case["samples"][0]
    return merge.camera_bytes(
        merge.JoinedFrame(
            sample,
            paths[1] / f"{FRAME}.png",
            sample["sha256"],
            **(
                {"sha256_representation": sample["sha256_representation"]}
                if "sha256_representation" in sample
                else {}
            ),
        )
    )


def _mock_codec(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Mandatory tests check the codec contract even when optional Pillow is absent."""
    image = Mock(width=1, height=1)
    image.format = "PNG"
    image.__enter__ = Mock(return_value=image)
    image.__exit__ = Mock(return_value=False)
    image.convert.return_value = image
    image.tobytes.return_value = RAW_BGRA

    def open_image(stream: io.BytesIO) -> Mock:
        assert stream.getvalue() == _png()
        return image

    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=SimpleNamespace(open=open_image)))
    return image


@pytest.mark.parametrize("reader", ["merge", "route"])
def test_raw_bgra_receipt_uses_the_existing_lossless_codec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    """Raw hashes differ from PNG hashes and must survive both reader and replay checks."""
    paths = _inputs(tmp_path, RAW_REPRESENTATION, hashlib.sha256(RAW_BGRA).hexdigest())
    image = _mock_codec(monkeypatch)

    assert _read(reader, paths, monkeypatch) == _png()
    image.convert.assert_called_with("RGBA")
    image.tobytes.assert_called_with("raw", "BGRA")


@pytest.mark.parametrize("reader", ["merge", "route"])
def test_raw_bgra_receipt_verifies_actual_png_pixels_and_alpha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    """The optional real renderer codec validates channel order and the nonopaque alpha byte."""
    pytest.importorskip(
        "PIL.Image", reason="Renderer's pinned optional Pillow dependency is absent"
    )
    paths = _inputs(tmp_path, RAW_REPRESENTATION, hashlib.sha256(RAW_BGRA).hexdigest())

    assert _read(reader, paths, monkeypatch) == _png()


@pytest.mark.parametrize("reader", ["merge", "route"])
def test_historical_receipt_without_representation_verifies_original_png_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    """Old receipts retain their recorded PNG digest without requiring an image decoder."""
    paths = _inputs(tmp_path, LEGACY, hashlib.sha256(_png()).hexdigest())
    monkeypatch.setitem(sys.modules, "PIL", None)

    assert _read(reader, paths, monkeypatch) == _png()


@pytest.mark.parametrize("reader", ["merge", "route"])
def test_explicit_raw_representation_cannot_be_verified_with_a_png_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    """A known label must never downgrade to the legacy encoded-byte check."""
    paths = _inputs(tmp_path, RAW_REPRESENTATION, hashlib.sha256(_png()).hexdigest())
    _mock_codec(monkeypatch)

    with pytest.raises(ValueError, match=r"hash.*(match|changed)|bytes changed"):
        _read(reader, paths, monkeypatch)


@pytest.mark.parametrize("reader", ["merge", "route"])
@pytest.mark.parametrize("representation", [None, "RGB pixels", "encoded PNG"])
def test_unknown_explicit_hash_representation_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str, representation: object
) -> None:
    """Only absent legacy labels or the exact current raw-BGRA contract are accepted."""
    paths = _inputs(tmp_path, representation, hashlib.sha256(_png()).hexdigest())

    with pytest.raises(ValueError, match="Unsupported camera hash representation"):
        _read(reader, paths, monkeypatch)


@pytest.mark.parametrize("reader", ["merge", "route"])
def test_raw_hash_verification_requires_the_decoder_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: str
) -> None:
    """Unavailable optional decoding gives a clear error rather than a false provenance claim."""
    paths = _inputs(tmp_path, RAW_REPRESENTATION, hashlib.sha256(RAW_BGRA).hexdigest())
    monkeypatch.setitem(sys.modules, "PIL", None)

    with pytest.raises(ValueError, match="requires Pillow"):
        _read(reader, paths, monkeypatch)
