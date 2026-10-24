"""Native-like walker actors, publication and acknowledgements without CARLA."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit import experiment_walkers
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from carla_agentic_toolkit.carla_protocols import CarlaClient

WALKER = 101
CONTROLLER = 102


@dataclass
class WalkerActor:
    """Retain original controller Stop and walker publication state."""

    id: int
    type_id: str
    world: WalkerWorld
    attributes: dict[str, str] = field(default_factory=dict)

    def stop(self) -> None:
        """Record native navigation removal or its failure."""
        self.world.events.append(("stop", self.id))
        self.world.fail("stop")
        self.world.drift("stop")

    def start(self) -> None:
        """Record the navigation registration boundary."""
        self.world.events.append(("start", self.id))
        self.world.fail("start")

    def set_max_speed(self, _speed: float) -> None:
        """Expose a setup failure after Start."""
        self.world.events.append(("speed", self.id))
        self.world.fail("speed")

    def go_to_location(self, _location: object) -> None:
        """Record the final native navigation setup."""
        self.world.events.append(("destination", self.id))

    def get_transform(self) -> object:
        """Expose the actor API without independent motion sampling."""
        return SimpleNamespace()

    def get_velocity(self) -> object:
        """Return stationary motion for actor capability validation."""
        return SimpleNamespace(x=0, y=0, z=0)

    def destroy(self) -> bool:
        """Delete on true acknowledgement, or require the batch fallback."""
        self.world.events.append(("destroy", self.id))
        if not self.world.cached_destroy:
            return False
        self.world.actors.pop(self.id, None)
        return True


@dataclass
class WalkerWorld:
    """Publish actors only on Tick/WaitForTick, as a retained client stream does."""

    id: int = 17
    synchronous: bool = False
    frame: int = 10
    failure: str = ""
    change_episode: str = ""
    cached_destroy: bool = True
    published: bool = False
    omit_walker: bool = False
    actors: dict[int, WalkerActor] = field(default_factory=dict)
    events: list[tuple[str, object]] = field(default_factory=list)

    def fail(self, stage: str) -> None:
        """Raise the chosen native failure without changing its origin."""
        if self.failure == stage:
            message = f"{stage} failed"
            raise RuntimeError(message)

    def drift(self, stage: str) -> None:
        """Model external episode replacement at a native boundary."""
        if self.change_episode == stage:
            self.id += 1

    def set_pedestrians_seed(self, seed: int) -> None:
        """Record native seed calls independently of location sampling."""
        self.events.append(("seed", seed))
        self.fail("seed")

    def get_random_location_from_navigation(self) -> object:
        """Record every navigation draw before returning a location."""
        self.events.append(("navigation", self.frame))
        return object()

    def get_blueprint_library(self) -> SimpleNamespace:
        """Return the actual pedestrian/controller blueprint families."""
        return SimpleNamespace(
            filter=lambda _pattern: [SimpleNamespace(id="walker.pedestrian.0001")],
            find=lambda name: SimpleNamespace(id=name),
        )

    def try_spawn_actor(self, blueprint: object, _transform: object) -> WalkerActor:
        """Return one newly created walker before snapshot publication."""
        return self._create(WALKER, str(cast("SimpleNamespace", blueprint).id))

    def spawn_actor(self, blueprint: object, _transform: object, _parent: object) -> WalkerActor:
        """Return its AI controller before navigation registration."""
        return self._create(CONTROLLER, str(cast("SimpleNamespace", blueprint).id))

    def _create(self, actor_id: int, type_id: str) -> WalkerActor:
        self.events.append(("spawn", actor_id))
        actor = WalkerActor(actor_id, type_id, self)
        self.actors[actor_id] = actor
        return actor

    def get_settings(self) -> SimpleNamespace:
        """Expose a native boolean mode for the owner barrier."""
        return SimpleNamespace(synchronous_mode=self.synchronous)

    def get_snapshot(self) -> SimpleNamespace:
        """Expose only actors that have reached the creating-client stream."""
        return SimpleNamespace(frame=self.frame, find=self._snapshot_actor)

    def _snapshot_actor(self, actor_id: int) -> object | None:
        if not self.published or self.omit_walker:
            return None
        return self.actors.get(actor_id)

    def tick(self, seconds: float = 10.0) -> int:
        """Record a bounded synchronous owner cue."""
        self._advance("tick", seconds)
        return self.frame

    def wait_for_tick(self, seconds: float) -> SimpleNamespace:
        """Record a bounded asynchronous publication wait."""
        self._advance("wait", seconds)
        return self.get_snapshot()

    def _advance(self, operation: str, seconds: float) -> None:
        self.events.append((operation, seconds))
        self.fail("barrier")
        if self.failure != "stale":
            self.frame += 1
        self.published = True
        self.drift("barrier")

    def get_actors(self, _ids: object = None) -> SimpleNamespace:
        """Resolve explicit IDs authoritatively, even before publication."""
        return SimpleNamespace(find=self.actors.get)


@dataclass
class WalkerCase:
    """Retain independent native state and the actual durable API boundary."""

    world: WalkerWorld
    adapter: PythonCarlaAdapter
    ownership: RunOwnership
    api: CarlaScriptApi
    journal_path: Path


def walker_case(path: Path, monkeypatch: pytest.MonkeyPatch) -> WalkerCase:
    """Keep adapter/facade/journal real; replace native constructors and transport only."""
    world = WalkerWorld()
    client = SimpleNamespace(get_world=lambda: world, set_timeout=lambda _seconds: None)
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_connected_client", cast("CarlaClient", client))
    monkeypatch.setattr(experiment_walkers, "random_walker_transform", _navigation_transform)
    monkeypatch.setattr(adapter, "apply_batch", lambda commands, **_kwargs: _batch(world, commands))
    ownership = RunOwnership(path)
    api = CarlaScriptApi(adapter, RunSnapshots(), ownership=ownership)
    return WalkerCase(world, adapter, ownership, api, path)


def _navigation_transform(world: object) -> object:
    return cast("WalkerWorld", world).get_random_location_from_navigation()


def _batch(world: WalkerWorld, commands: list[dict[str, object]]) -> dict[str, object]:
    actor_id = int(cast("int", commands[0]["actor_id"]))
    world.events.append(("batch", actor_id))
    world.actors.pop(actor_id, None)
    return {"responses": [{"actor_id": actor_id, "error": ""}]}
