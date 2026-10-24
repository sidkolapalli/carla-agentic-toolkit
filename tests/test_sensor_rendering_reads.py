"""Camera reads reject unavailable rendering before installing native listeners."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter as adapter_module
from carla_agentic_toolkit import experiment_scene
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import CameraAttachRequest, Location, Rotation, Transform

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaClient

SENSOR_ID = 7
ORIGIN_WORLD = 17
CAMERA = "sensor.camera.rgb"
READ_OPERATIONS = ("subscribe", "stream", "capture")
INVALID_RENDERING = (True, None, 0, 1, "False")
CPU_SENSORS = (
    "sensor.other.gnss",
    "sensor.other.collision",
    "sensor.lidar.ray_cast",
    "sensor.other.radar",
)


class ReadingSensor:
    """Emit one valid sample immediately so a missing guard fails without timeout."""

    id = SENSOR_ID

    def __init__(self, sensor_type: str) -> None:
        """Track native registration separately from a server actor's identity."""
        self.type_id = sensor_type
        self.attributes: dict[str, str] = {}
        self.listens = 0
        self.stops = 0
        self.listening = False
        self.sample = SimpleNamespace(frame=1, timestamp=0.05)
        if sensor_type.startswith(("sensor.camera.", "sensor.lidar.")):
            payload = (
                b"ply\nformat ascii 1.0\nend_header\n"
                if sensor_type.startswith("sensor.lidar.")
                else b"\x89PNG\r\n\x1a\nimage"
            )
            self.sample.save_to_disk = Mock(
                side_effect=lambda path: Path(path).write_bytes(payload)
            )

    def listen(self, callback: Callable[[object], None]) -> None:
        """Deliver a camera-like sample only after recording native Listen."""
        self.listens += 1
        self.listening = True
        callback(self.sample)

    def is_listening(self) -> bool:
        """Expose the native method, not a truthy method object."""
        return self.listening

    def stop(self) -> None:
        """Acknowledge release of this original registration."""
        self.stops += 1
        self.listening = False


@dataclass
class ReadingWorld:
    """Allow rendering reads to fail or replace the client's current episode."""

    sensor: ReadingSensor
    id: int = ORIGIN_WORLD
    settings: object = field(
        default_factory=lambda: SimpleNamespace(synchronous_mode=False, no_rendering_mode=False)
    )
    settings_error: RuntimeError | None = None
    during_settings: Callable[[], None] | None = None
    settings_reads: int = 0

    def get_actors(self, _ids: object = None) -> ReadingWorld:
        """Resolve explicit IDs without any simulator tick or creation."""
        return self

    def find(self, actor_id: int) -> ReadingSensor | None:
        """Return the existing sensor without claiming toolkit ownership."""
        return self.sensor if actor_id == self.sensor.id else None

    def get_settings(self) -> object:
        """Expose only the settings read boundary exercised by these regressions."""
        self.settings_reads += 1
        if self.during_settings is not None:
            self.during_settings()
        if self.settings_error is not None:
            raise self.settings_error
        return self.settings


@dataclass
class ReadingClient:
    """Keep the current world link explicit rather than relying on Mock IDs."""

    world: ReadingWorld

    def get_world(self) -> ReadingWorld:
        """Return the currently observed native episode."""
        return self.world


@dataclass
class ReadingCase:
    """Retain the original handle and world alongside the real adapter."""

    adapter: PythonCarlaAdapter
    client: ReadingClient
    origin: ReadingWorld
    sensor: ReadingSensor


@dataclass
class ReplacingRenderingSettings:
    """Replace only during the new rendering property read, not timing preflight."""

    replace: Callable[[], None]
    synchronous_mode: bool = False

    @property
    def no_rendering_mode(self) -> bool:
        """Model a renderer-mode RPC that completes after an episode replacement."""
        self.replace()
        return False


def _case(monkeypatch: pytest.MonkeyPatch, sensor_type: str = CAMERA) -> ReadingCase:
    sensor = ReadingSensor(sensor_type)
    world = ReadingWorld(sensor)
    client = ReadingClient(world)
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", lambda: cast("CarlaClient", client))
    return ReadingCase(adapter, client, world, sensor)


def _read(case: ReadingCase, operation: str, path: Path) -> object:
    readers: dict[str, Callable[[], object]] = {
        "subscribe": lambda: case.adapter.subscribe_sensor(SENSOR_ID),
        "stream": lambda: case.adapter.read_sensor_stream(
            sensor_id=SENSOR_ID, frame_count=1, output_dir=path.parent
        ),
        "capture": lambda: case.adapter.capture_sensor_frame(sensor_id=SENSOR_ID, output_path=path),
    }
    return readers[operation]()


def _assert_no_listener(case: ReadingCase, path: Path) -> None:
    assert case.sensor.listens == 0
    cast("Mock", case.sensor.sample.save_to_disk).assert_not_called()
    assert not path.parent.exists()


def _attach_camera(case: ReadingCase, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "_configured_blueprint", Mock())
    monkeypatch.setattr(adapter_module, "_parent_actor", Mock(return_value=None))
    monkeypatch.setattr(adapter_module, "_spawn_sensor", Mock(return_value=case.sensor))
    case.adapter.attach_camera(
        request=CameraAttachRequest(
            CAMERA, Transform(Location(0, 0, 0), Rotation(0, 0, 0)), {}, None
        )
    )


@pytest.mark.parametrize("operation", READ_OPERATIONS)
@pytest.mark.parametrize("rendering", INVALID_RENDERING)
def test_camera_read_refuses_disabled_or_malformed_rendering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str, rendering: object
) -> None:
    """Only an explicit native False value authorizes GPU camera collection."""
    case = _case(monkeypatch)
    case.origin.settings = SimpleNamespace(synchronous_mode=False, no_rendering_mode=rendering)
    path = tmp_path / "captures" / "camera.png"

    with pytest.raises(CarlaAdapterError, match="no_rendering_mode"):
        _read(case, operation, path)

    _assert_no_listener(case, path)


@pytest.mark.parametrize("operation", READ_OPERATIONS)
@pytest.mark.parametrize("unavailable", ["missing", "failure", "none"])
def test_camera_read_refuses_unavailable_rendering_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str, unavailable: str
) -> None:
    """Unknown native rendering state produces an actionable refusal, not empty data."""
    case = _case(monkeypatch)
    states = {"missing": SimpleNamespace(synchronous_mode=False), "failure": None, "none": None}
    case.origin.settings = states[unavailable]
    if unavailable == "failure":
        case.origin.settings_error = RuntimeError("native settings unavailable")
    path = tmp_path / "captures" / "camera.png"

    with pytest.raises(CarlaAdapterError, match="no_rendering_mode"):
        _read(case, operation, path)

    _assert_no_listener(case, path)


@pytest.mark.parametrize("operation", READ_OPERATIONS)
def test_camera_read_rechecks_rendering_after_sensor_attachment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str
) -> None:
    """A cached creation handle does not preserve an old rendering authorization."""
    case = _case(monkeypatch)
    _attach_camera(case, monkeypatch)
    case.origin.settings = SimpleNamespace(synchronous_mode=False, no_rendering_mode=True)
    path = tmp_path / "captures" / "camera.png"

    with pytest.raises(CarlaAdapterError, match="no_rendering_mode"):
        _read(case, operation, path)

    _assert_no_listener(case, path)


@pytest.mark.parametrize("operation", READ_OPERATIONS)
def test_rendered_camera_read_control(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, operation: str
) -> None:
    """A rendered camera still delivers data through the actual listener lifetime."""
    case = _case(monkeypatch)

    _read(case, operation, tmp_path / "captures" / "camera.png")
    case.adapter.close_sensor_subscriptions()

    assert case.sensor.listens == 1
    assert case.sensor.stops == 1


@pytest.mark.parametrize("sensor_type", CPU_SENSORS)
@pytest.mark.parametrize("operation", READ_OPERATIONS)
def test_cpu_sensor_read_is_available_without_rendering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sensor_type: str, operation: str
) -> None:
    """Camera-only restrictions do not block CPU sensors in an asynchronous world."""
    case = _case(monkeypatch, sensor_type)
    case.origin.settings = SimpleNamespace(synchronous_mode=False, no_rendering_mode=True)

    path = tmp_path / "captures" / "sample.png"
    if sensor_type.startswith("sensor.lidar."):
        path = path.with_suffix(".ply")
    if operation == "capture" and not hasattr(case.sensor.sample, "save_to_disk"):
        with pytest.raises(CarlaAdapterError, match="expected image API"):
            _read(case, operation, path)
    else:
        _read(case, operation, path)
    case.adapter.close_sensor_subscriptions()

    assert case.sensor.listens == 1


@pytest.mark.parametrize("sensor_type", CPU_SENSORS)
def test_cpu_subscription_does_not_read_rendering_settings(
    monkeypatch: pytest.MonkeyPatch, sensor_type: str
) -> None:
    """Even an unavailable settings RPC is irrelevant to non-camera subscriptions."""
    case = _case(monkeypatch, sensor_type)
    case.origin.settings_error = RuntimeError("settings should not be read")

    case.adapter.subscribe_sensor(SENSOR_ID)
    case.adapter.close_sensor_subscriptions()

    assert case.origin.settings_reads == 0


def test_camera_buffer_can_drain_and_close_after_rendering_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existing delivery and stop acknowledgements remain available for cleanup."""
    case = _case(monkeypatch)
    case.adapter.subscribe_sensor(SENSOR_ID)
    case.origin.settings = SimpleNamespace(synchronous_mode=False, no_rendering_mode=True)

    report = case.adapter.drain_sensor(SENSOR_ID, 1)
    case.adapter.close_sensor_subscription(SENSOR_ID)
    case.adapter.close_sensor_subscription(SENSOR_ID)

    assert report["timed_out"] is False
    assert case.sensor.stops == 1


def _retained_subscription(case: ReadingCase) -> None:
    case.adapter.subscribe_sensor(SENSOR_ID)
    case.adapter.close_sensor_subscription(SENSOR_ID)


def test_retained_subscription_rechecks_episode_after_rendering_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settings RPC cannot authorize listening to an original handle in a new episode."""
    case = _case(monkeypatch)
    _retained_subscription(case)
    replacement = ReadingWorld(ReadingSensor(CAMERA), id=ORIGIN_WORLD + 1)
    case.origin.during_settings = lambda: setattr(case.client, "world", replacement)

    with pytest.raises(CarlaAdapterError, match="episode"):
        case.adapter.subscribe_sensor(SENSOR_ID)

    assert case.sensor.listens == 1
    assert replacement.sensor.listens == 0


def test_retained_subscription_rejects_already_replaced_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The existing inherited-handle origin guard remains ahead of rendering lookup."""
    case = _case(monkeypatch)
    _retained_subscription(case)
    reads_before = case.origin.settings_reads
    case.client.world = ReadingWorld(ReadingSensor(CAMERA), id=ORIGIN_WORLD + 1)

    with pytest.raises(CarlaAdapterError, match="episode"):
        case.adapter.subscribe_sensor(SENSOR_ID)

    assert case.origin.settings_reads == reads_before
    assert case.sensor.listens == 1


def test_camera_stream_rechecks_episode_after_rendering_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The added rendering read cannot authorize an old handle after replacement."""
    case = _case(monkeypatch)
    replacement = ReadingWorld(ReadingSensor(CAMERA), id=ORIGIN_WORLD + 1)
    case.origin.settings = ReplacingRenderingSettings(
        lambda: setattr(case.client, "world", replacement)
    )
    path = tmp_path / "captures" / "camera.png"

    with pytest.raises(CarlaAdapterError, match="episode"):
        _read(case, "stream", path)

    _assert_no_listener(case, path)
    assert replacement.sensor.listens == 0


def _screenshot_spies(case: ReadingCase, monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, Mock]:
    spectator = Mock(return_value=Transform(Location(0, 0, 0), Rotation(0, 0, 0)))
    attach = Mock(return_value=SimpleNamespace(sensor_id=SENSOR_ID))
    monkeypatch.setattr(experiment_scene, "spectator_transform", spectator)
    monkeypatch.setattr(case.adapter, "attach_sensor", attach)
    monkeypatch.setattr(case.adapter, "capture_sensor_frame", Mock())
    monkeypatch.setattr(case.adapter, "detach_sensor", Mock())
    return spectator, attach


@pytest.mark.parametrize("rendering", INVALID_RENDERING)
def test_screenshot_refuses_rendering_before_spectator_or_camera_creation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rendering: object
) -> None:
    """RGB screenshot preflight cannot inspect the spectator or begin a native spawn."""
    case = _case(monkeypatch)
    case.origin.settings = SimpleNamespace(synchronous_mode=False, no_rendering_mode=rendering)
    spectator, attach = _screenshot_spies(case, monkeypatch)

    with pytest.raises(CarlaAdapterError, match="no_rendering_mode"):
        case.adapter.save_screenshot(output_path=tmp_path / "camera.png", attributes={})

    spectator.assert_not_called()
    attach.assert_not_called()
    cast("Mock", case.adapter.capture_sensor_frame).assert_not_called()


@pytest.mark.parametrize("unavailable", ["missing", "failure"])
def test_screenshot_refuses_unavailable_settings_before_spectator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, unavailable: str
) -> None:
    """An unreadable rendering mode is not permission for temporary RGB creation."""
    case = _case(monkeypatch)
    case.origin.settings = SimpleNamespace(synchronous_mode=False)
    if unavailable == "failure":
        case.origin.settings_error = RuntimeError("native settings unavailable")
    spectator, attach = _screenshot_spies(case, monkeypatch)

    with pytest.raises(CarlaAdapterError, match="no_rendering_mode"):
        case.adapter.save_screenshot(output_path=tmp_path / "camera.png", attributes={})

    spectator.assert_not_called()
    attach.assert_not_called()


def test_screenshot_rechecks_episode_after_rendering_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Early rendering inspection must not pass an old spectator into a replacement."""
    case = _case(monkeypatch)
    replacement = ReadingWorld(ReadingSensor(CAMERA), id=ORIGIN_WORLD + 1)
    case.origin.during_settings = lambda: setattr(case.client, "world", replacement)
    spectator, attach = _screenshot_spies(case, monkeypatch)

    with pytest.raises(CarlaAdapterError, match="episode"):
        case.adapter.save_screenshot(output_path=tmp_path / "camera.png", attributes={})

    spectator.assert_not_called()
    attach.assert_not_called()
