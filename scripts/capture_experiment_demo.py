"""Capture two separate Jev demos on a dedicated asynchronous Town10HD instance.

Requires the matching CARLA API, the optional Jev dependency, and a credential in
the trusted environment. The session owns the camera and every simulator tick.
This in-process instrumentation is separate from the matched evaluation and does
not change planning or policy. Private traces stay in --state-dir. Camera PNGs and
manifests go into a new --output directory; no existing evidence is overwritten.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import sys
import uuid
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import patch

from carla_agentic_toolkit import managed_engine
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.managed_world import world_identity, world_settings
from carla_agentic_toolkit.merge_experiment import MergeExperiment
from carla_agentic_toolkit.sensor_subscription import SensorSubscription
from carla_agentic_toolkit.simulator_lease import private_state_root

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import (
        CarlaActor,
        CarlaClient,
        CarlaSensor,
        CarlaWorld,
    )
    from carla_agentic_toolkit.managed_session import ManagedSession
    from carla_agentic_toolkit.merge_planner import MergeObservation

WIDTH, HEIGHT = 960, 540
QUEUE_CAPACITY = 16
MAX_CAPTURE_FRAMES = 602
MAX_FRAME_BYTES = 4 * 1024 * 1024
CASES = (("normal-budget", 40), ("restricted-budget", 1))


class SensorImage(Protocol):
    """Retain native pixels for hashing independently of CARLA's PNG writer."""

    frame: int
    timestamp: float
    raw_data: bytes

    def save_to_disk(self, path: str) -> object:
        """Save a delivered CARLA frame."""


class CameraSensor(Protocol):
    """Stop reception before final draining; the managed session destroys actors."""

    def stop(self) -> None:
        """Stop the owned camera listener."""

    def listen(self, callback: Callable[[object], None]) -> None:
        """Deliver original camera data to the shared bounded subscription."""


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class FrameRecorder:
    """Bound callback memory, frame retention, and owner-thread disk work."""

    def __init__(self, folder: Path) -> None:
        """Create a new frame directory without overwriting previous captures."""
        self.folder = folder
        self.frames = folder / "frames"
        self.frames.mkdir(parents=True, exist_ok=False)
        self.subscription: SensorSubscription | None = None
        self.saved: list[dict[str, object]] = []
        self.errors: list[str] = []
        self._closed = False
        self._last_frame = 0

    def subscribe(self, sensor: CameraSensor) -> None:
        """Bind the already-owned camera to the same drop-oldest listener as other consumers."""
        if self.subscription is not None:
            message = "Camera recorder is already subscribed."
            raise ValueError(message)
        self.subscription = SensorSubscription(cast("CarlaSensor", sensor), capacity=QUEUE_CAPACITY)

    def save_arrived(self) -> None:
        """Drain at most one queue capacity on the owner thread, preserving errors."""
        if self._closed:
            return
        for _ in range(QUEUE_CAPACITY):
            image = self._next_image()
            if image is None:
                return
            self._save_checked(cast("SensorImage", image))

    def _next_image(self) -> object | None:
        if self.subscription is None:
            return None
        try:
            return self.subscription.next_frame(timeout_seconds=0.0)
        except CarlaAdapterError as error:
            if str(error) != "Timed out waiting for a sensor frame.":
                raise
            return None

    def _save_checked(self, image: SensorImage) -> None:
        try:
            self._save(image)
        except (OSError, RuntimeError, ValueError) as error:
            self.errors.append(f"save_frame:{type(error).__name__}")
            raise

    def _save(self, image: SensorImage) -> None:
        _validate_image(image)
        if len(self.saved) >= MAX_CAPTURE_FRAMES:
            message = "Camera frame retention limit exceeded."
            raise ValueError(message)
        path = self.frames / f"{image.frame}.png"
        if path.exists():
            message = "Duplicate camera frame."
            raise ValueError(message)
        digest = hashlib.sha256(image.raw_data).hexdigest()
        image.save_to_disk(str(path))
        if path.stat().st_size > MAX_FRAME_BYTES:
            message = "Camera frame exceeds its byte limit."
            raise ValueError(message)
        self.saved.append(
            {
                "frame": image.frame,
                "timestamp": image.timestamp,
                "sha256": digest,
            }
        )
        self._last_frame = max(self._last_frame, image.frame)

    def finish(self, sensor: CameraSensor) -> dict[str, Any]:
        """Write failure evidence even when camera stop or final draining fails."""
        # Save bounded arrivals before a failed Stop can freeze the shared queue.
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            self.save_arrived()
        stopped, trailing = self._stop(sensor)
        self._closed = True
        with contextlib.suppress(OSError, RuntimeError, ValueError):
            for image in trailing:
                self._save_checked(cast("SensorImage", image))
        manifest = self._manifest(stopped=stopped)
        _write_json(self.folder / "camera-manifest.json", manifest)
        if self.errors:
            message = "Camera capture did not finish cleanly; inspect camera-manifest.json."
            raise RuntimeError(message)
        return manifest

    def _stop(self, sensor: CameraSensor) -> tuple[bool, tuple[object, ...]]:
        try:
            if self.subscription is None:
                sensor.stop()
                return True, ()
            return True, self.subscription.close_and_drain(self._last_frame).frames
        except (OSError, RuntimeError, ValueError) as error:
            self.errors.append(f"stop_camera:{type(error).__name__}")
            return False, ()

    def _manifest(self, *, stopped: bool) -> dict[str, Any]:
        dropped, pending = self._delivery_counts()
        return {
            "schema_version": 1,
            "camera_type": "sensor.camera.rgb",
            "width": WIDTH,
            "height": HEIGHT,
            "fov": 80,
            "sensor_tick": 0.05,
            "view": "fixed birdseye, original CARLA renderer",
            "sha256_representation": "carla.Image.raw_data (32-bit BGRA)",
            "camera_stopped": stopped,
            "dropped_queue_frames": dropped,
            "pending_queue_frames": pending,
            "errors": self.errors,
            "ok": stopped and bool(self.saved) and not self.errors and dropped == 0,
            "images": self.saved,
        }

    def _delivery_counts(self) -> tuple[int, int]:
        if self.subscription is None:
            return 0, 0
        return self.subscription.dropped_samples, self.subscription.pending_samples


def _validate_image(image: SensorImage) -> None:
    if type(image.frame) is not int or image.frame < 0:
        message = "Camera frame identity must be a nonnegative integer."
        raise ValueError(message)
    if not math.isfinite(image.timestamp):
        message = "Camera timestamp must be finite."
        raise ValueError(message)


class CameraExperiment(MergeExperiment):
    """Add a session-owned camera to the unchanged reviewed merge fixture."""

    def __init__(self, session: ManagedSession, folder: Path) -> None:
        """Retain instrumentation outside the runtime package."""
        super().__init__(session)
        self.recorder = FrameRecorder(folder)

    def prepare(self) -> None:
        """Register the camera for normal session cleanup before subscribing."""
        super().prepare()
        blueprint = self.session.world.get_blueprint_library().find("sensor.camera.rgb")
        attributes = {
            "image_size_x": str(WIDTH),
            "image_size_y": str(HEIGHT),
            "fov": "80",
            "sensor_tick": "0.05",
            "role_name": f"managed:{self.session.run_id}:demo-camera",
        }
        for key, value in attributes.items():
            blueprint.set_attribute(key, value)
        camera = self.session.world.spawn_actor(blueprint, self._camera_transform())
        self.session.own(camera, controller="demo-camera", protected=True)
        self.session.on_close(lambda: self._close_camera(camera))
        self.recorder.subscribe(camera)

    def _close_camera(self, camera: CameraSensor) -> dict[str, object]:
        manifest = self.recorder.finish(camera)
        return {
            "demo_camera_images": len(manifest["images"]),
            "demo_camera_queue_drops": manifest["dropped_queue_frames"],
        }

    def _camera_transform(self) -> object:
        corridor = self.corridor
        if corridor is None:
            message = "The merge corridor must be prepared before camera placement."
            raise RuntimeError(message)
        carla = import_module("carla")
        yaw = math.radians(corridor.policy_start.yaw)
        along, lateral = 25.0, corridor.target_offset_m / 2
        location = carla.Location(
            x=corridor.policy_start.x + along * math.cos(yaw) - lateral * math.sin(yaw),
            y=corridor.policy_start.y + along * math.sin(yaw) + lateral * math.cos(yaw),
            z=corridor.policy_start.z + 38.0,
        )
        return carla.Transform(
            location, carla.Rotation(pitch=-90, yaw=corridor.policy_start.yaw - 90)
        )

    def observe(self, snapshot: object) -> MergeObservation:
        """Save arrivals without advancing the world or changing observations."""
        self.recorder.save_arrived()
        return super().observe(snapshot)

    def fixture_metadata(self) -> dict[str, object]:
        """Make the extra camera and capture implementation explicit in evidence."""
        return super().fixture_metadata() | {
            "demo_instrumentation": {
                "camera": True,
                "separate_from_matched_evaluation": True,
                "capture_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            }
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Require explicit live mutation consent and bounded numerical configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    options = parser.parse_args(argv)
    try:
        ExperimentSpec(host=options.host, port=options.port, policy="jev")
        _separate_paths(options.output, options.state_dir)
    except ValueError as error:
        parser.error(str(error))
    return options


def _separate_paths(output: Path, state: Path) -> None:
    output, state = output.expanduser().resolve(), state.expanduser().resolve()
    if output.is_relative_to(state) or state.is_relative_to(output):
        message = "Camera output and private state must be separate directory trees."
        raise ValueError(message)


def prepare_state(options: argparse.Namespace) -> Path:
    """Apply the same private-state allowlist and owner checks as managed clients."""
    name = "CARLA_AGENTIC_TOOLKIT_STATE_DIR"
    previous = os.environ.get(name)
    os.environ[name] = str(options.state_dir.expanduser().resolve())
    try:
        return private_state_root()
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def _snapshot(world: CarlaWorld) -> dict[str, object]:
    return {
        "world_id": world_identity(world),
        "actors": sorted(cast("CarlaActor", actor).id for actor in world.get_actors().filter("*")),
        "settings": world_settings(world),
    }


def _run_instrumented(spec: ExperimentSpec, state: Path, folder: Path) -> dict[str, object]:
    def factory(session: ManagedSession) -> CameraExperiment:
        return CameraExperiment(session, folder)

    with patch.object(managed_engine, "build_experiment", factory):
        return managed_engine.run_experiment(
            spec,
            uuid.uuid4().hex,
            state_root=state,
            cancelled=lambda: False,
            publish_status=lambda _value: None,
        )


def _restoration(client: CarlaClient, before: dict[str, object]) -> dict[str, object]:
    try:
        world = client.get_world()
        world.wait_for_tick(10.0)
        after = _snapshot(world)
    except (OSError, RuntimeError, ValueError) as error:
        return {
            "actors_restored": False,
            "settings_restored": False,
            "verification_error_type": type(error).__name__,
        }
    same_world = before.get("world_id") == after["world_id"]
    return {
        "actors_restored": same_world and before.get("actors") == after["actors"],
        "settings_restored": same_world and before.get("settings") == after["settings"],
    }


def _camera_report(folder: Path) -> dict[str, object]:
    path = folder / "camera-manifest.json"
    if path.exists():
        return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
    value = {"ok": False, "errors": ["camera_manifest_missing"], "images": []}
    _write_json(path, value)
    return value


def run_case(spec: ExperimentSpec, state: Path, folder: Path) -> dict[str, object]:
    """Retain each outcome and restoration check, including partial failures."""
    folder.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "case": folder.name,
        "spec": spec.model_dump(),
        "worker_returned": False,
        "worker_started": False,
        "result": {},
        "actors_restored": False,
        "settings_restored": False,
    }
    client = None
    before: dict[str, object] = {}
    try:
        client = managed_engine.connect_client(spec)
        before = _snapshot(client.get_world())
        _require_async(before)
        report["worker_started"] = True
        report["result"] = _run_instrumented(spec, state, folder)
        report["worker_returned"] = True
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001
        report["capture_error_type"] = type(error).__name__
    if client is not None and report["worker_started"]:
        report.update(_restoration(client, before))
    report["camera"] = _camera_report(folder)
    report["capture_ok"] = capture_ok(report)
    _write_json(folder / "result.json", report)
    return report


def _require_async(snapshot: dict[str, object]) -> None:
    settings = cast("dict[str, object]", snapshot["settings"])
    if settings.get("synchronous_mode") is not False:
        message = "The dedicated CARLA instance must initially be asynchronous."
        raise ValueError(message)


def capture_ok(report: dict[str, Any]) -> bool:
    """Keep capture/cleanup success distinct from the physical maneuver outcome."""
    required = (
        report.get("worker_returned"),
        report.get("result", {}).get("state") == "completed",
        report.get("actors_restored"),
        report.get("settings_restored"),
        report.get("camera", {}).get("ok"),
        report.get("result", {}).get("cleanup", {}).get("ok"),
    )
    return all(value is True for value in required)


def run_capture(options: argparse.Namespace) -> bool:
    """Run the fixed two-case plan once, stopping on any unverified capture or cleanup."""
    if not options.confirm_live:
        message = "--confirm-live is required before simulator access."
        raise ValueError(message)
    state = prepare_state(options)
    output = options.output.expanduser().resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    reports = []
    for case, budget in CASES:
        spec = ExperimentSpec(
            host=options.host, port=options.port, policy="jev", max_requests=budget
        )
        report = run_case(spec, state, output / case)
        reports.append(report)
        _write_json(output / "capture-results.json", reports)
        result = cast("dict[str, object]", report["result"])
        _emit({"case": case, "capture_ok": report["capture_ok"], "outcome": result.get("outcome")})
        if not report["capture_ok"]:
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    """Exit zero only for two verified captures, not for successful maneuver claims."""
    options = parse_args(argv)
    try:
        return 0 if run_capture(options) else 1
    except (OSError, RuntimeError, ValueError, ImportError) as error:
        _emit({"capture_ok": False, "error_type": type(error).__name__})
        return 1


def _emit(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, allow_nan=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
