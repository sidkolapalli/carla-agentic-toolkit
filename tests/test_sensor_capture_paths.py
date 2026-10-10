"""Sensor output paths preserve their destination across CARLA native persistence."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.experiment_perception import save_sensor_frames
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api

PNG_BYTES = b"\x89PNG\r\n\x1a\nsensor-frame"


class NativeImage:
    """Model CARLA's native writer rejecting an empty parent directory."""

    frame = 42

    def save_to_disk(self, path: str) -> None:
        """Reproduce the live basename failure before writing a known payload."""
        if not os.path.dirname(path):  # noqa: PTH120 -- pathlib hides the native empty-parent bug.
            message = 'filesystem error: in create_directories: No such file or directory [""]'
            raise RuntimeError(message)
        Path(path).write_bytes(PNG_BYTES)


@pytest.mark.parametrize("path_kind", ["basename", "nested", "absolute"])
def test_capture_persists_requested_output_and_publication_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path_kind: str
) -> None:
    """Basenames in the output working directory are valid published captures."""
    monkeypatch.chdir(tmp_path)
    output_path = {
        "basename": Path("front.png"),
        "nested": Path("captures/front.png"),
        "absolute": tmp_path / "absolute/front.png",
    }[path_kind]
    world = Mock(id=17)
    world.get_settings.return_value.synchronous_mode = False
    world.get_settings.return_value.no_rendering_mode = False
    sensor = Mock(id=7, type_id="sensor.camera.rgb")
    sensor.listen.side_effect = lambda callback: callback(NativeImage())
    adapter = PythonCarlaAdapter()
    client = Mock()
    client.get_world.return_value = world
    monkeypatch.setattr(adapter, "_client", Mock(return_value=client))
    monkeypatch.setattr(adapter, "_world", Mock(return_value=world))
    monkeypatch.setattr(adapter, "_sensor_actor", Mock(return_value=sensor))
    snapshots = RunSnapshots()

    result = build_api(adapter, snapshots).capture_sensor_frame(7, str(output_path), publish=True)

    assert result == {
        "capture_id": "capture-000007",
        "sensor_id": 7,
        "path": str(output_path),
        "frame": NativeImage.frame,
        "mime_type": "image/png",
        "publish": True,
    }
    assert output_path.read_bytes() == PNG_BYTES
    assert snapshots.read_snapshot("carla-snapshot://captures/capture-000007") == result
    sensor.stop.assert_called_once_with()


@pytest.mark.parametrize("directory", [".", "captures"])
def test_stream_capture_accepts_the_output_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, directory: str
) -> None:
    """The stream writer supports the same current-directory destination as single captures."""
    monkeypatch.chdir(tmp_path)

    paths = save_sensor_frames([NativeImage()], 7, Path(directory))

    assert paths == [Path(directory) / "sensor-7-42.png"]
    assert paths[0].read_bytes() == PNG_BYTES
