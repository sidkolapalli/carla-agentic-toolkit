"""Demo composition must never borrow a camera image from another observation frame."""

from __future__ import annotations

import hashlib
import io
import json
from typing import TYPE_CHECKING

import pytest

from scripts import render_experiment_demo as demo

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Provide three exact observation identities with only two recorded camera frames."""
    projection = tmp_path / "projection.json"
    projection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "a" * 32,
                "trace_sha256": "b" * 64,
                "code_sha256": "c" * 64,
                "seed": 7,
                "policy": "jev",
                "fixed_delta_seconds": 0.05,
                "outcome": {"completed": True, "status": "completed"},
                "cleanup": {"ok": True, "settings_restored": True, "world_replaced": False},
                "samples": [
                    {
                        "frame": frame,
                        "simulation_seconds": frame * 0.05,
                        "observation_sequence": frame,
                        "phase": "preparing",
                        "decision": None,
                        "execution": None,
                    }
                    for frame in (10, 11, 12)
                ],
            }
        )
    )
    frames = tmp_path / "frames"
    frames.mkdir()
    for frame in (10, 12):
        (frames / f"{frame}.png").write_bytes(b"image decoding is outside this identity test")
    capture = tmp_path / "capture.json"
    capture.write_text(
        json.dumps(
            {
                "result": {"run_id": "a" * 32},
                "camera": {
                    "images": [
                        {
                            "frame": frame,
                            "sha256": hashlib.sha256(
                                (frames / f"{frame}.png").read_bytes()
                            ).hexdigest(),
                        }
                        for frame in (10, 12)
                    ]
                },
            }
        )
    )
    return projection, frames, capture


def test_exact_join_counts_gaps_without_borrowing_images(inputs: tuple[Path, Path, Path]) -> None:
    """Only identical observation/image frame numbers may enter the playback schedule."""
    case = demo.load_case(*inputs)
    assert ([item.sample["frame"] for item in case.joined], case.missing_frames) == (
        [10, 12],
        (11,),
    )
    assert [item.image.name for item in case.joined] == ["10.png", "12.png"]


def test_duplicate_numeric_camera_identity_is_rejected(inputs: tuple[Path, Path, Path]) -> None:
    """Leading-zero aliases cannot silently replace a different camera file."""
    (inputs[1] / "0010.png").write_bytes(b"ambiguous")
    with pytest.raises(ValueError, match="Duplicate camera frame"):
        demo.load_case(*inputs)


@pytest.mark.parametrize("change", ["duplicate", "future_frame", "future_sequence"])
def test_invalid_projection_temporal_identity_is_rejected(
    inputs: tuple[Path, Path, Path], change: str
) -> None:
    """The renderer refuses repeated observations or decisions received after an observation."""
    value = json.loads(inputs[0].read_text())
    updates = {
        "duplicate": {"frame": 10},
        "future_frame": {"decision": {"frame": 13, "sequence": 1}},
        "future_sequence": {"decision": {"frame": 10, "sequence": 13}},
    }
    value["samples"][1].update(updates[change])
    inputs[0].write_text(json.dumps(value))
    with pytest.raises(ValueError, match="identity"):
        demo.load_case(*inputs)


def test_no_matching_images_cannot_produce_a_demo(inputs: tuple[Path, Path, Path]) -> None:
    """A camera-only or trace-only run must not become an invented visual replay."""
    for image in inputs[1].glob("*.png"):
        image.unlink()
    with pytest.raises(ValueError, match="No exact camera"):
        demo.load_case(*inputs)


def test_same_frames_from_another_run_are_rejected(inputs: tuple[Path, Path, Path]) -> None:
    """Frame numbers may recur in another world; only the matching capture run is authoritative."""
    value = json.loads(inputs[2].read_text())
    value["result"]["run_id"] = "d" * 32
    inputs[2].write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Capture run identity"):
        demo.load_case(*inputs)


def test_changed_camera_bytes_are_rejected(inputs: tuple[Path, Path, Path]) -> None:
    """A filename alone cannot bind changed pixels to a recorded observation."""
    (inputs[1] / "10.png").write_bytes(b"substituted image")
    with pytest.raises(ValueError, match="Camera image hash"):
        demo.load_case(*inputs)


def test_duplicate_capture_records_are_rejected(inputs: tuple[Path, Path, Path]) -> None:
    """Even duplicate identical records are ambiguous capture provenance."""
    value = json.loads(inputs[2].read_text())
    value["camera"]["images"].append(value["camera"]["images"][0])
    inputs[2].write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Duplicate capture frame"):
        demo.load_case(*inputs)


def test_unlisted_camera_image_is_rejected(inputs: tuple[Path, Path, Path]) -> None:
    """All supplied PNGs need capture provenance, even when unused by this observation set."""
    (inputs[1] / "99.png").write_bytes(b"foreign camera frame")
    with pytest.raises(ValueError, match="Camera frame is absent"):
        demo.load_case(*inputs)


def test_projection_and_capture_reads_are_bounded(
    inputs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both JSON files enforce the byte limit while reading, before JSON allocation."""
    blobs = {path: path.read_bytes() for path in (inputs[0], inputs[2], *inputs[1].glob("*.png"))}
    seen: list[Path] = []

    class LimitedReader(io.BytesIO):
        """Refuse the original unbounded read pattern."""

        def read(self, size: int | None = -1, /) -> bytes:
            assert size == demo.PROJECTION_LIMIT + 1
            return super().read(size)

    def open_input(path: Path, mode: str = "r") -> io.BytesIO:
        assert mode == "rb"
        if path.suffix == ".json":
            seen.append(path)
            return LimitedReader(blobs[path])
        return io.BytesIO(blobs[path])

    monkeypatch.setattr(type(inputs[0]), "open", open_input)
    demo.load_case(*inputs)
    assert set(seen) == {inputs[0], inputs[2]}
