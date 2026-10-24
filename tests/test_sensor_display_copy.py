"""Native color conversion applies only to an explicitly published display copy."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from carla_agentic_toolkit.output_content import published_captures
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api
from tests.test_sensor_evidence_formats import (
    CONVERTED_BYTES,
    PNG_BYTES,
    Sensor,
    WritableFrame,
    sensor_adapter,
)


@pytest.mark.parametrize(
    ("sensor_type", "converter"),
    [
        ("sensor.camera.depth", "Depth"),
        ("sensor.camera.depth", "LogarithmicDepth"),
        ("sensor.camera.semantic_segmentation", "CityScapesPalette"),
    ],
)
def test_converter_only_changes_the_published_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sensor_type: str, converter: str
) -> None:
    """The raw capture remains ground truth while MCP receives the converted PNG."""
    native_converter = object()
    monkeypatch.setitem(
        sys.modules,
        "carla",
        SimpleNamespace(ColorConverter=SimpleNamespace(**{converter: native_converter})),
    )
    measurement = WritableFrame()
    adapter = sensor_adapter(monkeypatch, Sensor(sensor_type, measurement))
    raw = tmp_path / "raw.png"

    report = build_api(adapter, RunSnapshots()).capture_sensor_frame(
        7, str(raw), publish=True, color_converter=converter
    )
    publication_path = Path(str(report["publication_path"]))
    published = published_captures(
        {"carla-snapshot://captures/capture-000007": report}, tmp_path, text_bytes=0
    )

    _assert_raw_ground_truth(report, raw)
    _assert_display_file(publication_path, raw)
    assert measurement.writes == [
        (raw.absolute(), None),
        (publication_path.absolute(), native_converter),
    ]
    assert base64.b64decode(published[0].data) == CONVERTED_BYTES


def _assert_raw_ground_truth(report: dict[str, object], raw: Path) -> None:
    assert raw.read_bytes() == PNG_BYTES
    assert report["path"] == str(raw)


def _assert_display_file(publication_path: Path, raw: Path) -> None:
    assert publication_path != raw
    assert publication_path.suffix == ".png"
    assert publication_path.read_bytes() == CONVERTED_BYTES


@pytest.mark.parametrize("converter", ["unknown", "depth", "", "CityscapesPalette"])
def test_invalid_converter_is_rejected_before_listen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, converter: str
) -> None:
    """Local converter errors cannot create a sensor listener or write evidence."""
    sensor = Sensor("sensor.camera.depth", WritableFrame())
    api = build_api(sensor_adapter(monkeypatch, sensor), RunSnapshots())

    report = api.capture_sensor_frame(
        7, str(tmp_path / "raw.png"), publish=True, color_converter=converter
    )

    assert report["ok"] is False
    assert "color_converter" in str(report["error"])
    assert sensor.listens == 0
    assert list(tmp_path.iterdir()) == []


def test_conversion_without_publication_is_rejected_before_listen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A display-only converter must not silently replace raw-only evidence."""
    sensor = Sensor("sensor.camera.depth", WritableFrame())
    api = build_api(sensor_adapter(monkeypatch, sensor), RunSnapshots())

    report = api.capture_sensor_frame(7, str(tmp_path / "raw.png"), color_converter="Depth")

    assert report["ok"] is False
    assert "publish=True" in str(report["error"])
    assert sensor.listens == 0
    assert list(tmp_path.iterdir()) == []
