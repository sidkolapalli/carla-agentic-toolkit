"""Native managed creation is authorized by durable intent and returned IDs only."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from carla_agentic_toolkit import experiment_replay, merge_experiment, route_actors
from carla_agentic_toolkit.managed_creation import CreationOptions
from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.merge_fixture import Pose
from carla_agentic_toolkit.route_geometry import RoutePath, RoutePoint
from carla_agentic_toolkit.simulator_lease import RecoveryRequiredError, SimulatorLease
from scripts import capture_experiment_demo, capture_route_demo
from tests.test_managed_session import FakeWorld, _apply_destroy_batch
from tests.test_merge_experiment import Blueprint

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaBlueprint, CarlaClient

RUN_ID = "journal-run"
ACTOR_ID = 11
POSE = {
    "location": {"x": 0.0, "y": 0.0, "z": 0.4},
    "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
}


@dataclass
class SpawnCase:
    """Real exclusive lease and session with an independently controlled native boundary."""

    session: ManagedSession
    lease: SimulatorLease
    world: SpawnWorld
    client: CarlaClient
    calls: list[object] = field(default_factory=list)

    def vehicle(self) -> object:
        """Exercise the existing fixture entry point, not a future helper import."""
        return merge_experiment.MergeExperiment(self.session)._spawn_vehicle(  # noqa: SLF001 -- Native fixture boundary.
            "policy", Pose(0, 0, 0, 0)
        )


class SpawnWorld(FakeWorld):
    """Make the tested native spawn interface explicit for the real session."""

    def spawn_actor(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        """Replace this native boundary before any call."""
        raise NotImplementedError


def _plans(state: dict[str, object]) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", state.get("spawn_intents", []))


def _native_transform(
    location: object | None = None, rotation: object | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        location=location or SimpleNamespace(x=0.0, y=0.0, z=0.0),
        rotation=rotation or SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0),
    )


def _vector(**values: float) -> SimpleNamespace:
    return SimpleNamespace(**({"x": 0.0, "y": 0.0, "z": 0.0} | values))


def _rotation(**values: float) -> SimpleNamespace:
    return SimpleNamespace(**({"pitch": 0.0, "yaw": 0.0, "roll": 0.0} | values))


@pytest.fixture
def spawn_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SpawnCase]:
    """Open the real managed owner before adding any actor to its dedicated world."""
    world = SpawnWorld()
    client = cast(
        "CarlaClient", SimpleNamespace(get_world=lambda: world, reload_world=world.reload_world)
    )
    native = SimpleNamespace(Location=_vector, Rotation=_rotation, Transform=_native_transform)
    monkeypatch.setattr(merge_experiment, "import_module", lambda _name: native)
    monkeypatch.setattr(experiment_replay, "apply_batch", _apply_destroy_batch)
    monkeypatch.setattr(
        world, "get_blueprint_library", lambda: SimpleNamespace(find=Blueprint), raising=False
    )
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(ExperimentSpec(), client, lease, RUN_ID)
        session.open()
        case = SpawnCase(session, lease, world, client)

        def spawn(blueprint: Blueprint, _transform: object, **_kwargs: object) -> SimpleNamespace:
            case.calls.append(blueprint.id)
            actor = SimpleNamespace(
                id=11 + len(world.actors),
                type_id=blueprint.id,
                attributes=blueprint.attributes.copy(),
            )
            world.actors.append(actor)
            return actor

        monkeypatch.setattr(world, "spawn_actor", spawn, raising=False)
        yield case


def test_intent_is_durable_before_native_spawn(
    spawn_case: SpawnCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A native RPC may not begin before its same-episode plan has reached the lease journal."""
    original = spawn_case.world.spawn_actor

    def spawn(*args: object, **kwargs: object) -> object:
        plans = _plans(spawn_case.lease.recovery_state)
        assert len(plans) == 1
        assert plans[0]["world_id"] == spawn_case.world.id
        assert plans[0]["actor_id"] is None
        assert plans[0]["transform"] == POSE
        return original(*args, **kwargs)

    monkeypatch.setattr(spawn_case.world, "spawn_actor", spawn)
    spawn_case.vehicle()
    assert spawn_case.lease.recovery_state["spawn_intents"] == []


def test_raw_returned_id_is_durable_before_metadata(
    spawn_case: SpawnCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading attributes or type identity must follow the first durable raw-ID write."""
    case = spawn_case

    class Actor:
        id = 11
        type_id = "vehicle.tesla.model3"

        @property
        def attributes(self) -> dict[str, str]:
            plans = _plans(case.lease.recovery_state)
            assert [plan["actor_id"] for plan in plans] == [11]
            return {"role_name": "hero"}

    monkeypatch.setattr(case.world, "spawn_actor", lambda *_args: Actor())
    case.vehicle()
    actors = cast("list[dict[str, object]]", case.lease.recovery_state["actors"])
    assert actors[0]["actor_id"] == ACTOR_ID


def test_failed_pre_spawn_journal_prevents_native_call(
    spawn_case: SpawnCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed authorization write cannot be followed by a native mutation."""
    dirty = spawn_case.lease.mark_dirty

    def fail(state: dict[str, object]) -> None:
        if state.get("spawn_intents"):
            message = "planned spawn journal failed"
            raise OSError(message)
        dirty(state)

    monkeypatch.setattr(spawn_case.lease, "mark_dirty", fail)
    with pytest.raises((OSError, RuntimeError), match="journal"):
        spawn_case.vehicle()
    assert spawn_case.calls == []
    assert spawn_case.session.close()["ok"] is False


def test_returned_id_journal_failure_preserves_cleanup_handle(
    spawn_case: SpawnCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed raw-ID persistence cannot discard the known returned actor in this worker."""
    dirty = spawn_case.lease.mark_dirty

    def fail(state: dict[str, object]) -> None:
        if any(plan["actor_id"] is not None for plan in _plans(state)):
            message = "returned ID journal failed"
            raise OSError(message)
        dirty(state)

    monkeypatch.setattr(spawn_case.lease, "mark_dirty", fail)
    with pytest.raises((OSError, RuntimeError), match="journal"):
        spawn_case.vehicle()
    assert spawn_case.session.close()["ok"] is False
    assert spawn_case.world.destroyed == [11]
    assert spawn_case.lease.recovery_state


def test_known_id_survives_completion_write_failure(
    spawn_case: SpawnCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A separate completed-intent write may fail after durable returned identity exists."""
    case = spawn_case
    dirty = case.lease.mark_dirty

    def fail(state: dict[str, object]) -> None:
        if state.get("actors") and not state.get("spawn_intents"):
            message = "completion journal failed"
            raise OSError(message)
        dirty(state)

    monkeypatch.setattr(case.lease, "mark_dirty", fail)
    with pytest.raises((OSError, RuntimeError), match="completion"):
        case.vehicle()
    assert [plan["actor_id"] for plan in _plans(case.lease.recovery_state)] == [11]


def test_episode_change_during_intent_write_prevents_native_spawn(
    spawn_case: SpawnCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The new pre-RPC write must not leave an unchecked origin window."""
    dirty = spawn_case.lease.mark_dirty

    def replace_after_write(state: dict[str, object]) -> None:
        dirty(state)
        if state.get("spawn_intents"):
            spawn_case.world.id += 1

    monkeypatch.setattr(spawn_case.lease, "mark_dirty", replace_after_write)
    with pytest.raises(RuntimeError, match="world"):
        spawn_case.vehicle()
    assert spawn_case.calls == []
    assert spawn_case.session.close()["ok"] is False


@pytest.mark.parametrize("invalid_id", [True, "11", None])
def test_invalid_native_id_never_becomes_cleanup_authority(
    spawn_case: SpawnCase, monkeypatch: pytest.MonkeyPatch, invalid_id: object
) -> None:
    """Malformed results cannot authorize even a guessed integer deletion."""
    requests: list[object] = []
    monkeypatch.setattr(
        spawn_case.world,
        "spawn_actor",
        lambda *_args: SimpleNamespace(
            id=invalid_id, type_id="vehicle.tesla.model3", attributes={"role_name": "hero"}
        ),
    )

    def batch(_client: object, commands: object, *, do_tick: bool) -> dict[str, object]:
        assert do_tick is False
        requests.append(commands)
        return {"responses": []}

    monkeypatch.setattr(experiment_replay, "apply_batch", batch)
    with pytest.raises(RuntimeError, match="ID"):
        spawn_case.vehicle()
    assert spawn_case.session.close()["ok"] is False
    assert requests == []


@pytest.mark.parametrize(
    "boundary", ["merge_sensor", "route_vehicle", "route_sensor", "merge_camera", "route_camera"]
)
def test_every_managed_sensor_and_demo_camera_has_pre_native_intent(
    spawn_case: SpawnCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, boundary: str
) -> None:
    """Managed instrumentation follows the same intent boundary as fixture vehicles."""
    case = spawn_case
    module = SimpleNamespace(Location=_vector, Rotation=_rotation, Transform=_native_transform)
    monkeypatch.setattr(route_actors, "import_module", lambda _name: module)
    monkeypatch.setattr(capture_route_demo, "import_module", lambda _name: module)
    parent = SimpleNamespace(id=99)

    def spawn(blueprint: Blueprint, _transform: object, **kwargs: object) -> SimpleNamespace:
        plans = _plans(case.lease.recovery_state)
        assert len(plans) == 1
        assert plans[0]["type_id"] == blueprint.id
        assert plans[0]["attach_to"] == getattr(kwargs.get("attach_to"), "id", None)
        actor = SimpleNamespace(
            id=11 + len(case.world.actors),
            type_id=blueprint.id,
            attributes=blueprint.attributes.copy(),
            listen=lambda _callback: None,
            stop=lambda: None,
        )
        case.world.actors.append(actor)
        return actor

    monkeypatch.setattr(case.world, "spawn_actor", spawn)
    merge = merge_experiment.MergeExperiment(case.session)
    route = route_actors.RouteActors(case.session, RoutePath((RoutePoint(0, 0), RoutePoint(50, 0))))
    actions = {
        "merge_sensor": lambda: merge._attach_sensor(  # noqa: SLF001 -- Native fixture boundary.
            "policy", parent, "gnss"
        ),
        "route_vehicle": lambda: route.spawn("policy", RoutePoint(0, 0)),
        "route_sensor": lambda: route._sensor(  # noqa: SLF001 -- Native sensor boundary.
            "policy", parent
        ),
        "merge_camera": lambda: _merge_camera(case.session, monkeypatch, tmp_path),
        "route_camera": lambda: _route_camera(case.session, route, parent, monkeypatch, tmp_path),
    }
    actions[boundary]()
    assert case.lease.recovery_state["spawn_intents"] == []


def _merge_camera(session: ManagedSession, monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(capture_experiment_demo.MergeExperiment, "prepare", lambda _self: None)
    monkeypatch.setattr(
        capture_experiment_demo.CameraExperiment,
        "_camera_transform",
        lambda _self: _native_transform(),
    )
    capture_experiment_demo.CameraExperiment(session, path / "merge").prepare()


def _route_camera(
    session: ManagedSession,
    route: route_actors.RouteActors,
    parent: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
) -> None:
    monkeypatch.setattr(capture_route_demo.RouteExperiment, "prepare", lambda _self: None)
    camera = capture_route_demo.CameraRoute(session, path / "route")
    camera.actors = route
    camera.actors.handles["policy"] = parent
    camera.prepare()


@pytest.mark.parametrize("matching_role", ["hero", f"managed:{RUN_ID}:policy"])
def test_lost_reply_never_adopts_resembling_actor(
    spawn_case: SpawnCase, monkeypatch: pytest.MonkeyPatch, matching_role: str
) -> None:
    """A matching label, type, and pose cannot distinguish creation from an outsider."""

    def lost_reply(blueprint: Blueprint, _transform: object) -> None:
        spawn_case.world.actors.append(
            SimpleNamespace(
                id=77, type_id=blueprint.id, attributes={"role_name": matching_role}, pose=POSE
            )
        )
        message = "native spawn reply lost"
        raise RuntimeError(message)

    monkeypatch.setattr(spawn_case.world, "spawn_actor", lost_reply)
    with pytest.raises(RuntimeError, match="reply lost"):
        spawn_case.vehicle()
    report = spawn_case.session.close()
    assert spawn_case.world.destroyed == []
    assert report["ok"] is False
    assert spawn_case.lease.recovery_state


def _plan(actor_id: int | None, world_id: int) -> dict[str, object]:
    return {
        "intent_id": 1,
        "world_id": world_id,
        "type_id": "vehicle.test",
        "role_name": "hero",
        "controller": "policy",
        "protected": True,
        "transform": POSE,
        "attach_to": None,
        "actor_id": actor_id,
    }


@pytest.mark.parametrize("actor_id", [None, 11])
def test_fresh_recovery_uses_only_durable_returned_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, actor_id: int | None
) -> None:
    """Known-ID completion uncertainty is recoverable; an unknown result stays quarantined."""
    world = FakeWorld()
    client = cast(
        "CarlaClient", SimpleNamespace(get_world=lambda: world, reload_world=world.reload_world)
    )
    before = world.settings.copy()
    monkeypatch.setattr(experiment_replay, "apply_batch", _apply_destroy_batch)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(ExperimentSpec(), client, lease, RUN_ID)
        session.open()
        state = lease.recovery_state | {
            "spawn_journal_version": 1,
            "spawn_intents": [_plan(actor_id, world.id)],
        }
        lease.mark_dirty(state)
    world.actors.append(
        SimpleNamespace(id=11, type_id="vehicle.test", attributes={"role_name": "hero"})
    )
    with SimulatorLease("localhost", 3000, state_root=tmp_path, recovering=True) as lease:
        report = ManagedSession.recover(client, lease, ExperimentSpec())
        assert world.destroyed == ([] if actor_id is None else [11])
        assert report["ok"] is (actor_id is not None)
        assert bool(lease.recovery_state) is (actor_id is None)
        assert world.settings == before
    _assert_normal_lease(tmp_path, dirty=actor_id is None)


def _assert_normal_lease(path: Path, *, dirty: bool) -> None:
    if dirty:
        with (
            pytest.raises(RecoveryRequiredError),
            SimulatorLease("localhost", 3000, state_root=path),
        ):
            pass


def test_legacy_marker_cannot_authorize_prefix_adoption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old journals lacked pre-native intent coverage and cannot prove unknown outcomes absent."""
    world = FakeWorld()
    client = cast(
        "CarlaClient", SimpleNamespace(get_world=lambda: world, reload_world=world.reload_world)
    )
    monkeypatch.setattr(experiment_replay, "apply_batch", _apply_destroy_batch)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(ExperimentSpec(), client, lease, RUN_ID)
        session.open()
        state = _legacy_state(lease.recovery_state)
        lease.mark_dirty(state)
    marker = next(tmp_path.glob("*.json"))
    original_bytes = marker.read_bytes()
    world.actors.append(
        SimpleNamespace(
            id=77, type_id="vehicle.test", attributes={"role_name": f"managed:{RUN_ID}:policy"}
        )
    )
    with SimulatorLease("localhost", 3000, state_root=tmp_path, recovering=True) as lease:
        report = ManagedSession.recover(client, lease, ExperimentSpec())
        assert world.destroyed == []
        assert report["ok"] is False
        assert lease.recovery_state
        assert marker.read_bytes() == original_bytes


def _legacy_state(state: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in state.items()
        if key not in {"spawn_journal_version", "spawn_intents"}
    }


def test_own_cannot_resolve_unknown_spawn(spawn_case: SpawnCase) -> None:
    """Trusted compatibility registration cannot complete an unrelated unknown native intent."""
    journal = spawn_case.session.creation
    journal.begin(
        cast("CarlaBlueprint", Blueprint("vehicle.test")),
        _native_transform(),
        CreationOptions("hero", "policy"),
    )
    actor = SimpleNamespace(id=11, type_id="vehicle.test", attributes={"role_name": "hero"})
    spawn_case.world.actors.append(actor)
    spawn_case.session.own(cast("CarlaActor", actor), controller="policy", protected=True)
    report = spawn_case.session.close()
    assert spawn_case.world.destroyed == [11]
    assert report["ok"] is False
    assert [plan["actor_id"] for plan in _plans(spawn_case.lease.recovery_state)] == [None]


@pytest.mark.parametrize("version", [True, 1.0])
def test_noninteger_version_cannot_manufacture_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: object
) -> None:
    """Only the recorded integer schema version can establish new intent coverage."""
    world = FakeWorld()
    client = cast(
        "CarlaClient", SimpleNamespace(get_world=lambda: world, reload_world=world.reload_world)
    )
    monkeypatch.setattr(experiment_replay, "apply_batch", _apply_destroy_batch)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(ExperimentSpec(), client, lease, RUN_ID)
        session.open()
        lease.mark_dirty(lease.recovery_state | {"spawn_journal_version": version})
    marker = next(tmp_path.glob("*.json"))
    original_bytes = marker.read_bytes()
    with SimulatorLease("localhost", 3000, state_root=tmp_path, recovering=True) as lease:
        report = ManagedSession.recover(client, lease, ExperimentSpec())
        assert report["ok"] is False
        assert marker.read_bytes() == original_bytes
