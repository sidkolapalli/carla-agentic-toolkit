"""Native-like camera deliveries for trusted capture and queue contract tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

from scripts import capture_experiment_demo as capture

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

    from carla_agentic_toolkit.managed_session import ManagedSession


@dataclass
class Image:
    """A sensor delivery with independent raw pixels and a deterministic disk payload."""

    frame: int
    timestamp: float = 0.05
    raw_data: bytes = b"\x10\x20\x30\xff"

    def save_to_disk(self, path: str) -> None:
        """Persist fake encoded bytes without depending on the native CARLA package."""
        Path(path).write_bytes(b"camera-frame")


@dataclass
class Camera:
    """A native-like listener with controllable trailing deliveries and Stop errors."""

    callback: Callable[[object], None] | None = None
    stopping_image: Image | None = None
    stop_error: RuntimeError | None = None
    stops: int = 0

    def listen(self, callback: Callable[[object], None]) -> None:
        """Retain the actual callback installed by the camera capture path."""
        self.callback = callback

    def emit(self, image: Image) -> None:
        """Deliver one original image without polling, ticking, or networking."""
        assert self.callback is not None
        self.callback(image)

    def stop(self) -> None:
        """Allow an in-flight native callback to complete during Stop."""
        self.stops += 1
        if self.stopping_image is not None:
            self.emit(self.stopping_image)
        if self.stop_error is not None:
            raise self.stop_error


def camera_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[capture.CameraExperiment, Camera]:
    """Exercise the real camera preparation while replacing unrelated merge setup."""
    camera = Camera()
    session = Mock()
    session.spec = capture.ExperimentSpec()
    session.run_id = "capture-test"
    session.world.spawn_actor.return_value = camera
    experiment = capture.CameraExperiment(cast("ManagedSession", session), tmp_path)
    monkeypatch.setattr(capture.MergeExperiment, "prepare", lambda _experiment: None)
    monkeypatch.setattr(capture.CameraExperiment, "_camera_transform", lambda _experiment: object())
    experiment.prepare()
    session.own.assert_called_once_with(camera, controller="demo-camera", protected=True)
    session.on_close.assert_called_once()
    return experiment, camera


def recorder(tmp_path: Path) -> tuple[capture.FrameRecorder, Camera]:
    """Bind the recorder's real shared subscription to a deterministic offline camera."""
    frame_recorder = capture.FrameRecorder(tmp_path)
    camera = Camera()
    frame_recorder.subscribe(camera)
    return frame_recorder, camera
