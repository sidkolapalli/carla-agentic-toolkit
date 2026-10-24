"""Offline failure and resource-bound checks for the opt-in camera recorder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit.sensor_subscription import SensorSubscription
from scripts import capture_experiment_demo as capture
from scripts.capture_experiment_demo import _run_instrumented
from tests.capture_helpers import Camera, Image
from tests.capture_helpers import camera_case as _camera_case
from tests.capture_helpers import recorder as _recorder

if TYPE_CHECKING:
    from typing import Any

    from carla_agentic_toolkit.carla_protocols import CarlaSensor


def _assert_saved_range(manifest: dict[str, Any], overflow: int) -> None:
    assert [image["frame"] for image in manifest["images"]] == list(
        range(overflow, capture.QUEUE_CAPACITY + overflow)
    )


def _assert_overflow_evidence(manifest: dict[str, Any], overflow: int) -> None:
    assert manifest["dropped_queue_frames"] == overflow
    assert manifest["pending_queue_frames"] == 0
    assert manifest["ok"] is False


def test_capture_installs_the_actual_shared_subscription(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The demo must reuse shared listener storage, not only imitate its drop policy."""
    _experiment, camera = _camera_case(tmp_path, monkeypatch)

    assert isinstance(getattr(camera.callback, "__self__", None), SensorSubscription)


def test_capture_overflow_keeps_newest_frames_with_shared_drop_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Overflow discards the oldest queued images while retaining the bounded newest set."""
    experiment, camera = _camera_case(tmp_path, monkeypatch)
    overflow = 3
    for frame in range(capture.QUEUE_CAPACITY + overflow):
        camera.emit(Image(frame))

    manifest = experiment.recorder.finish(camera)

    _assert_saved_range(manifest, overflow)
    _assert_overflow_evidence(manifest, overflow)


def test_capture_hashes_raw_bgra_without_reading_the_encoded_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In-memory native pixels, not the different PNG payload, determine the frame digest."""
    experiment, camera = _camera_case(tmp_path, monkeypatch)
    image = Image(1)
    camera.emit(image)
    read_bytes = Mock(side_effect=AssertionError("encoded PNG must not be read for hashing"))
    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    manifest = experiment.recorder.finish(camera)

    assert manifest["images"][0]["sha256"] == hashlib.sha256(image.raw_data).hexdigest()
    read_bytes.assert_not_called()
    assert manifest["sha256_representation"] == "carla.Image.raw_data (32-bit BGRA)"


def test_capture_preserves_final_stop_delivery_and_rejects_late_callbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing the queue must retain the listener cutoff and trailing-frame evidence."""
    experiment, camera = _camera_case(tmp_path, monkeypatch)
    camera.emit(Image(1))
    camera.stopping_image = Image(2)

    manifest = experiment.recorder.finish(camera)
    camera.emit(Image(3))

    assert [image["frame"] for image in manifest["images"]] == [1, 2]
    assert camera.stops == 1
    assert not (tmp_path / "frames/3.png").exists()


def test_capture_failed_stop_preserves_arrived_frames_and_drop_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed listener cutoff cannot erase already-arrived images or claim cleanup success."""
    experiment, camera = _camera_case(tmp_path, monkeypatch)
    overflow = 3
    for frame in range(capture.QUEUE_CAPACITY + overflow):
        camera.emit(Image(frame))
    camera.stop_error = RuntimeError("disconnected")

    with pytest.raises(RuntimeError, match="Camera capture"):
        experiment.recorder.finish(camera)

    manifest = json.loads((tmp_path / "camera-manifest.json").read_text(encoding="utf-8"))
    _assert_saved_range(manifest, overflow)
    _assert_overflow_evidence(manifest, overflow)
    assert manifest["camera_stopped"] is False
    assert manifest["errors"] == ["stop_camera:CarlaAdapterError"]


def test_shared_subscription_counters_remain_readable_after_queue_cutoff() -> None:
    """Trusted capture evidence reads the same locked counters even when close freezes data."""
    camera = Camera()
    subscription = SensorSubscription(cast("CarlaSensor", camera), capacity=2)
    for frame in range(4):
        camera.emit(Image(frame))

    assert (subscription.dropped_samples, subscription.pending_samples) == (2, 2)
    subscription.close()
    camera.emit(Image(5))
    assert (subscription.dropped_samples, subscription.pending_samples) == (2, 0)


def _arguments(tmp_path: Path) -> list[str]:
    return ["--output", str(tmp_path / "output"), "--state-dir", str(tmp_path / "state")]


def test_requires_confirmation_before_any_carla_import(tmp_path: Path) -> None:
    """The mutation opt-in cannot be omitted even with valid paths."""
    with pytest.raises(SystemExit):
        capture.parse_args(_arguments(tmp_path))
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("port", ["0", "65534", "-1"])
def test_rejects_rpc_ports_without_streaming_headroom(tmp_path: Path, port: str) -> None:
    """Numerical configuration fails before simulator access."""
    with pytest.raises(SystemExit):
        capture.parse_args(["--confirm-live", "--port", port, *_arguments(tmp_path)])


def test_private_state_cannot_be_published_below_camera_output(tmp_path: Path) -> None:
    """A publishable capture tree must never contain provider traces or budgets."""
    args = ["--confirm-live", "--output", str(tmp_path), "--state-dir", str(tmp_path / "state")]
    with pytest.raises(SystemExit):
        capture.parse_args(args)


def test_bounded_queue_reports_drops_and_keeps_frame_hashes(tmp_path: Path) -> None:
    """The callback never grows a backlog beyond its fixed capacity."""
    recorder, camera = _recorder(tmp_path)
    overflow = 3
    for frame in range(capture.QUEUE_CAPACITY + overflow):
        camera.emit(Image(frame))
    manifest = recorder.finish(camera)
    assert (manifest["dropped_queue_frames"], manifest["ok"], len(manifest["images"])) == (
        overflow,
        False,
        capture.QUEUE_CAPACITY,
    )
    assert manifest["images"][0]["sha256"] == hashlib.sha256(Image(0).raw_data).hexdigest()


def test_close_rejects_callbacks_after_sensor_stop(tmp_path: Path) -> None:
    """A late callback cannot enqueue or create a file after final draining."""
    recorder, camera = _recorder(tmp_path)
    camera.emit(Image(1))
    recorder.finish(camera)
    camera.emit(Image(2))
    recorder.save_arrived()
    assert not (tmp_path / "frames/2.png").exists()


def test_duplicate_frame_never_overwrites_previous_capture(tmp_path: Path) -> None:
    """Duplicate sensor identities invalidate the capture and preserve original bytes."""
    recorder, camera = _recorder(tmp_path)
    camera.emit(Image(1))
    recorder.save_arrived()
    original = (tmp_path / "frames/1.png").read_bytes()
    camera.emit(Image(1))
    with pytest.raises(RuntimeError, match="Camera capture"):
        recorder.finish(camera)
    manifest = json.loads((tmp_path / "camera-manifest.json").read_text())
    assert manifest["ok"] is False
    assert manifest["errors"]
    assert (tmp_path / "frames/1.png").read_bytes() == original


def test_failed_stop_is_not_reported_as_verified_capture(tmp_path: Path) -> None:
    """Stop failures still produce an explicit manifest and propagate to session cleanup."""
    recorder, camera = _recorder(tmp_path)
    camera.emit(Image(4))
    camera.stop_error = RuntimeError("disconnected")
    with pytest.raises(RuntimeError, match="Camera capture"):
        recorder.finish(camera)
    manifest = json.loads((tmp_path / "camera-manifest.json").read_text())
    assert manifest["camera_stopped"] is False
    assert manifest["ok"] is False
    assert len(manifest["images"]) == 1


def test_capture_count_is_bounded_on_repeated_drains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A small queue alone cannot bound total retained files; the run has a separate cap."""
    monkeypatch.setattr(capture, "MAX_CAPTURE_FRAMES", 1)
    recorder, camera = _recorder(tmp_path)
    camera.emit(Image(1))
    recorder.save_arrived()
    camera.emit(Image(2))
    with pytest.raises(RuntimeError, match="Camera capture"):
        recorder.finish(camera)
    assert not (tmp_path / "frames/2.png").exists()


def test_missing_camera_or_cleanup_cannot_pass_capture_report() -> None:
    """A completed maneuver is insufficient without camera and restoration evidence."""
    report = {
        "result": {"state": "completed", "cleanup": {"ok": True}},
        "actors_restored": True,
        "settings_restored": True,
        "camera": {"ok": True},
        "worker_returned": True,
    }
    assert capture.capture_ok(report)
    report["camera"] = {"ok": False}
    assert not capture.capture_ok(report)
    report["camera"] = {"ok": True}
    report["result"] = {"state": "completed", "outcome": {"completed": True}}
    assert not capture.capture_ok(report)


def test_infrastructure_failure_cannot_pass_with_successful_cleanup() -> None:
    """Restored actors and saved images cannot turn a failed worker into a completed capture."""
    report = {
        "result": {"state": "failed", "cleanup": {"ok": True}},
        "actors_restored": True,
        "settings_restored": True,
        "camera": {"ok": True},
        "worker_returned": True,
    }
    assert not capture.capture_ok(report)


def test_capture_failure_prevents_second_trial_and_preserves_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The helper retains a failed case and never silently retries or admits another owner."""
    options = capture.parse_args(["--confirm-live", *_arguments(tmp_path)])
    failed = {"case": "normal-budget", "capture_ok": False, "result": {"state": "failed"}}
    runner = Mock(return_value=failed)
    monkeypatch.setattr(capture, "run_case", runner)
    monkeypatch.setattr(capture, "prepare_state", lambda _options: tmp_path / "state")
    assert capture.run_capture(options) is False
    assert runner.call_count == 1
    assert json.loads((tmp_path / "output/capture-results.json").read_text()) == [failed]


def test_initially_synchronous_world_is_rejected_before_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The camera helper cannot depend on somebody else ticking an already paused world."""
    monkeypatch.setattr(capture.managed_engine, "connect_client", Mock())
    before = {"world_id": 1, "actors": [], "settings": {"synchronous_mode": True}}
    monkeypatch.setattr(capture, "_snapshot", lambda _world: before)
    runner = Mock(return_value={"cleanup": {"ok": True}})
    monkeypatch.setattr(capture, "_run_instrumented", runner)
    report = capture.run_case(capture.ExperimentSpec(policy="jev"), tmp_path, tmp_path / "case")
    assert not runner.called
    assert report["capture_ok"] is False
    assert report["capture_error_type"] == "ValueError"


def test_instrumented_factory_is_restored_after_worker_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing demo must not leave the module configured with a camera factory."""
    original = capture.managed_engine.build_experiment
    monkeypatch.setattr(capture.managed_engine, "run_experiment", Mock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError):
        _run_instrumented(capture.ExperimentSpec(policy="jev"), tmp_path, tmp_path)
    assert capture.managed_engine.build_experiment is original
