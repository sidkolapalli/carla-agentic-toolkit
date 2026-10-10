"""New demo labels accept historical evidence without changing its pinned bytes."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, cast

import pytest

from scripts import project_demo_trace as projection
from scripts import render_experiment_demo as renderer
from scripts.render_experiment_demo import _case_labels
from tests import test_render_experiment_demo as renderer_fixtures
from tests.test_project_demo_trace import _events, _write

if TYPE_CHECKING:
    from pathlib import Path

rendering_inputs = renderer_fixtures.inputs


@pytest.mark.parametrize(
    "labels",
    [
        {"seed": 7},
        {"replicate_index": 7},
        {"seed": 7, "replicate_index": 7},
    ],
)
def test_projection_exports_canonical_replicate_without_modifying_trace(
    tmp_path: Path, labels: dict[str, object]
) -> None:
    """The legacy name is an input alias, not an exported random-seed claim."""
    events = _events()
    events[0]["data"].pop("seed")
    events[0]["data"].update(labels)
    path = tmp_path / "events.jsonl"
    digest = _write(path, events)
    original = path.read_bytes()
    result = projection.project_trace(path, digest)
    assert (result["replicate_index"], "seed" in result) == (7, False)
    assert (result["trace_sha256"], path.read_bytes()) == (digest, original)


@pytest.mark.parametrize(
    "labels",
    [
        {},
        {"seed": 7, "replicate_index": 8},
        {"seed": True, "replicate_index": 1},
        {"seed": False, "replicate_index": 0},
        {"seed": -1},
        {"seed": 2**31},
        {"replicate_index": "7"},
    ],
)
def test_projection_rejects_unknown_invalid_or_conflicting_replicate(
    tmp_path: Path, labels: dict[str, object]
) -> None:
    """Publication cannot infer defaults or accept bool/int alias equality."""
    events = _events()
    events[0]["data"].pop("seed")
    events[0]["data"].update(labels)
    path = tmp_path / "events.jsonl"
    digest = _write(path, events)
    with pytest.raises(ValueError, match=r"replicate_index|seed"):
        projection.project_trace(path, digest)


@pytest.mark.parametrize(
    "labels",
    [
        {"seed": 7},
        {"replicate_index": 7},
        {"seed": 7, "replicate_index": 7},
    ],
)
def test_renderer_labels_old_and_new_projection_as_replicate(
    rendering_inputs: tuple[Path, Path, Path], labels: dict[str, object]
) -> None:
    """Reader normalization leaves projection, capture, and camera provenance intact."""
    path, _, capture = rendering_inputs
    value = json.loads(path.read_text())
    value.pop("seed")
    value.update(labels)
    path.write_text(json.dumps(value))
    original = path.read_bytes()
    case = renderer.load_case(*rendering_inputs)
    canvas = LabelCanvas()
    _case_labels(cast("renderer.Canvas", canvas), case, case.joined[0])
    assert "Policy jev / replicate 7" in canvas.labels
    assert (case.projection_sha256, path.read_bytes()) == (
        hashlib.sha256(original).hexdigest(),
        original,
    )
    assert case.capture_sha256 == hashlib.sha256(capture.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "labels",
    [
        {},
        {"seed": 7, "replicate_index": 8},
        {"seed": True, "replicate_index": 1},
        {"seed": False, "replicate_index": 0},
        {"seed": -1},
        {"seed": 2**31},
        {"replicate_index": "7"},
    ],
)
def test_renderer_rejects_invalid_or_conflicting_replicate_labels(
    rendering_inputs: tuple[Path, Path, Path], labels: dict[str, object]
) -> None:
    """A rendered label must be a known strict replicate, not a guessed default."""
    value = json.loads(rendering_inputs[0].read_text())
    value.pop("seed")
    value.update(labels)
    rendering_inputs[0].write_text(json.dumps(value))
    with pytest.raises(ValueError, match=r"replicate_index|seed"):
        renderer.load_case(*rendering_inputs)


class LabelCanvas:
    """Capture text without importing the optional Pillow dependency."""

    def __init__(self) -> None:
        """Start a presentation text capture."""
        self.labels: list[str] = []

    def text(self, _position: tuple[int, int], content: str, *_args: object) -> None:
        """Retain exactly the renderer's supplied label."""
        self.labels.append(content)
