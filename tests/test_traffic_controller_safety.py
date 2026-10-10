"""Deterministic regressions for controller lifecycle, ownership, and timing."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import traffic_controller_service as controller
from carla_agentic_toolkit import traffic_controller_step, traffic_density
from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.models import TrafficControllerStartRequest, TrafficDensityRequest
from tests.test_traffic_controller_service import ActorCollection, TrafficManager, VehicleActor

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.models import TrafficManagerRequest

TARGET_COUNT = 3


@dataclass
class MultiWorld:
    """World with mutable vehicle population and explicitly controlled timing."""

    actors: list[VehicleActor]
    synchronous_mode: bool = False
    events: list[str] = field(default_factory=list)

    def get_actors(self, _actor_ids: list[int] | None = None) -> ActorCollection:
        """Return surviving actors."""
        return ActorCollection(tuple(actor for actor in self.actors if not actor.destroyed))

    def get_settings(self) -> SimpleNamespace:
        """Return the current authoritative world mode."""
        return SimpleNamespace(synchronous_mode=self.synchronous_mode)

    def wait_for_tick(self, _seconds: float) -> None:
        """Record waits without advancing the world."""
        self.events.append("wait")


def _client(world: MultiWorld) -> CarlaClient:
    return cast("CarlaClient", SimpleNamespace(get_world=lambda: world))


def _request(count: int, *, reset: bool = False) -> TrafficControllerStartRequest:
    return TrafficControllerStartRequest(
        density=TrafficDensityRequest(vehicle_count=count, reset_existing=reset)
    )


def _runtime(monkeypatch: pytest.MonkeyPatch, world: MultiWorld) -> list[TrafficManagerRequest]:
    configurations: list[TrafficManagerRequest] = []
    monkeypatch.setattr(traffic_controller_step, "traffic_manager", lambda *_args: TrafficManager())
    monkeypatch.setattr(
        traffic_controller_step,
        "configure_traffic_manager",
        lambda _manager, request, **_kwargs: configurations.append(request),
    )
    monkeypatch.setattr(traffic_density, "populate_traffic_actors", lambda **_kwargs: ([], []))
    monkeypatch.setattr(controller, "_client", lambda _request: _client(world))
    return configurations


@dataclass
class StepGate:
    """Observe a captured request and release its fake CARLA work explicitly."""

    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    request: TrafficControllerStartRequest | None = None
    configure_manager: bool = False
    worker: threading.Thread | None = None


@dataclass
class GatedSteps:
    """Sequence maintenance iterations without timing-dependent sleeps."""

    gates: list[StepGate]
    index: int = 0

    def __call__(
        self,
        _client: object,
        request: TrafficControllerStartRequest,
        *_args: object,
        configure_manager: bool,
        **_kwargs: object,
    ) -> controller.TrafficControllerStep:
        """Block one real worker until its test explicitly permits completion."""
        gate = self.gates[min(self.index, len(self.gates) - 1)]
        self.index += 1
        gate.request = request
        gate.configure_manager = configure_manager
        gate.worker = threading.current_thread()
        gate.entered.set()
        assert gate.release.wait(30.0), "test did not release the blocked maintenance step"
        return controller.TrafficControllerStep(
            request=replace(request, density=replace(request.density, reset_existing=False)),
            vehicle_count=request.density.vehicle_count,
            moving_vehicle_count=0,
            registered_actor_ids=frozenset(),
        )

    def cleanup(self, service: controller.InProcessTrafficControllerService) -> None:
        """Release test workers even when an assertion fails."""
        for gate in self.gates:
            gate.release.set()
        service.stop()
        for gate in self.gates:
            if gate.worker is not None:
                gate.worker.join(1.0)


def _gated_service(
    monkeypatch: pytest.MonkeyPatch, steps: GatedSteps
) -> controller.InProcessTrafficControllerService:
    _runtime(monkeypatch, MultiWorld([]))
    monkeypatch.setattr(controller, "STOP_JOIN_TIMEOUT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(controller, "maintain_traffic_once", steps)
    return controller.InProcessTrafficControllerService()


def _await_gate(gate: StepGate) -> None:
    assert gate.entered.wait(1.0), "controller did not enter the expected maintenance step"


def test_timed_out_worker_remains_active_and_blocks_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A join timeout cannot release the right to start another CARLA worker."""
    steps = GatedSteps([StepGate()])
    service = _gated_service(monkeypatch, steps)
    service.start(_request(1))
    try:
        _await_gate(steps.gates[0])
        stopped = service.stop()
        assert stopped.active is True
        assert stopped.stopping is True
        with pytest.raises(CarlaAdapterError, match="still stopping"):
            service.start(_request(2))
        assert steps.index == 1
    finally:
        steps.cleanup(service)


def test_cancelled_completion_cannot_publish_old_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Late work may finish, but must not republish active state or old counts."""
    steps = GatedSteps([StepGate()])
    service = _gated_service(monkeypatch, steps)
    service.start(_request(1))
    try:
        assert steps.gates[0].entered.wait(1.0)
        service.stop()
        steps.gates[0].release.set()
        worker = steps.gates[0].worker
        assert worker is not None
        worker.join(1.0)
        status = service.get_status()
        assert (status.active, status.vehicle_count, status.applied_revision) == (False, 0, None)
    finally:
        steps.cleanup(service)


def test_newer_density_revisions_survive_older_completions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applied observations advance without overwriting several newer requests."""
    steps = GatedSteps([StepGate(), StepGate()])
    service = _gated_service(monkeypatch, steps)
    service.start(_request(1))
    try:
        assert steps.gates[0].entered.wait(1.0)
        service.set_density(TrafficDensityRequest(vehicle_count=2))
        accepted = service.set_density(TrafficDensityRequest(vehicle_count=3))
        assert accepted.target_vehicle_count == TARGET_COUNT
        steps.gates[0].release.set()
        assert steps.gates[1].entered.wait(1.0)
        status = service.get_status()
        assert {
            "next_request": steps.gates[1].request,
            "configure_manager": steps.gates[1].configure_manager,
            "target": status.target_vehicle_count,
            "applied_target": status.applied_target_vehicle_count,
            "desired_revision": status.desired_revision,
            "applied_revision": status.applied_revision,
        } == {
            "next_request": _request(TARGET_COUNT),
            "configure_manager": True,
            "target": TARGET_COUNT,
            "applied_target": 1,
            "desired_revision": 3,
            "applied_revision": 1,
        }
    finally:
        steps.cleanup(service)


def test_newer_reset_is_consumed_only_by_its_matching_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-flight older reset cannot discard or resurrect a newer reset."""
    steps = GatedSteps([StepGate(), StepGate(), StepGate()])
    service = _gated_service(monkeypatch, steps)
    started = service.start(_request(1, reset=True))
    try:
        _await_gate(steps.gates[0])
        accepted = service.set_density(TrafficDensityRequest(vehicle_count=2, reset_existing=True))
        assert (accepted.generation, steps.index) == (started.generation, 1)
        steps.gates[0].release.set()
        _await_gate(steps.gates[1])
        captured_reset = steps.gates[1].request
        steps.gates[1].release.set()
        _await_gate(steps.gates[2])
        assert (captured_reset, steps.gates[2].request, steps.gates[2].configure_manager) == (
            _request(2, reset=True),
            _request(2),
            False,
        )
    finally:
        steps.cleanup(service)


def test_adoption_never_grants_deletion_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lowering density after adoption leaves pre-existing vehicles intact."""
    actors = [VehicleActor(id=index) for index in range(1, 4)]
    world = MultiWorld(actors)
    _runtime(monkeypatch, world)
    first = controller.maintain_traffic_once(_client(world), _request(3), {})
    lowered = controller.maintain_traffic_once(
        _client(world), _request(1), {}, first.registered_actor_ids
    )

    assert not any(actor.destroyed for actor in actors)
    assert lowered.vehicle_count == TARGET_COUNT
    assert lowered.conflict == {
        "error_type": "density_conflict",
        "target_vehicle_count": 1,
        "protected_actor_ids": [1, 2, 3],
    }


def test_total_world_target_accounts_for_protected_vehicles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Five owned plus one protected at target three removes three owned actors."""
    actors = [VehicleActor(id=index) for index in range(1, 7)]
    world = MultiWorld(actors)
    _runtime(monkeypatch, world)
    result = controller.maintain_traffic_once(
        _client(world),
        _request(TARGET_COUNT),
        {},
        controller.ControllerActors(frozenset(range(1, 7)), frozenset(range(2, 7))),
    )

    assert {
        "destroyed": [actor.id for actor in actors if actor.destroyed],
        "count": result.vehicle_count,
        "owned": result.owned_actor_ids,
        "conflict": result.conflict,
    } == {
        "destroyed": [4, 5, 6],
        "count": TARGET_COUNT,
        "owned": frozenset({2, 3}),
        "conflict": None,
    }


def test_stale_owned_ids_do_not_authorize_other_actor_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ownership is intersected with live IDs before planning reductions."""
    actors = [VehicleActor(id=1), VehicleActor(id=2)]
    world = MultiWorld(actors)
    _runtime(monkeypatch, world)
    result = controller.maintain_traffic_once(
        _client(world),
        _request(1),
        {},
        controller.ControllerActors(frozenset({1, 2, 999}), frozenset({2, 999})),
    )

    assert [actor.id for actor in actors if actor.destroyed] == [2]
    assert result.owned_actor_ids == frozenset()
    assert result.registered_actor_ids == frozenset({1})


@pytest.mark.parametrize("configure_manager", [False, True])
@pytest.mark.parametrize("reset", [False, True])
def test_synchronous_world_rejected_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch, *, configure_manager: bool, reset: bool
) -> None:
    """Synchronous density control cannot reset actors or change TM timing."""
    actor = VehicleActor()
    world = MultiWorld([actor], synchronous_mode=True)
    configurations = _runtime(monkeypatch, world)

    with pytest.raises(UnsupportedFeatureError, match="synchronous"):
        controller.maintain_traffic_once(
            _client(world), _request(0, reset=reset), {}, configure_manager=configure_manager
        )

    assert configurations == []
    assert actor.destroyed is False
    assert actor.autopilot_calls == []
    assert world.events == []


def test_async_reconfiguration_stops_if_world_changes_to_synchronous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every pass checks the active world even when TM was configured earlier."""
    world = MultiWorld([VehicleActor()])
    configurations = _runtime(monkeypatch, world)
    first = controller.maintain_traffic_once(_client(world), _request(1), {})
    assert [request.synchronous_mode for request in configurations] == [False]
    assert world.events == ["wait"]
    world.synchronous_mode = True

    with pytest.raises(UnsupportedFeatureError, match="synchronous"):
        controller.maintain_traffic_once(
            _client(world), _request(2), {}, first.registered_actor_ids, configure_manager=False
        )

    assert len(configurations) == 1
    assert world.events == ["wait"]


def test_synchronous_controller_failure_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsupported background timing produces an explicit inactive error state."""
    world = MultiWorld([], synchronous_mode=True)
    configurations = _runtime(monkeypatch, world)
    entered = threading.Event()
    workers: list[threading.Thread] = []

    def client(_request: object) -> CarlaClient:
        workers.append(threading.current_thread())
        entered.set()
        return _client(world)

    monkeypatch.setattr(controller, "_client", client)
    service = controller.InProcessTrafficControllerService()
    service.start(_request(1))
    try:
        assert entered.wait(1.0)
        workers[0].join(1.0)
        status = service.get_status()
        assert status.active is False
        assert status.error_type == "unsupported_feature"
        assert configurations == []
    finally:
        service.stop()
