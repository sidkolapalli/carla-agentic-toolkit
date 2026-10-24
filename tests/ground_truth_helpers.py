"""Native-like, read-only fixtures for script ground-truth queries."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaClient


def vector(x: float, y: float = 0.0, z: float = 0.0) -> SimpleNamespace:
    """Return native-like vector coordinates."""
    return SimpleNamespace(x=x, y=y, z=z)


def transform(x: float, *, yaw: float = 90.0) -> SimpleNamespace:
    """Return a frozen transform distinct from live actor readings."""
    return SimpleNamespace(
        location=vector(x, z=0.5),
        rotation=SimpleNamespace(pitch=0.0, yaw=yaw, roll=0.0),
    )


def _yaw(x: float, y: float, degrees: float) -> tuple[float, float]:
    angle = math.radians(degrees)
    return math.cos(angle) * x - math.sin(angle) * y, math.sin(angle) * x + math.cos(angle) * y


@dataclass
class NativeBox:
    """A native box method that composes local offset and both rotations."""

    location: SimpleNamespace = field(default_factory=lambda: vector(2.0))
    extent: SimpleNamespace = field(default_factory=lambda: vector(1.0, 2.0, 3.0))
    rotation: SimpleNamespace = field(
        default_factory=lambda: SimpleNamespace(pitch=0.0, yaw=90.0, roll=0.0)
    )
    transforms: list[object] = field(default_factory=list)
    invalid_vertex: bool = False

    def get_world_vertices(self, value: SimpleNamespace) -> list[SimpleNamespace]:
        """Return eight actual transformed corners, recording the exact argument."""
        self.transforms.append(value)
        origin_x, origin_y = _yaw(self.location.x, self.location.y, value.rotation.yaw)
        vertices = [
            self._corner(value, (origin_x, origin_y), (x, y, z))
            for z in (-1, 1)
            for y in (-1, 1)
            for x in (-1, 1)
        ]
        if self.invalid_vertex:
            vertices[0].x = math.nan
        return vertices

    def _corner(
        self, value: SimpleNamespace, origin: tuple[float, float], signs: tuple[int, int, int]
    ) -> SimpleNamespace:
        origin_x, origin_y = origin
        x, y, z = signs
        corner_x, corner_y = _yaw(
            x * self.extent.x, y * self.extent.y, self.rotation.yaw + value.rotation.yaw
        )
        return vector(
            value.location.x + origin_x + corner_x,
            value.location.y + origin_y + corner_y,
            value.location.z + self.location.z + z * self.extent.z,
        )


@dataclass
class NativeActor:
    """Only box metadata and camera attributes are legitimate actor reads."""

    id: int
    type_id: str = "vehicle.test"
    attributes: dict[str, str] = field(default_factory=dict)
    bounding_box: NativeBox = field(default_factory=NativeBox)
    live_transform_calls: int = 0
    samples: list[object] = field(default_factory=list)
    listen_calls: int = 0
    stop_calls: int = 0

    def get_transform(self) -> SimpleNamespace:
        """Expose a disagreeing value to catch a hidden live-read fallback."""
        self.live_transform_calls += 1
        return transform(999.0)

    def listen(self, callback: object) -> None:
        """Deliver queued fake measurements without networking or ticking."""
        self.listen_calls += 1
        for sample in self.samples:
            cast("Callable[[object], None]", callback)(sample)

    def stop(self) -> None:
        """Acknowledge listener shutdown without destroying the actor."""
        self.stop_calls += 1


@dataclass
class NativeState:
    """One actor state frozen in a selected physics frame."""

    value: SimpleNamespace
    transform_calls: int = 0

    def get_transform(self) -> SimpleNamespace:
        """Return the original frame's transform exactly once."""
        self.transform_calls += 1
        return self.value


@dataclass
class NativeSnapshot:
    """The selected snapshot, which does not advance or mutate the simulator."""

    states: dict[int, NativeState] = field(
        default_factory=lambda: {17: NativeState(transform(10.0)), 18: NativeState(transform(20.0))}
    )
    frame: int = 41
    found_ids: list[int] = field(default_factory=list)

    def find(self, actor_id: int) -> NativeState | None:
        """Record frame-frozen actor selection."""
        self.found_ids.append(actor_id)
        return self.states.get(actor_id)


@dataclass
class NativeActorList:
    """Explicit server actor lookup without cached actor enumeration."""

    actors: dict[int, NativeActor]

    def find(self, actor_id: int) -> NativeActor | None:
        """Resolve only an explicit requested actor."""
        return self.actors.get(actor_id)


def _actors() -> dict[int, NativeActor]:
    return {
        17: NativeActor(17),
        18: NativeActor(18),
        19: NativeActor(
            19,
            type_id="sensor.camera.rgb",
            attributes={"image_size_x": "800", "image_size_y": "600", "fov": "90"},
        ),
    }


@dataclass
class NativeWorld:
    """World exposing read queries, with no simulator mutation capabilities."""

    id: int = 7
    snapshot: NativeSnapshot = field(default_factory=NativeSnapshot)
    actors: dict[int, NativeActor] = field(default_factory=_actors)
    boxes: list[NativeBox] = field(
        default_factory=lambda: [NativeBox(location=vector(x)) for x in (30.0, 2.0, 10.0, 5.0)]
    )
    snapshot_calls: int = 0
    actor_queries: list[list[int]] = field(default_factory=list)
    level_queries: list[object] = field(default_factory=list)
    settings_calls: int = 0

    def get_settings(self) -> SimpleNamespace:
        """Expose real boolean fields for existing stream/listener guards."""
        self.settings_calls += 1
        return SimpleNamespace(synchronous_mode=False, no_rendering_mode=False)

    def get_snapshot(self) -> NativeSnapshot:
        """Return the retained publication without requesting a tick."""
        self.snapshot_calls += 1
        return self.snapshot

    def get_actors(self, actor_ids: list[int]) -> NativeActorList:
        """Require explicit-ID lookup, never a cached inventory read."""
        self.actor_queries.append(actor_ids)
        return NativeActorList(self.actors)

    def get_level_bbs(self, label: object) -> list[NativeBox]:
        """Return deliberately nonspatial native order."""
        self.level_queries.append(label)
        return self.boxes


@dataclass
class NativeClient:
    """Retained client without network access."""

    world: NativeWorld

    def set_timeout(self, _seconds: float) -> None:
        """Accept the adapter's ordinary budget refresh."""

    def get_world(self) -> NativeWorld:
        """Return the unchanged native-like episode."""
        return self.world


@dataclass
class GroundTruthCase:
    """Actual adapter and curated facade over native-like read operations."""

    world: NativeWorld
    adapter: PythonCarlaAdapter
    api: CarlaScriptApi
    snapshots: RunSnapshots


def make_case() -> GroundTruthCase:
    """Construct the real facade without any CARLA import or connection."""
    world = NativeWorld()
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", NativeClient(world))  # noqa: SLF001
    snapshots = RunSnapshots()
    return GroundTruthCase(world, adapter, CarlaScriptApi(adapter, snapshots), snapshots)


def carla_module() -> SimpleNamespace:
    """Expose only the dynamic enum needed by the static-box query."""
    return SimpleNamespace(CityObjectLabel=SimpleNamespace(Any="any", Buildings="buildings"))
