"""Frame pacing rejects the wrong mode before sending native tick cues."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import experiment_scene
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.rpc_timeouts import RpcTimeoutPolicy
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_spectator_watch_api import ACTOR_ID, World

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld
    from carla_agentic_toolkit.models import JsonObject

INITIAL_FRAME = 40
WAIT_SECONDS = 2.5
EXECUTION_DEADLINE = 0.7


@dataclass
class Clock:
    """Advance wall time only when the simulator produces another frame."""

    elapsed: float = 0.0

    def now(self) -> float:
        """Return deterministic monotonic time."""
        return self.elapsed


@dataclass
class TimingWorld:
    """Model CARLA tick cues separately from asynchronous frame waits."""

    clock: Clock
    synchronous_mode: bool = False
    tick_cues: int = 0
    frame: int = INITIAL_FRAME
    waits: list[float] = field(default_factory=list)

    def get_settings(self) -> object:
        """Expose the native boolean mode rather than a truthy mock."""
        return SimpleNamespace(synchronous_mode=self.synchronous_mode)

    def tick(self) -> int:
        """Count every cue, including an unsafe asynchronous request."""
        self.tick_cues += 1
        self.frame += 1
        return self.frame

    def wait_for_tick(self, timeout: float) -> object:
        """Produce another asynchronous frame within the requested timeout."""
        self.waits.append(timeout)
        self.clock.elapsed += min(timeout, 0.25)
        self.frame += 1
        return SimpleNamespace(frame=self.frame)


@dataclass
class TimingCase:
    """Keep the facade, adapter, deadline policy and native world aligned."""

    clock: Clock
    world: TimingWorld
    adapter: PythonCarlaAdapter
    snapshots: RunSnapshots
    api: CarlaScriptApi
    sleep: Mock


@pytest.fixture
def timing_case(monkeypatch: pytest.MonkeyPatch) -> TimingCase:
    """Patch only native transport and time, retaining both application layers."""
    clock = Clock()
    world = TimingWorld(clock)
    policy = RpcTimeoutPolicy(clock=clock.now)
    adapter = PythonCarlaAdapter(rpc_timeout_policy=policy)
    client = cast("CarlaClient", SimpleNamespace(get_world=lambda: world))
    monkeypatch.setattr(adapter, "_client", lambda: client)
    monkeypatch.setattr(experiment_scene.time, "monotonic", clock.now)
    sleep = Mock()
    monkeypatch.setattr(experiment_scene.time, "sleep", sleep)
    snapshots = RunSnapshots()
    return TimingCase(clock, world, adapter, snapshots, CarlaScriptApi(adapter, snapshots), sleep)


@pytest.mark.parametrize("operation", ["tick", "tick_n", "tick_n_zero"])
def test_async_ticking_is_rejected_without_queued_cues(
    timing_case: TimingCase, operation: str
) -> None:
    """A later switch to synchronous mode must not spend old helper tick cues."""
    result = _tick(timing_case.api, operation)
    _assert_timing_failure(result, "tick_failed")
    assert "synchronous" in str(result["message"])
    timing_case.world.synchronous_mode = True
    assert timing_case.world.tick_cues == 0
    assert timing_case.world.frame == INITIAL_FRAME


@pytest.mark.parametrize("operation", ["tick", "tick_n", "tick_n_zero"])
def test_sync_ticking_advances_only_requested_frames(
    timing_case: TimingCase, operation: str
) -> None:
    """Synchronous owners retain the existing result and last-frame snapshot."""
    timing_case.world.synchronous_mode = True
    result = _tick(timing_case.api, operation)
    expected_count = {"tick": 1, "tick_n": 5, "tick_n_zero": 0}[operation]
    assert (timing_case.world.tick_cues, timing_case.world.waits) == (expected_count, [])
    if operation == "tick":
        assert result == {"frame": 41}
    else:
        assert result == {"frames": list(range(41, 41 + expected_count)), "count": expected_count}


def _tick(api: CarlaScriptApi, operation: str) -> JsonObject:
    if operation == "tick":
        return api.tick()
    return api.tick_n(0 if operation == "tick_n_zero" else 5)


def _assert_timing_failure(result: JsonObject, error_type: str) -> None:
    assert {"ok": result.get("ok"), "error_type": result.get("error_type")} == {
        "ok": False,
        "error_type": error_type,
    }


def _assert_no_native_timing(world: TimingWorld) -> None:
    assert (world.tick_cues, world.waits) == (0, [])


def test_async_wait_uses_frames_until_requested_wall_time(timing_case: TimingCase) -> None:
    """Waiting keeps native frame/navigation work active instead of sleeping."""
    assert timing_case.api.wait(WAIT_SECONDS) == {"waited_seconds": WAIT_SECONDS}
    assert timing_case.clock.elapsed == WAIT_SECONDS
    assert timing_case.world.waits == [1.0] * 7 + [0.75, 0.5, 0.25]
    assert timing_case.world.tick_cues == 0
    timing_case.sleep.assert_not_called()


@pytest.mark.parametrize(
    ("seconds", "expected"), [(0.0, 0.0), (-1.0, 0.0), (61.0, 60.0), (float("inf"), 60.0)]
)
def test_async_wait_preserves_existing_duration_clamp(
    timing_case: TimingCase, seconds: float, expected: float
) -> None:
    """The existing zero-to-sixty-second pacing contract stays intact."""
    assert timing_case.api.wait(seconds) == {"waited_seconds": expected}
    assert timing_case.clock.elapsed == expected
    assert timing_case.world.tick_cues == 0
    timing_case.sleep.assert_not_called()


@pytest.mark.parametrize("seconds", [0.0, 0.5])
def test_sync_wait_rejects_before_blocking(timing_case: TimingCase, seconds: float) -> None:
    """Even an empty wait must not suggest that a synchronous world can self-advance."""
    timing_case.world.synchronous_mode = True
    result = timing_case.api.wait(seconds)
    _assert_timing_failure(result, "wait_failed")
    _assert_no_native_timing(timing_case.world)
    timing_case.sleep.assert_not_called()


def test_wait_rechecks_mode_before_the_next_frame(
    timing_case: TimingCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mode switch during pacing cannot block the next synchronous frame."""
    wait = timing_case.world.wait_for_tick

    def switch_after_frame(timeout: float) -> object:
        snapshot = wait(timeout)
        timing_case.world.synchronous_mode = True
        return snapshot

    monkeypatch.setattr(timing_case.world, "wait_for_tick", switch_after_frame)
    result = timing_case.api.wait(1.0)
    assert result["ok"] is False
    assert timing_case.world.waits == [1.0]
    assert timing_case.world.tick_cues == 0


@pytest.mark.parametrize(
    ("absolute_deadline", "request_deadline"),
    [(EXECUTION_DEADLINE, None), (2.0, EXECUTION_DEADLINE)],
)
def test_wait_caps_each_native_timeout_by_remaining_execution_budget(
    timing_case: TimingCase,
    monkeypatch: pytest.MonkeyPatch,
    absolute_deadline: float,
    request_deadline: float | None,
) -> None:
    """The per-frame wait never resets or exceeds the worker's trusted deadline."""
    policy = RpcTimeoutPolicy(
        absolute_deadline=absolute_deadline,
        request_deadline=request_deadline,
        clock=timing_case.clock.now,
    )
    monkeypatch.setattr(timing_case.adapter, "_rpc_timeout_policy", policy)
    result = timing_case.api.wait(1.0)
    _assert_timing_failure(result, "wait_failed")
    assert result["retryable"] is False
    assert timing_case.world.waits == pytest.approx([0.7, 0.45, 0.2])
    assert (policy.absolute_deadline, policy.request_deadline) == (
        absolute_deadline,
        request_deadline,
    )


def test_wait_rejects_nan_without_native_frame_work(timing_case: TimingCase) -> None:
    """Replacing time.sleep must not turn its NaN rejection into empty success."""
    timing_case.sleep.side_effect = ValueError("Invalid value NaN")
    result = timing_case.api.wait(float("nan"))
    _assert_timing_failure(result, "wait_failed")
    assert timing_case.world.waits == []


def test_wait_refreshes_native_rpc_timeout_before_each_mode_read(
    timing_case: TimingCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cached client cap cannot outlive the remaining request or execution budget."""
    native_caps = [10.0]
    mode_caps: list[float] = []
    client = cast(
        "CarlaClient",
        SimpleNamespace(get_world=lambda: timing_case.world, set_timeout=native_caps.append),
    )
    monkeypatch.setattr(timing_case.adapter, "_client", lambda: client)
    policy = RpcTimeoutPolicy(absolute_deadline=EXECUTION_DEADLINE, clock=timing_case.clock.now)
    monkeypatch.setattr(timing_case.adapter, "_rpc_timeout_policy", policy)
    get_settings = timing_case.world.get_settings

    def guarded_settings() -> object:
        mode_caps.append(native_caps[-1])
        return get_settings()

    monkeypatch.setattr(timing_case.world, "get_settings", guarded_settings)
    assert timing_case.api.wait(1.0)["ok"] is False
    assert mode_caps == pytest.approx([0.7, 0.7, 0.45, 0.2])
    assert timing_case.world.waits == pytest.approx([0.7, 0.45, 0.2])


def test_tick_n_rechecks_mode_before_the_next_cue(
    timing_case: TimingCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An external mode switch cannot queue the remaining batch of native ticks."""
    timing_case.world.synchronous_mode = True
    tick = timing_case.world.tick

    def switch_after_tick() -> int:
        frame = tick()
        timing_case.world.synchronous_mode = False
        return frame

    monkeypatch.setattr(timing_case.world, "tick", switch_after_tick)
    result = timing_case.api.tick_n(5)
    assert result["ok"] is False
    assert timing_case.world.tick_cues == 1


@pytest.mark.parametrize("operation", ["tick", "tick_n", "wait"])
def test_mode_read_failure_is_structured_and_does_not_issue_native_work(
    timing_case: TimingCase, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """An unavailable mode cannot authorize either timing strategy."""
    monkeypatch.setattr(
        timing_case.world, "get_settings", Mock(side_effect=RuntimeError("offline"))
    )
    result = timing_case.api.wait(0.5) if operation == "wait" else _tick(timing_case.api, operation)
    assert result["ok"] is False
    assert "offline" in str(result["message"])
    _assert_no_native_timing(timing_case.world)


def test_watch_rejects_sync_before_actor_or_spectator_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong-mode watching must not even inspect or move the operator camera."""
    world = Mock(wraps=World())
    world.get_settings = Mock(return_value=SimpleNamespace(synchronous_mode=True))
    monkeypatch.setattr(experiment_scene, "carla_transform", lambda value: value)
    times = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(experiment_scene.time, "monotonic", lambda: next(times))
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(
        adapter, "_connected_client", cast("CarlaClient", SimpleNamespace(get_world=lambda: world))
    )
    result = CarlaScriptApi(adapter, RunSnapshots()).watch_actor(ACTOR_ID, seconds=0.5)
    assert result["ok"] is False
    assert result["error_type"] == "watch_actor_failed"
    world.get_actors.assert_not_called()
    world.get_spectator.assert_not_called()
    world.wait_for_tick.assert_not_called()


def test_native_wait_failure_returns_structured_error(
    timing_case: TimingCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asynchronous pacing must not report that a failed frame wait completed."""
    wait = Mock(side_effect=RuntimeError("frame timed out"))
    monkeypatch.setattr(timing_case.world, "wait_for_tick", wait)
    result = timing_case.api.wait(0.5)
    _assert_timing_failure(result, "wait_failed")
    assert "frame timed out" in str(result["message"])
    wait.assert_called_once_with(0.5)


def test_async_watch_restores_spectator_when_native_frame_wait_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preflight guard must preserve existing camera restoration on runtime failure."""
    world = World()
    monkeypatch.setattr(world, "wait_for_tick", Mock(side_effect=RuntimeError("frame timed out")))
    monkeypatch.setattr(experiment_scene, "carla_transform", lambda value: value)
    times = iter((0.0, 0.0))
    monkeypatch.setattr(experiment_scene.time, "monotonic", lambda: next(times))
    with pytest.raises(RuntimeError, match="frame timed out"):
        experiment_scene.watch_actor(
            cast("CarlaWorld", world), actor_id=ACTOR_ID, seconds=1.0, distance=8.0, height=4.0
        )
    assert world.spectator.transforms[-1] == world.spectator.initial
