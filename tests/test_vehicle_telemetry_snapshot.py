"""Script telemetry must bind every motion measurement to one native snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient

ACTOR_ID = 17
SNAPSHOT_FRAME = 41
SNAPSHOT_SECONDS = 2.05
SNAPSHOT_SPEED = 13.0
TELEMETRY_URI = f"carla-snapshot://actors/{ACTOR_ID}/telemetry"


@dataclass(frozen=True)
class Vector:
    """Native-like immutable vector value."""

    x: float
    y: float
    z: float


def _transform(x: float) -> SimpleNamespace:
    return SimpleNamespace(
        location=Vector(x, 2.0, 0.5),
        rotation=SimpleNamespace(pitch=1.0, yaw=90.0, roll=0.0),
    )


@dataclass
class MotionState:
    """An ActorSnapshot with motion values unlike the live actor getters."""

    calls: list[str] = field(default_factory=list)

    def get_transform(self) -> SimpleNamespace:
        """Return the transform frozen in this snapshot."""
        self.calls.append("transform")
        return _transform(4.0)

    def get_velocity(self) -> Vector:
        """Return one vector, changing on any accidental repeated read."""
        self.calls.append("velocity")
        if self.calls.count("velocity") > 1:
            return Vector(100.0, 0.0, 0.0)
        return Vector(3.0, 4.0, 12.0)

    def get_acceleration(self) -> Vector:
        """Return this frame's acceleration."""
        self.calls.append("acceleration")
        return Vector(0.1, 0.2, 0.3)


@dataclass
class WorldSnapshot:
    """Native world snapshot with one optional actor state and timestamp."""

    state: MotionState | None
    frame: int = SNAPSHOT_FRAME
    timestamp: SimpleNamespace = field(
        default_factory=lambda: SimpleNamespace(elapsed_seconds=SNAPSHOT_SECONDS)
    )
    found_ids: list[int] = field(default_factory=list)

    def find(self, actor_id: int) -> MotionState | None:
        """Resolve the requested ID in this frozen frame."""
        self.found_ids.append(actor_id)
        assert actor_id == ACTOR_ID
        return self.state


@dataclass
class Vehicle:
    """Live actor getters deliberately disagree with the selected snapshot."""

    motion_calls: list[str] = field(default_factory=list)
    extra_calls: list[str] = field(default_factory=list)

    def get_transform(self) -> SimpleNamespace:
        """Return a later transform that telemetry must not use."""
        self.motion_calls.append("transform")
        return _transform(999.0)

    def get_velocity(self) -> Vector:
        """Return later velocities that also disagree with each other."""
        self.motion_calls.append("velocity")
        return Vector(100.0 * self.motion_calls.count("velocity"), 0.0, 0.0)

    def get_acceleration(self) -> Vector:
        """Return a later acceleration that telemetry must not use."""
        self.motion_calls.append("acceleration")
        return Vector(9.0, 9.0, 9.0)

    def get_control(self) -> SimpleNamespace:
        """Expose control separately, as native snapshots do not contain it."""
        self.extra_calls.append("control")
        return SimpleNamespace(throttle=0.2, brake=0.0, steer=-0.1)

    def get_speed_limit(self) -> float:
        """Expose the actor's separately read speed limit."""
        self.extra_calls.append("speed_limit")
        return 30.0

    def get_traffic_light_state(self) -> str:
        """Expose the actor's separately read light state."""
        self.extra_calls.append("traffic_light_state")
        return "Green"


@dataclass
class ActorList:
    """Explicit actor-ID resolution, separate from snapshot membership."""

    vehicle: Vehicle | None

    def find(self, actor_id: int) -> Vehicle | None:
        """Resolve the native actor handle when it still exists."""
        assert actor_id == ACTOR_ID
        return self.vehicle


@dataclass
class World:
    """A world retaining one selected frame without any automatic tick."""

    snapshot: WorldSnapshot
    vehicle: Vehicle | None
    synchronous_mode: bool
    snapshot_calls: int = 0
    actor_queries: list[list[int]] = field(default_factory=list)

    def get_snapshot(self) -> WorldSnapshot:
        """Count retrievals so a second frame cannot silently enter the result."""
        self.snapshot_calls += 1
        return self.snapshot

    def get_actors(self, actor_ids: list[int]) -> ActorList:
        """Resolve a handle even when it has not reached the cached snapshot."""
        self.actor_queries.append(actor_ids)
        return ActorList(self.vehicle)


@dataclass
class Client:
    """Cached native client fake with no simulator connection."""

    world: World

    def set_timeout(self, _seconds: float) -> None:
        """Accept the existing adapter timeout refresh."""

    def get_world(self) -> World:
        """Return the retained world."""
        return self.world


@dataclass
class TelemetryCase:
    """Real adapter, facade and snapshot cache over native-like values."""

    world: World
    adapter: PythonCarlaAdapter
    api: CarlaScriptApi
    snapshots: RunSnapshots


def _case(*, synchronous: bool, present: bool = True) -> TelemetryCase:
    state = MotionState() if present else None
    world = World(WorldSnapshot(state), Vehicle(), synchronous)
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", Client(world))  # noqa: SLF001
    snapshots = RunSnapshots()
    return TelemetryCase(world, adapter, CarlaScriptApi(adapter, snapshots), snapshots)


def _expected_telemetry() -> dict[str, object]:
    return {
        "actor_id": ACTOR_ID,
        "frame": SNAPSHOT_FRAME,
        "elapsed_seconds": SNAPSHOT_SECONDS,
        "transform": {
            "location": {"x": 4.0, "y": 2.0, "z": 0.5},
            "rotation": {"pitch": 1.0, "yaw": 90.0, "roll": 0.0},
        },
        "velocity": {"x": 3.0, "y": 4.0, "z": 12.0},
        "acceleration": {"x": 0.1, "y": 0.2, "z": 0.3},
        "control": {"throttle": 0.2, "brake": 0.0, "steer": -0.1},
        "speed_limit": 30.0,
        "traffic_light_state": "Green",
        "speed_mps": SNAPSHOT_SPEED,
    }


def _assert_snapshot_lookup(case: TelemetryCase) -> None:
    assert case.world.snapshot_calls == 1
    assert case.world.snapshot.found_ids == [ACTOR_ID]


def _assert_one_frame(case: TelemetryCase) -> None:
    _assert_snapshot_lookup(case)
    state = case.world.snapshot.state
    assert state is not None
    vehicle = case.world.vehicle
    assert vehicle is not None
    assert {
        "snapshot_motion": sorted(state.calls),
        "actor_motion": vehicle.motion_calls,
        "actor_extra": vehicle.extra_calls,
        "actor_queries": case.world.actor_queries,
    } == {
        "snapshot_motion": ["acceleration", "transform", "velocity"],
        "actor_motion": [],
        "actor_extra": ["control", "speed_limit", "traffic_light_state"],
        "actor_queries": [[ACTOR_ID]],
    }


def _assert_structured_error(result: dict[str, object], message: str) -> None:
    assert result == {
        "ok": False,
        "error_type": "get_vehicle_telemetry_failed",
        "retryable": True,
        "message": message,
        "error": message,
    }


def _assert_no_getter_fallback(case: TelemetryCase) -> None:
    vehicle = case.world.vehicle
    assert vehicle is not None
    assert vehicle.motion_calls == []
    assert vehicle.extra_calls == []
    assert case.snapshots.snapshot_uris() == ()


@pytest.mark.parametrize("synchronous", [False, True])
def test_native_telemetry_uses_one_snapshot_for_motion_and_speed(*, synchronous: bool) -> None:
    """Async and sync motion should have identical frame-coherent provenance."""
    case = _case(synchronous=synchronous)

    result = case.adapter.get_vehicle_telemetry(ACTOR_ID)

    assert result == _expected_telemetry()
    _assert_one_frame(case)


@pytest.mark.parametrize("synchronous", [False, True])
def test_facade_publishes_the_same_frame_coherent_telemetry(*, synchronous: bool) -> None:
    """The facade snapshot preserves native frame and elapsed simulation time."""
    case = _case(synchronous=synchronous)

    result = case.api.get_vehicle_telemetry(ACTOR_ID)

    assert result == _expected_telemetry()
    assert case.snapshots.read_snapshot(TELEMETRY_URI) == result
    _assert_one_frame(case)


@pytest.mark.parametrize("synchronous", [False, True])
def test_not_yet_observed_actor_returns_structured_error_without_getter_fallback(
    *, synchronous: bool
) -> None:
    """A resolvable handle must not masquerade as snapshot membership."""
    case = _case(synchronous=synchronous, present=False)

    result = case.api.get_vehicle_telemetry(ACTOR_ID)

    _assert_structured_error(
        result, f"Actor {ACTOR_ID} was not found in world snapshot frame {SNAPSHOT_FRAME}."
    )
    _assert_snapshot_lookup(case)
    _assert_no_getter_fallback(case)


def test_native_adapter_explicitly_rejects_missing_snapshot_actor() -> None:
    """Native callers receive the adapter error rather than fabricated motion."""
    case = _case(synchronous=False, present=False)

    with pytest.raises(CarlaAdapterError, match="snapshot"):
        case.adapter.get_vehicle_telemetry(ACTOR_ID)


def test_actor_lookup_failure_keeps_existing_structured_error() -> None:
    """A gone live handle still cannot supply the separate actor-only fields."""
    case = _case(synchronous=False)
    case.world.vehicle = None

    result = case.api.get_vehicle_telemetry(ACTOR_ID)

    assert result["ok"] is False
    assert result["error_type"] == "get_vehicle_telemetry_failed"
    assert result["message"] == f"Actor {ACTOR_ID} was not found."


@pytest.mark.parametrize(
    "method_name", ["get_snapshot", "get_transform", "get_velocity", "get_acceleration"]
)
def test_native_snapshot_read_failure_is_structured_without_fallback(
    monkeypatch: pytest.MonkeyPatch, method_name: str
) -> None:
    """A failed native frame read cannot silently turn into mixed actor values."""
    case = _case(synchronous=False)
    target = case.world if method_name == "get_snapshot" else case.world.snapshot.state
    assert target is not None
    message = f"native {method_name} failed"

    def fail_read() -> None:
        raise RuntimeError(message)

    monkeypatch.setattr(target, method_name, fail_read)

    result = case.api.get_vehicle_telemetry(ACTOR_ID)

    _assert_structured_error(result, message)
    _assert_no_getter_fallback(case)
