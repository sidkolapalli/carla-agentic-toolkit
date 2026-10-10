"""Density frame-wait failures stop maintenance without losing lifecycle evidence."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import traffic_controller_service as controller
from carla_agentic_toolkit import traffic_controller_step as step_module
from carla_agentic_toolkit import traffic_density as density
from carla_agentic_toolkit.errors import CarlaAdapterError, OwnershipError
from carla_agentic_toolkit.models import TrafficControllerStartRequest, TrafficDensityRequest
from tests.test_traffic_controller_service import ActorCollection, TrafficManager, VehicleActor

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld
    from carla_agentic_toolkit.models import TrafficControllerStatus

WORLD_ID = 17
ORIGINAL_ID = 11
SPAWNED_ID = 21
PREVIOUS_COUNT = 8
PREVIOUS_MOVING = 3
PREVIOUS_REVISION = 4
CURRENT_REVISION = 5
SECOND_ID = 12
RESET_WAIT_COUNT = 3


@dataclass
class FrameWorld:
    """Fail a selected native wait while tracking every later maintenance RPC."""

    actors: list[VehicleActor] = field(default_factory=list)
    id: int = WORLD_ID
    fail_wait: int | None = None
    error: Exception = field(default_factory=lambda: RuntimeError("frame delivery timed out"))
    events: list[str] = field(default_factory=list)
    wait_calls: int = 0
    on_failed_wait: Callable[[], None] | None = None

    def get_settings(self) -> SimpleNamespace:
        """Expose the actual asynchronous mode required by controller maintenance."""
        self.events.append("settings")
        return SimpleNamespace(synchronous_mode=False)

    def get_actors(self, _ids: object = None) -> ActorCollection:
        """Return surviving actors without manufacturing a fresh frame."""
        self.events.append("actors")
        return ActorCollection(tuple(actor for actor in self.actors if not actor.destroyed))

    def wait_for_tick(self, seconds: float) -> SimpleNamespace:
        """Reject a timeout without authorizing another observation or mutation attempt."""
        assert seconds == 1.0
        self.wait_calls += 1
        self.events.append(f"wait:{self.wait_calls}")
        if self.fail_wait is not None and self.wait_calls >= self.fail_wait:
            self.events.append("wait_failed")
            if self.on_failed_wait is not None:
                self.on_failed_wait()
            raise self.error
        return SimpleNamespace(frame=self.wait_calls)


@dataclass
class FrameCase:
    """Use actual density maintenance with a harmless native population fake."""

    world: FrameWorld
    client: CarlaClient
    service: controller.InProcessTrafficControllerService
    spawned: list[int]
    destroyed: list[tuple[tuple[int, ...], int | None]]


def _request(*, reset: bool = False, count: int = 1) -> TrafficControllerStartRequest:
    return TrafficControllerStartRequest(
        density=TrafficDensityRequest(vehicle_count=count, reset_existing=reset)
    )


def _case(monkeypatch: pytest.MonkeyPatch, *, fail_wait: int | None) -> FrameCase:
    world = FrameWorld(actors=[VehicleActor(id=ORIGINAL_ID)], fail_wait=fail_wait)
    client = cast("CarlaClient", SimpleNamespace(get_world=lambda: world))
    spawned: list[int] = []
    destroyed: list[tuple[tuple[int, ...], int | None]] = []
    service = controller.InProcessTrafficControllerService(
        on_spawn=spawned.extend,
        on_destroy=lambda ids, identity: destroyed.append((ids, identity)),
    )
    monkeypatch.setattr(controller, "_client", lambda _request: client)
    monkeypatch.setattr(step_module, "traffic_manager", lambda *_args: _manager(world))
    monkeypatch.setattr(
        step_module,
        "configure_traffic_manager",
        lambda *_args, **_kwargs: world.events.append("configure"),
    )
    monkeypatch.setattr(density, "populate_traffic_actors", lambda **_kwargs: _populate(world))
    return FrameCase(world, client, service, spawned, destroyed)


def _manager(world: FrameWorld) -> TrafficManager:
    world.events.append("traffic_manager")
    return TrafficManager()


def _populate(world: FrameWorld) -> tuple[list[int], list[object]]:
    world.events.append("spawn")
    world.actors.append(VehicleActor(id=SPAWNED_ID))
    return [SPAWNED_ID], []


def _no_rpcs_after_failure(world: FrameWorld) -> None:
    failure_index = world.events.index("wait_failed")
    assert world.events[failure_index + 1 :] == []


def _seed_previous_state(case: FrameCase, request: TrafficControllerStartRequest) -> None:
    previous = TrafficDensityRequest(vehicle_count=PREVIOUS_COUNT)
    case.service._state = replace(  # noqa: SLF001
        case.service._state,  # noqa: SLF001
        request=request,
        generation=1,
        revision=CURRENT_REVISION,
        vehicle_count=PREVIOUS_COUNT,
        moving_vehicle_count=PREVIOUS_MOVING,
        applied_revision=PREVIOUS_REVISION,
        applied_density=previous,
        registered_actor_ids=frozenset({ORIGINAL_ID}),
        owned_actor_ids=frozenset({ORIGINAL_ID}),
    )


def _assert_previous_observations(status: TrafficControllerStatus) -> None:
    assert {
        "count": status.vehicle_count,
        "moving": status.moving_vehicle_count,
        "applied_revision": status.applied_revision,
        "applied_target": status.applied_target_vehicle_count,
    } == {
        "count": PREVIOUS_COUNT,
        "moving": PREVIOUS_MOVING,
        "applied_revision": PREVIOUS_REVISION,
        "applied_target": PREVIOUS_COUNT,
    }


def _reset_progress(phase: str, *, destroyed: bool) -> dict[str, object]:
    return {
        "phase": phase,
        "world_id": WORLD_ID,
        "destroy_phase_completed": destroyed,
        "destroyed_actor_ids": [ORIGINAL_ID] if destroyed else [],
    }


def _joined_status(
    case: FrameCase, request: TrafficControllerStartRequest
) -> TrafficControllerStatus:
    case.service.start(request)
    worker = case.service._thread  # noqa: SLF001
    assert worker is not None
    worker.join(0.6)
    return case.service.get_status()


def _assert_fatal_status(status: TrafficControllerStatus, phase: str) -> None:
    assert (status.active, status.error_type) == (False, "frame_wait_failed")
    assert status.to_dict().get("frame_wait_phase") == phase
    assert "frame delivery timed out" in (status.last_error or "")


@pytest.mark.parametrize("native_error", [AttributeError, RuntimeError, TypeError, ValueError])
def test_density_wait_reports_native_failure_without_sleep(
    monkeypatch: pytest.MonkeyPatch, native_error: type[Exception]
) -> None:
    """The existing public density helper must surface one missed native frame."""
    case = _case(monkeypatch, fail_wait=1)
    case.world.error = native_error("frame delivery timed out")
    sleep = Mock()
    monkeypatch.setattr(density, "time", SimpleNamespace(sleep=sleep), raising=False)

    with pytest.raises(CarlaAdapterError, match="frame delivery timed out") as caught:
        density.wait_for_tick(cast("CarlaWorld", case.world))

    assert caught.value.details.get("error_type") == "frame_wait_failed"
    assert caught.value.__cause__ is case.world.error
    sleep.assert_not_called()


def test_density_wait_success_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """A delivered asynchronous frame preserves the existing helper return value."""
    case = _case(monkeypatch, fail_wait=None)

    assert density.wait_for_tick(cast("CarlaWorld", case.world)) is None
    assert case.world.wait_calls == 1


def test_maintenance_end_failure_retains_completed_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """Actual spawn/destruction observations cannot disappear on the final wait."""
    case = _case(monkeypatch, fail_wait=3)

    with pytest.raises(CarlaAdapterError, match="frame delivery timed out") as caught:
        step_module.maintain_traffic_once(case.client, _request(reset=True), {})

    result = getattr(caught.value, "completed_step", None)
    assert isinstance(result, step_module.TrafficControllerStep)
    _assert_completed_result(result)
    assert caught.value.details.get("phase") == "maintenance_end"
    _no_rpcs_after_failure(case.world)


def test_trim_only_end_wait_failure_retains_confirmed_deletion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal surplus trim is lifecycle evidence even without reset or new spawns."""
    case = _case(monkeypatch, fail_wait=1)
    case.world.actors.append(VehicleActor(id=SECOND_ID))
    actors = density.ControllerActors(
        registered=frozenset({ORIGINAL_ID, SECOND_ID}),
        owned=frozenset({ORIGINAL_ID, SECOND_ID}),
    )

    with pytest.raises(CarlaAdapterError, match="frame delivery timed out") as caught:
        step_module.maintain_traffic_once(case.client, _request(), {}, actors)

    result = getattr(caught.value, "completed_step", None)
    assert isinstance(result, step_module.TrafficControllerStep)
    assert (result.destroyed_actor_ids, result.spawned_actor_ids) == ((SECOND_ID,), ())
    assert result.owned_actor_ids == frozenset({ORIGINAL_ID})
    _no_rpcs_after_failure(case.world)


def _assert_completed_result(result: step_module.TrafficControllerStep) -> None:
    assert (result.spawned_actor_ids, result.destroyed_actor_ids) == ((SPAWNED_ID,), (ORIGINAL_ID,))
    assert (result.vehicle_count, result.owned_actor_ids) == (1, frozenset({SPAWNED_ID}))
    assert result.request.density.reset_existing is False


@pytest.mark.parametrize(
    ("failed_wait", "phase", "destroyed"),
    [(1, "reset_before_destroy", False), (2, "reset_after_destroy", True)],
)
def test_reset_wait_failures_report_only_confirmed_progress(
    monkeypatch: pytest.MonkeyPatch, failed_wait: int, phase: str, *, destroyed: bool
) -> None:
    """Neither reset wait can fall through into TM configuration or population."""
    case = _case(monkeypatch, fail_wait=failed_wait)

    with pytest.raises(CarlaAdapterError, match="frame delivery timed out") as caught:
        step_module.maintain_traffic_once(case.client, _request(reset=True), {})

    assert caught.value.details.get("reset_progress") == _reset_progress(phase, destroyed=destroyed)
    assert getattr(caught.value, "completed_step", None) is None
    assert "traffic_manager" not in case.world.events
    _no_rpcs_after_failure(case.world)


@pytest.mark.parametrize(
    ("failed_wait", "phase"),
    [(1, "reset_before_destroy"), (2, "reset_after_destroy"), (3, "maintenance_end")],
)
def test_controller_stops_after_one_missed_frame(
    monkeypatch: pytest.MonkeyPatch, failed_wait: int, phase: str
) -> None:
    """The real worker exits after the failing pass without reconnecting or retrying."""
    case = _case(monkeypatch, fail_wait=failed_wait)
    try:
        status = _joined_status(case, _request(reset=True))
        _assert_fatal_status(status, phase)
        assert case.world.wait_calls == failed_wait
        _no_rpcs_after_failure(case.world)
    finally:
        case.service.stop()


def test_final_wait_failure_commits_standalone_ownership_callbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without native observers, completed spawns and deletes still reach the journal hooks."""
    case = _case(monkeypatch, fail_wait=3)
    try:
        status = _joined_status(case, _request(reset=True))
        _assert_completed_ownership(case, status)
        _assert_fatal_status(status, "maintenance_end")
    finally:
        case.service.stop()


def _assert_completed_ownership(case: FrameCase, status: TrafficControllerStatus) -> None:
    assert (case.spawned, case.destroyed) == ([SPAWNED_ID], [((ORIGINAL_ID,), WORLD_ID)])
    assert (status.owned_actor_ids, status.vehicle_count, status.applied_revision) == (
        (SPAWNED_ID,),
        1,
        1,
    )
    assert case.service._state.request.density.reset_existing is False  # noqa: SLF001


@pytest.mark.parametrize(
    ("failed_wait", "phase", "destroyed"),
    [(1, "reset_before_destroy", False), (2, "reset_after_destroy", True)],
)
def test_partial_reset_preserves_previous_observations_and_reports_deletions(
    monkeypatch: pytest.MonkeyPatch, failed_wait: int, phase: str, *, destroyed: bool
) -> None:
    """Reset progress is separate from a completed density step and its prior counts."""
    case = _case(monkeypatch, fail_wait=failed_wait)
    _seed_previous_state(case, _request(reset=True))

    with pytest.raises(CarlaAdapterError, match="frame delivery timed out"):
        case.service._control_once(case.client, 1)  # noqa: SLF001

    status = case.service.get_status()
    _assert_previous_observations(status)
    assert status.to_dict().get("reset_progress") == _reset_progress(phase, destroyed=destroyed)
    _assert_reset_lifecycle(case, status, destroyed=destroyed)
    _no_rpcs_after_failure(case.world)


def _assert_reset_lifecycle(
    case: FrameCase, status: TrafficControllerStatus, *, destroyed: bool
) -> None:
    assert case.destroyed == ([((ORIGINAL_ID,), WORLD_ID)] if destroyed else [])
    assert status.owned_actor_ids == (() if destroyed else (ORIGINAL_ID,))
    assert case.service._state.request.density.reset_existing is not destroyed  # noqa: SLF001


def test_partial_reset_retains_actor_without_destroy_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The completed destroy phase releases only IDs with positive native acknowledgements."""
    case = _case(monkeypatch, fail_wait=2)
    monkeypatch.setattr(case.world.actors[0], "destroy", Mock(return_value=False))
    case.world.actors.append(VehicleActor(id=SECOND_ID))
    _seed_previous_state(case, _request(reset=True))
    case.service._state = replace(  # noqa: SLF001
        case.service._state,  # noqa: SLF001
        registered_actor_ids=frozenset({ORIGINAL_ID, SECOND_ID}),
        owned_actor_ids=frozenset({ORIGINAL_ID, SECOND_ID}),
    )

    with pytest.raises(CarlaAdapterError, match="frame delivery timed out"):
        case.service._control_once(case.client, 1)  # noqa: SLF001

    status = case.service.get_status()
    _assert_previous_observations(status)
    assert status.owned_actor_ids == (ORIGINAL_ID,)
    assert case.destroyed == [((SECOND_ID,), WORLD_ID)]
    assert status.to_dict().get("reset_progress") == {
        **_reset_progress("reset_after_destroy", destroyed=True),
        "destroyed_actor_ids": [SECOND_ID],
    }


def test_partial_reset_reports_entry_episode_after_failed_wait_changes_proxy_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never tag acknowledged original-episode deletion with an ID read after timeout."""
    case = _case(monkeypatch, fail_wait=2)
    _seed_previous_state(case, _request(reset=True))
    case.world.on_failed_wait = lambda: setattr(case.world, "id", WORLD_ID + 1)

    with pytest.raises(CarlaAdapterError, match="frame delivery timed out"):
        case.service._control_once(case.client, 1)  # noqa: SLF001

    status = case.service.get_status()
    assert case.destroyed == [((ORIGINAL_ID,), WORLD_ID)]
    assert status.to_dict().get("reset_progress") == _reset_progress(
        "reset_after_destroy", destroyed=True
    )


@pytest.mark.parametrize("stale_state", ["generation", "stopping"])
def test_final_wait_stale_state_keeps_lifecycle_callbacks_without_publishing_step(
    monkeypatch: pytest.MonkeyPatch, stale_state: str
) -> None:
    """The exception's completed step preserves existing stale-generation lifecycle policy."""
    case = _case(monkeypatch, fail_wait=3)
    _seed_previous_state(case, _request(reset=True))

    def invalidate_generation() -> None:
        changes = {"generation": 2} if stale_state == "generation" else {"stopping": True}
        case.service._state = replace(case.service._state, **changes)  # noqa: SLF001

    case.world.on_failed_wait = invalidate_generation
    with pytest.raises(CarlaAdapterError, match="frame delivery timed out"):
        case.service._control_once(case.client, 1)  # noqa: SLF001

    _assert_previous_observations(case.service.get_status())
    assert (case.spawned, case.destroyed) == ([SPAWNED_ID], [((ORIGINAL_ID,), WORLD_ID)])
    assert case.service._state.request.density.reset_existing is True  # noqa: SLF001


def test_partial_reset_cannot_consume_newer_desired_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirmed deletion may publish progress but cannot discard a newer reset revision."""
    case = _case(monkeypatch, fail_wait=2)
    _seed_previous_state(case, _request(reset=True))

    def newer_revision() -> None:
        case.service._state = replace(  # noqa: SLF001
            case.service._state,  # noqa: SLF001
            request=_request(reset=True, count=2),
            revision=CURRENT_REVISION + 1,
        )

    case.world.on_failed_wait = newer_revision
    with pytest.raises(CarlaAdapterError, match="frame delivery timed out"):
        case.service._control_once(case.client, 1)  # noqa: SLF001

    status = case.service.get_status()
    _assert_previous_observations(status)
    assert (status.target_vehicle_count, status.desired_revision) == (2, CURRENT_REVISION + 1)
    assert case.service._state.request.density.reset_existing is True  # noqa: SLF001
    assert case.destroyed == [((ORIGINAL_ID,), WORLD_ID)]


@pytest.mark.parametrize("stale_state", ["generation", "stopping"])
def test_partial_reset_stale_state_keeps_callbacks_without_publishing_progress(
    monkeypatch: pytest.MonkeyPatch, stale_state: str
) -> None:
    """Acknowledged deletion outlives stale work, while newer state remains untouched."""
    case = _case(monkeypatch, fail_wait=2)
    _seed_previous_state(case, _request(reset=True))

    def invalidate_generation() -> None:
        changes = {"generation": 2} if stale_state == "generation" else {"stopping": True}
        case.service._state = replace(case.service._state, **changes)  # noqa: SLF001

    case.world.on_failed_wait = invalidate_generation
    with pytest.raises(CarlaAdapterError, match="frame delivery timed out"):
        case.service._control_once(case.client, 1)  # noqa: SLF001

    status = case.service.get_status()
    _assert_previous_observations(status)
    assert status.to_dict().get("reset_progress") is None
    assert case.destroyed == [((ORIGINAL_ID,), WORLD_ID)]
    assert case.service._state.request.density.reset_existing is True  # noqa: SLF001


def test_wait_failure_lifecycle_callback_error_remains_fatal_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected destruction acknowledgement cannot be normalized into a timing error."""
    case = _case(monkeypatch, fail_wait=2)

    def reject_release(_ids: tuple[int, ...], _identity: int | None) -> None:
        message = "lifecycle journal release failed"
        raise OwnershipError(message)

    case.service._on_destroy = reject_release  # noqa: SLF001
    try:
        status = _joined_status(case, _request(reset=True))
        assert (status.active, status.error_type) == (False, "actor_ownership_failed")
        assert status.last_error == "lifecycle journal release failed"
    finally:
        case.service.stop()


@pytest.mark.parametrize("callback_error", [CarlaAdapterError, RuntimeError, AttributeError])
@pytest.mark.parametrize(
    ("callback_kind", "failed_wait", "phase"),
    [("destroy", 2, "reset_after_destroy"), ("spawn", 3, "maintenance_end")],
)
def test_wait_failure_remains_fatal_when_lifecycle_evidence_callback_fails(
    monkeypatch: pytest.MonkeyPatch,
    callback_error: type[Exception],
    callback_kind: str,
    failed_wait: int,
    phase: str,
) -> None:
    """A failed journal callback cannot replace the native timeout with a retryable error."""
    case = _case(monkeypatch, fail_wait=failed_wait)

    def reject_evidence(*_args: object) -> None:
        message = "lifecycle evidence unavailable"
        raise callback_error(message)

    callback = "_on_destroy" if callback_kind == "destroy" else "_on_spawn"
    monkeypatch.setattr(case.service, callback, reject_evidence)
    try:
        status = _joined_status(case, _request(reset=True))
        _assert_fatal_status(status, phase)
        assert "lifecycle evidence unavailable" in (status.last_error or "")
        assert case.world.wait_calls == failed_wait
        _no_rpcs_after_failure(case.world)
    finally:
        case.service.stop()


def test_ordinary_controller_error_type_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-wait failure still uses the existing generic controller error policy."""
    case = _case(monkeypatch, fail_wait=None)

    def fail_configuration(*_args: object, **_kwargs: object) -> None:
        case.service._stop_event.set()  # noqa: SLF001
        message = "ordinary manager error"
        raise CarlaAdapterError(message)

    monkeypatch.setattr(step_module, "configure_traffic_manager", fail_configuration)
    try:
        status = _joined_status(case, _request())
        assert (status.active, status.error_type) == (False, "controller_error")
        assert status.last_error == "ordinary manager error"
        assert status.to_dict().get("frame_wait_phase") is None
    finally:
        case.service.stop()


def test_successful_reset_control_commits_completed_observations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful reset and final wait still produce the established density result."""
    case = _case(monkeypatch, fail_wait=None)

    result = step_module.maintain_traffic_once(case.client, _request(reset=True), {})

    _assert_completed_result(result)
    assert case.world.wait_calls == RESET_WAIT_COUNT
