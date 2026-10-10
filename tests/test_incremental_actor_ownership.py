"""Native creation persists returned IDs before another simulator operation can interrupt it."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter_objects, experiment_scene, experiment_walkers
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient

WORLD_ID = 17
FIRST_ACTOR_ID = 101


class SpawnInterrupted(BaseException):
    """Model termination inside one call, bypassing ordinary CARLA error recovery."""


@dataclass
class CreatedActor:
    """Expose creation ID before optional setup operations interrupt execution."""

    id: int
    interrupt_stage: str = ""
    destroyed: bool = False

    def set_autopilot(self, _enabled: object, _port: int) -> None:
        """Pause after vehicle creation but before traffic configuration completes."""
        self._interrupt("autopilot")

    def start(self) -> None:
        """Pause before starting a newly created walker controller."""
        self._interrupt("start")

    def set_max_speed(self, _speed: float) -> None:
        """Accept walker setup without touching simulator state."""

    def go_to_location(self, _location: object) -> None:
        """Accept walker destination setup."""

    def listen(self, _callback: object) -> None:
        """Satisfy native sensor validation."""

    def stop(self) -> None:
        """Satisfy sensor listener cleanup."""

    def destroy(self) -> bool:
        """Mark only successful native cleanup."""
        self.destroyed = True
        return True

    def _interrupt(self, stage: str) -> None:
        if self.interrupt_stage == stage:
            raise SpawnInterrupted


@dataclass
class CreationWorld:
    """A spawn succeeds k times before the next RPC never returns."""

    interrupt_after: int = 1
    interrupt_stage: str = ""
    id: int = WORLD_ID
    actors: list[CreatedActor] = field(default_factory=list)
    frame: int = 0

    def get_snapshot(self) -> SimpleNamespace:
        """Retain a real frame and published created-actor lookup."""
        return SimpleNamespace(frame=self.frame, find=self._snapshot_actor)

    def _snapshot_actor(self, actor_id: int) -> CreatedActor | None:
        return next((actor for actor in self.actors if actor.id == actor_id), None)

    def wait_for_tick(self, _seconds: float) -> SimpleNamespace:
        """Publish the transform barrier before the tested controller interruption."""
        self.frame += 1
        return self.get_snapshot()

    def set_pedestrians_seed(self, _seed: int) -> None:
        """Accept native seed setup without altering interruption counters."""

    def spawn_actor(self, *_args: object) -> CreatedActor:
        """Return a raw actor ID or interrupt the next spawn before it returns."""
        if len(self.actors) >= self.interrupt_after:
            raise SpawnInterrupted
        actor = CreatedActor(FIRST_ACTOR_ID + len(self.actors), self.interrupt_stage)
        self.actors.append(actor)
        return actor

    def try_spawn_actor(self, *_args: object) -> CreatedActor:
        """Use the same returned-ID boundary for optional traffic/walker spawning."""
        return self.spawn_actor()

    def get_blueprint_library(self) -> Mock:
        """Provide one deterministic blueprint without importing CARLA."""
        blueprint = Mock(id="vehicle.test")
        blueprint.has_attribute.return_value = False
        return Mock(find=Mock(return_value=blueprint), filter=Mock(return_value=[blueprint]))

    def get_map(self) -> SimpleNamespace:
        """Provide enough spawn points for the interrupted traffic population."""
        return SimpleNamespace(name="Town01", get_spawn_points=lambda: list(range(8)))

    def get_settings(self) -> SimpleNamespace:
        """Allow asynchronous density without introducing a tick owner."""
        return SimpleNamespace(
            synchronous_mode=False, fixed_delta_seconds=None, no_rendering_mode=False
        )

    def get_actors(self, _actor_ids: object = None) -> Mock:
        """Keep the actor snapshot empty so cleanup requires server acknowledgement."""
        return Mock(filter=Mock(return_value=[]), find=Mock(return_value=None))


@dataclass
class CreationCase:
    """Retain the real adapter, facade, and durable ownership journal."""

    world: CreationWorld
    adapter: PythonCarlaAdapter
    ownership: RunOwnership
    api: CarlaScriptApi
    journal_path: Path
    client: CarlaClient


@pytest.fixture
def creation_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CreationCase:
    """Replace only native simulator dependencies, keeping the creation implementation real."""
    world = CreationWorld()
    manager = Mock()
    manager.get_port.return_value = 8000
    client = cast(
        "CarlaClient",
        SimpleNamespace(get_world=lambda: world, get_trafficmanager=lambda _port: manager),
    )
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", lambda: client)
    monkeypatch.setattr(adapter_objects, "carla_transform", lambda value: value)
    monkeypatch.setattr(experiment_walkers, "random_walker_transform", lambda _world: object())
    monkeypatch.setattr(experiment_walkers, "random_walker_location", lambda _world: object())
    journal_path = tmp_path / "owned-actors.json"
    ownership = RunOwnership(journal_path)
    api = CarlaScriptApi(adapter, RunSnapshots(), ownership=ownership)
    return CreationCase(world, adapter, ownership, api, journal_path, client)


def _spawn_request() -> dict[str, object]:
    return {
        "blueprint_id": "vehicle.test",
        "attributes": {},
        "transform": {
            "location": {"x": 0.0, "y": 0.0, "z": 0.0},
            "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
        },
    }


def _create(api: CarlaScriptApi, helper: str) -> None:
    if helper == "batch":
        api.spawn_actor_batch([_spawn_request()] * 4)
    elif helper == "traffic":
        api.populate_traffic({"vehicle_count": 4, "safe_filter": False, "advance_world": False})
    else:
        api.spawn_walkers(4)


@pytest.mark.parametrize("helper", ["batch", "traffic", "walkers"])
@pytest.mark.parametrize("completed", [1, 3])
def test_interrupted_native_call_keeps_every_returned_id(
    creation_case: CreationCase, helper: str, completed: int
) -> None:
    """The first k raw actor responses survive interruption before the facade call returns."""
    creation_case.world.interrupt_after = completed
    with pytest.raises(SpawnInterrupted):
        _create(creation_case.api, helper)
    expected = tuple(actor.id for actor in creation_case.world.actors)
    assert creation_case.ownership.actor_ids() == expected
    assert creation_case.ownership.world_id() == WORLD_ID


@pytest.mark.parametrize(
    ("helper", "stage", "completed"), [("traffic", "autopilot", 1), ("walkers", "start", 2)]
)
def test_setup_interruption_occurs_only_after_ids_are_durable(
    creation_case: CreationCase, helper: str, stage: str, completed: int
) -> None:
    """Autopilot and controller startup cannot hide actors already returned by CARLA."""
    creation_case.world.interrupt_after = completed
    creation_case.world.interrupt_stage = stage
    with pytest.raises(SpawnInterrupted):
        _create(creation_case.api, helper)
    assert creation_case.ownership.actor_ids() == tuple(
        actor.id for actor in creation_case.world.actors
    )


def test_sensor_id_is_durable_before_native_sensor_validation(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validation interruption must not strand the raw sensor actor outside ownership."""
    monkeypatch.setattr(adapter_objects, "require_sensor", Mock(side_effect=SpawnInterrupted))
    with pytest.raises(SpawnInterrupted):
        creation_case.api.attach_sensor("rgb", None, _spawn_request()["transform"])
    assert creation_case.ownership.actor_ids() == (FIRST_ACTOR_ID,)


@pytest.mark.parametrize("cleanup_error", ["", "server busy"])
def test_screenshot_camera_is_journaled_before_capture_and_released_only_when_cleaned(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch, cleanup_error: str
) -> None:
    """Temporary camera ownership survives capture interruption and unsuccessful destruction."""
    transform = _spawn_request()["transform"]
    monkeypatch.setattr(experiment_scene, "spectator_transform", lambda _world: transform)
    captured_ids: list[tuple[int, ...]] = []

    def capture(**_kwargs: object) -> object:
        captured_ids.append(creation_case.ownership.actor_ids())
        raise SpawnInterrupted

    monkeypatch.setattr(creation_case.adapter, "capture_sensor_frame", capture)
    monkeypatch.setattr(
        creation_case.adapter,
        "apply_batch",
        lambda _commands, **_kwargs: {
            "responses": [{"actor_id": FIRST_ACTOR_ID, "error": cleanup_error}]
        },
    )
    _assert_screenshot_interruption(creation_case.api, cleanup_error)
    assert captured_ids == [(FIRST_ACTOR_ID,)]
    expected = (FIRST_ACTOR_ID,) if cleanup_error else ()
    assert creation_case.ownership.actor_ids() == expected


def _assert_screenshot_interruption(api: CarlaScriptApi, cleanup_error: str) -> None:
    if cleanup_error:
        assert api.save_screenshot("capture.png")["ok"] is False
    else:
        with pytest.raises(SpawnInterrupted):
            api.save_screenshot("capture.png")
