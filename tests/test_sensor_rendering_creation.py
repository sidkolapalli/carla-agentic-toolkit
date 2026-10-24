"""Camera rendering refusal occurs before native creation, durable intent, or Listen."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter_objects
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from carla_agentic_toolkit.tool_inputs import parse_transform, zero_transform

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaBlueprint, CarlaClient, CarlaWorld
    from carla_agentic_toolkit.models import JsonObject

WORLD_ID = 17
SENSOR_ID = 7
CREATED_ID = 101
MISSING_MODE = object()
CAMERA_TYPES = (
    "sensor.camera.rgb",
    "sensor.camera.depth",
    "sensor.camera.semantic_segmentation",
    "sensor.camera.instance_segmentation",
    "sensor.camera.dvs",
    "sensor.camera.optical_flow",
    "sensor.camera.normals",
    "sensor.camera.cosmos_visualization",
)
CPU_TYPES = (
    "sensor.other.gnss",
    "sensor.other.collision",
    "sensor.other.imu",
    "sensor.lidar.ray_cast",
    "sensor.lidar.ray_cast_semantic",
    "sensor.other.radar",
)
CREATION_OPERATIONS = ("attach_camera", "attach_sensor", "batch")


@dataclass
class RenderingSettings:
    """Only reading the rendering flag may replace the native episode."""

    rendering_mode: object = False
    synchronous_mode: bool = False
    rendering_reads: int = 0
    on_rendering_read: Callable[[], None] = field(default=lambda: None, repr=False)

    @property
    def no_rendering_mode(self) -> bool:
        """Read the rendering flag independently from the existing timing preflight."""
        self.rendering_reads += 1
        self.on_rendering_read()
        if self.rendering_mode is MISSING_MODE:
            message = "no_rendering_mode is unavailable"
            raise AttributeError(message)
        if isinstance(self.rendering_mode, Exception):
            raise self.rendering_mode
        return cast("bool", self.rendering_mode)


@dataclass
class RenderingSensor:
    """A truthful sensor type exposes native listener side effects separately."""

    id: int
    type_id: str
    listen_calls: int = 0
    stop_calls: int = 0
    is_listening: bool = False
    save: Mock = field(default_factory=Mock)

    def listen(self, callback: Callable[[object], None]) -> None:
        """Deliver a native-like image without waiting for a real simulator."""
        self.listen_calls += 1
        self.is_listening = True
        callback(SimpleNamespace(frame=10, save_to_disk=self.save))

    def stop(self) -> None:
        """Record acknowledgement separately from creation and frame delivery."""
        self.stop_calls += 1
        self.is_listening = False


@dataclass
class RenderingWorld:
    """Blueprint lookup preserves the requested native sensor ID, never a vehicle placeholder."""

    settings: RenderingSettings
    id: int = WORLD_ID
    existing: RenderingSensor = field(
        default_factory=lambda: RenderingSensor(SENSOR_ID, "sensor.camera.rgb")
    )
    created: list[RenderingSensor] = field(default_factory=list)
    spawns: Mock = field(default_factory=Mock)
    settings_error: Exception | None = None
    settings_calls: int = 0

    def get_settings(self) -> RenderingSettings:
        """Record settings RPCs independently of individual flag reads."""
        self.settings_calls += 1
        if self.settings_error is not None:
            raise self.settings_error
        return self.settings

    def get_blueprint_library(self) -> SimpleNamespace:
        """Keep requested sensor blueprint IDs intact at the native boundary."""
        return SimpleNamespace(find=lambda blueprint_id: Mock(id=blueprint_id))

    def spawn_actor(self, blueprint: CarlaBlueprint, *_args: object) -> RenderingSensor:
        """Expose a known raw ID only after counting the actual native mutation."""
        self.spawns(blueprint.id)
        sensor = RenderingSensor(CREATED_ID + len(self.created), blueprint.id)
        self.created.append(sensor)
        return sensor

    def get_actors(self, _ids: object = None) -> SimpleNamespace:
        """Resolve only the inherited sensor used by capture tests."""
        return SimpleNamespace(
            find=lambda actor_id: self.existing if actor_id == SENSOR_ID else None
        )


@dataclass
class RenderingClient:
    """Keep an independently replaceable current-world pointer."""

    world: RenderingWorld

    def get_world(self) -> RenderingWorld:
        """Return the current episode rather than the caller's retained world."""
        return self.world

    def set_timeout(self, _seconds: float) -> None:
        """Support the actual retained-client timeout refresh without native I/O."""


@dataclass
class RenderingCase:
    """Exercise the real native adapter, facade, and durable ownership journal."""

    world: RenderingWorld
    client: RenderingClient
    adapter: PythonCarlaAdapter
    api: CarlaScriptApi
    ownership: RunOwnership
    journal_path: Path
    begin_creation: Mock


def _rendering_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mode: object
) -> RenderingCase:
    world = RenderingWorld(RenderingSettings(mode))
    client = RenderingClient(world)
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    monkeypatch.setattr(adapter_objects, "carla_transform", lambda value: value)
    journal_path = tmp_path / "owned-actors.json"
    ownership = RunOwnership(journal_path)
    begin_creation = Mock(wraps=ownership.begin_creation)
    monkeypatch.setattr(ownership, "begin_creation", begin_creation)
    api = CarlaScriptApi(adapter, RunSnapshots(), ownership=ownership)
    return RenderingCase(world, client, adapter, api, ownership, journal_path, begin_creation)


def _request(blueprint_id: str) -> JsonObject:
    return {"blueprint_id": blueprint_id, "transform": zero_transform(), "attributes": {}}


def _create(case: RenderingCase, operation: str, blueprint_id: str) -> JsonObject:
    calls = {
        "attach_camera": lambda: case.api.attach_camera(_request(blueprint_id)),
        "attach_sensor": lambda: case.api.attach_sensor(blueprint_id, None, zero_transform()),
        "batch": lambda: case.api.spawn_actor_batch([_request(blueprint_id)]),
    }
    return calls[operation]()


def _assert_no_creation(case: RenderingCase) -> None:
    case.world.spawns.assert_not_called()
    case.begin_creation.assert_not_called()
    fresh = RunOwnership(case.journal_path)
    assert fresh.actor_ids() == ()
    assert fresh.pending_creations() == 0
    assert case.world.existing.listen_calls == 0


def _assert_refused(result: JsonObject) -> None:
    assert result["ok"] is False
    failures = cast("list[JsonObject]", result.get("results", [result]))
    assert "no_rendering_mode" in str(failures[0].get("error", failures[0].get("message")))


@pytest.mark.parametrize("operation", CREATION_OPERATIONS)
@pytest.mark.parametrize("blueprint_id", CAMERA_TYPES)
def test_no_rendering_camera_creation_refuses_before_native_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, blueprint_id: str
) -> None:
    """Every known camera family fails through real creation paths without stranding intent."""
    case = _rendering_case(tmp_path, monkeypatch, mode=True)

    result = _create(case, operation, blueprint_id)

    _assert_refused(result)
    _assert_no_creation(case)


@pytest.mark.parametrize("blueprint_id", CAMERA_TYPES)
def test_direct_native_sensor_guard_precedes_creation_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blueprint_id: str
) -> None:
    """A high-level attachment check cannot be the only barrier around raw sensor creation."""
    case = _rendering_case(tmp_path, monkeypatch, mode=True)
    blueprint = cast("CarlaBlueprint", case.world.get_blueprint_library().find(blueprint_id))

    with pytest.raises(CarlaAdapterError, match="no_rendering_mode"):
        adapter_objects._spawn_sensor(  # noqa: SLF001
            cast("CarlaWorld", case.world),
            blueprint,
            parse_transform(zero_transform()),
            None,
            **case.adapter._creation_options(),  # noqa: SLF001
        )

    _assert_no_creation(case)


@pytest.mark.parametrize("operation", CREATION_OPERATIONS)
@pytest.mark.parametrize(
    "mode", [MISSING_MODE, None, 0, 1, "False", RuntimeError("rendering read failed")]
)
def test_unknown_camera_rendering_mode_is_not_coerced_into_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, mode: object
) -> None:
    """Unknown or unreadable native settings cannot authorize a camera spawn."""
    case = _rendering_case(tmp_path, monkeypatch, mode=mode)

    result = _create(case, operation, "sensor.camera.rgb")

    _assert_refused(result)
    _assert_no_creation(case)


@pytest.mark.parametrize("operation", CREATION_OPERATIONS)
def test_failed_settings_rpc_refuses_camera_before_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """A rendering preflight RPC failure must be actionable and non-mutating."""
    case = _rendering_case(tmp_path, monkeypatch, mode=False)
    case.world.settings_error = RuntimeError("settings unavailable")

    result = _create(case, operation, "sensor.camera.rgb")

    _assert_refused(result)
    _assert_no_creation(case)


@pytest.mark.parametrize("operation", CREATION_OPERATIONS)
@pytest.mark.parametrize("blueprint_id", CAMERA_TYPES)
def test_rendering_enabled_camera_creation_keeps_normal_journaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, blueprint_id: str
) -> None:
    """The guard neither disables rendering nor changes successful per-ID ownership."""
    case = _rendering_case(tmp_path, monkeypatch, mode=False)

    result = _create(case, operation, blueprint_id)

    assert result.get("ok") is not False
    _assert_created(case, blueprint_id)


def _assert_created(case: RenderingCase, blueprint_id: str) -> None:
    case.world.spawns.assert_called_once_with(blueprint_id)
    case.begin_creation.assert_called_once()
    fresh = RunOwnership(case.journal_path)
    assert fresh.actor_ids() == (CREATED_ID,)
    assert fresh.pending_creations() == 0


@pytest.mark.parametrize("operation", CREATION_OPERATIONS)
@pytest.mark.parametrize("blueprint_id", CPU_TYPES)
def test_cpu_sensor_creation_does_not_require_rendering_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, blueprint_id: str
) -> None:
    """GNSS/events and CPU ray-cast lidar/radar remain valid in a no-rendering world."""
    case = _rendering_case(tmp_path, monkeypatch, mode=True)
    case.world.settings_error = RuntimeError("CPU sensors must not inspect rendering settings")

    result = _create(case, operation, blueprint_id)

    assert result.get("ok") is not False
    _assert_created(case, blueprint_id)
    assert case.world.settings_calls == 0


def test_attach_refuses_episode_replacement_during_rendering_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new settings read cannot carry an old-world spawn into the replacement episode."""
    case = _rendering_case(tmp_path, monkeypatch, mode=False)
    replacement = _replace_on_rendering_read(case)

    result = case.api.attach_camera(_request("sensor.camera.rgb"))

    _assert_episode_refusal(result)
    _assert_replacement_untouched(case, replacement)


def _replace_on_rendering_read(case: RenderingCase) -> RenderingWorld:
    replacement = RenderingWorld(RenderingSettings(rendering_mode=False), id=WORLD_ID + 1)
    case.world.settings.on_rendering_read = partial(setattr, case.client, "world", replacement)
    return replacement


@pytest.mark.parametrize(("operation", "read_count"), [("batch", 1), ("attach_camera", 2)])
def test_raw_camera_rendering_read_refuses_replacement_before_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    read_count: int,
) -> None:
    """Both the generic read and the second attachment read guard before durable intent."""
    case = _rendering_case(tmp_path, monkeypatch, mode=False)
    replacement = RenderingWorld(RenderingSettings(rendering_mode=False), id=WORLD_ID + 1)
    case.world.settings.on_rendering_read = partial(
        _replace_at_rendering_read, case, replacement, read_count
    )

    result = _create(case, operation, "sensor.camera.rgb")

    assert result["ok"] is False
    _assert_replacement_untouched(case, replacement)


def _replace_at_rendering_read(
    case: RenderingCase, replacement: RenderingWorld, read_count: int
) -> None:
    if case.world.settings.rendering_reads == read_count:
        case.client.world = replacement


def _assert_episode_refusal(result: JsonObject) -> None:
    assert result["ok"] is False
    assert "episode" in str(result["message"]).lower()


def _assert_replacement_untouched(case: RenderingCase, replacement: RenderingWorld) -> None:
    assert case.client.world is replacement
    _assert_no_creation(case)
    replacement.spawns.assert_not_called()
    assert replacement.existing.listen_calls == 0


def test_capture_refuses_no_rendering_before_native_listen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing camera cannot evade refusal merely because creation happened earlier."""
    case = _rendering_case(tmp_path, monkeypatch, mode=True)

    result = case.api.capture_sensor_frame(SENSOR_ID, str(tmp_path / "capture.png"))

    _assert_refused(result)
    _assert_no_creation(case)
    assert case.world.existing.stop_calls == 0


def test_capture_refuses_episode_replacement_during_rendering_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacement occurs only on the new rendering flag read, not the existing timing check."""
    case = _rendering_case(tmp_path, monkeypatch, mode=False)
    replacement = _replace_on_rendering_read(case)

    result = case.api.capture_sensor_frame(SENSOR_ID, str(tmp_path / "capture.png"))

    _assert_episode_refusal(result)
    _assert_replacement_untouched(case, replacement)
    assert case.world.existing.stop_calls == 0


def test_rendering_enabled_capture_preserves_listener_lifetime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid cameras still listen once, save the image, and stop after collection."""
    case = _rendering_case(tmp_path, monkeypatch, mode=False)
    output_path = tmp_path / "capture.png"
    case.world.existing.save.side_effect = lambda _path: output_path.write_bytes(
        b"\x89PNG\r\n\x1a\nimage"
    )

    result = case.api.capture_sensor_frame(SENSOR_ID, str(output_path))

    assert result.get("ok") is not False
    _assert_valid_capture(case)
    _assert_no_spawn_intent(case)


def _assert_valid_capture(case: RenderingCase) -> None:
    assert case.world.existing.listen_calls == 1
    assert case.world.existing.stop_calls == 1
    case.world.existing.save.assert_called_once()


def _assert_no_spawn_intent(case: RenderingCase) -> None:
    case.world.spawns.assert_not_called()
    case.begin_creation.assert_not_called()
    assert RunOwnership(case.journal_path).pending_creations() == 0
