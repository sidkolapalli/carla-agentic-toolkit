"""Capture one reviewed route trial with a session-owned CARLA camera and exact frame IDs.

Uses the same restoration checks and bounded recorder as the merge capture tool.
The camera observes the unchanged route backend; it never advances a frame.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from carla_agentic_toolkit import managed_engine
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.route_experiment import RouteExperiment
from scripts import capture_experiment_demo as capture

if TYPE_CHECKING:
    from carla_agentic_toolkit.managed_session import ManagedSession
    from carla_agentic_toolkit.route_models import RouteObservation

CAMERA_INTERVAL_SECONDS = 0.2
SIMULATION_STEP_SECONDS = 0.05
MAX_ROUTE_STEPS = 2400


class RouteRecorder(capture.FrameRecorder):
    """Retain original rendered pixels and frame hashes with accurate route camera metadata."""

    def _manifest(self, *, stopped: bool) -> dict[str, Any]:
        return super()._manifest(stopped=stopped) | {
            "sensor_tick": CAMERA_INTERVAL_SECONDS,
            "view": "vehicle-attached elevated chase camera, original CARLA renderer",
        }


class CameraRoute(RouteExperiment):
    """Instrument the real route controller without changing its decisions or observation data."""

    def __init__(self, session: ManagedSession, folder: Path) -> None:
        """Create bounded storage before spawning any camera."""
        super().__init__(session)
        self.recorder = RouteRecorder(folder)

    def prepare(self) -> None:
        """Own the camera before subscribing, so failures follow normal session cleanup."""
        super().prepare()
        carla = import_module("carla")
        blueprint = self.session.world.get_blueprint_library().find("sensor.camera.rgb")
        attributes = {
            "image_size_x": str(capture.WIDTH),
            "image_size_y": str(capture.HEIGHT),
            "fov": "80",
            "sensor_tick": str(CAMERA_INTERVAL_SECONDS),
            "role_name": f"managed:{self.session.run_id}:route-camera",
        }
        for key, value in attributes.items():
            blueprint.set_attribute(key, value)
        transform = carla.Transform(carla.Location(x=-8.0, z=16.0), carla.Rotation(pitch=-50.0))
        camera = self.session.world.spawn_actor(
            blueprint, transform, attach_to=self.actors.handles["policy"]
        )
        self.session.own(camera, controller="route-demo-camera", protected=True)
        self.session.on_close(lambda: self._close_camera(camera))
        self.recorder.subscribe(camera)

    def _close_camera(self, camera: capture.CameraSensor) -> dict[str, object]:
        manifest = self.recorder.finish(camera)
        return {
            "demo_camera_images": len(manifest["images"]),
            "demo_camera_queue_drops": manifest["dropped_queue_frames"],
        }

    def observe(self, snapshot: object) -> RouteObservation:
        """Save only delivered images without waiting or modifying the owner clock."""
        self.recorder.save_arrived()
        return super().observe(snapshot)

    def fixture_metadata(self) -> dict[str, object]:
        """Mark camera instrumentation so evidence cannot be mistaken for an uninstrumented run."""
        return super().fixture_metadata() | {
            "demo_instrumentation": {
                "camera": True,
                "sensor_tick": CAMERA_INTERVAL_SECONDS,
                "capture_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
        }


def instrumented(spec: ExperimentSpec, state: Path, folder: Path) -> dict[str, object]:
    """Reuse the trusted experiment owner, adding only the owned camera factory."""

    def factory(session: ManagedSession) -> CameraRoute:
        return CameraRoute(session, folder)

    with patch.object(managed_engine, "build_experiment", factory):
        return managed_engine.run_experiment(
            spec,
            uuid.uuid4().hex,
            state_root=state,
            cancelled=lambda: False,
            publish_status=lambda _value: None,
        )


def main(argv: list[str] | None = None) -> int:
    """Require a reviewed route spec, explicit live access, and new output storage."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", required=True, action="store_true")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args(argv)
    spec = ExperimentSpec.model_validate_json(options.spec.read_text(encoding="utf-8"))
    if spec.fixture != "town10-route-ue5-v1" or spec.max_steps > MAX_ROUTE_STEPS:
        parser.error("Capture requires the route fixture with at most 2400 frames.")
    if spec.fixed_delta_seconds != SIMULATION_STEP_SECONDS:
        parser.error("Route camera capture requires 0.05s simulation steps.")
    state = capture.prepare_state(options)
    capture._separate_paths(options.output, state)  # noqa: SLF001 - shared capture harness
    with patch.object(capture, "_run_instrumented", instrumented):
        report = capture.run_case(spec, state, options.output)
    sys.stdout.write(
        json.dumps(
            {
                "capture_ok": report["capture_ok"],
                "result": report["result"],
                "output": str(options.output),
            }
        )
        + "\n"
    )
    return 0 if report["capture_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
