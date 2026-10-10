"""AI navigation needs native seeding and a published transform before Start."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.rpc_timeouts import RpcTimeoutPolicy
from tests.walker_lifecycle_helpers import CONTROLLER, WALKER, WalkerCase, walker_case

if TYPE_CHECKING:
    from pathlib import Path

FRAME_CAP = 5.0


@pytest.fixture
def case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WalkerCase:
    """Bind a real facade to the native lifecycle fake."""
    return walker_case(tmp_path / "owned-actors.json", monkeypatch)


@pytest.mark.parametrize("seed", [None, 0, 19])
@pytest.mark.parametrize("synchronous", [False, True])
def test_seed_and_publication_precede_controller_start(
    case: WalkerCase, *, seed: int | None, synchronous: bool
) -> None:
    """Native seed and creating-client publication precede navigation Start."""
    case.world.synchronous = synchronous
    result = case.api.spawn_walkers(1, seed=seed)
    assert result.get("ok") is not False
    _assert_seed_order(case.world.events, seed)
    _assert_startup_order(case.world.events, synchronous=synchronous)
    assert case.world.published


def _events_for(events: list[tuple[str, object]], operations: set[str]) -> list[tuple[str, object]]:
    return [event for event in events if event[0] in operations]


def _assert_seed_order(events: list[tuple[str, object]], seed: int | None) -> None:
    expected_seed = [] if seed is None else [("seed", seed)]
    assert _events_for(events, {"seed"}) == expected_seed
    if seed is not None:
        navigation = _events_for(events, {"navigation"})
        assert events.index(("seed", seed)) < events.index(navigation[0])


def _assert_cue(cues: list[tuple[str, object]], *, synchronous: bool) -> None:
    cue = "tick" if synchronous else "wait"
    assert len(cues) == 1
    assert cues[0][0] == cue
    assert isinstance(cues[0][1], float)


def _assert_startup_order(events: list[tuple[str, object]], *, synchronous: bool) -> None:
    cues = _events_for(events, {"tick", "wait"})
    _assert_cue(cues, synchronous=synchronous)
    seconds = cues[0][1]
    assert isinstance(seconds, float)
    assert 0 < seconds <= FRAME_CAP
    assert (
        events.index(("spawn", CONTROLLER))
        < events.index(cues[0])
        < events.index(("start", CONTROLLER))
    )


@pytest.mark.parametrize("failure", ["barrier", "stale", "missing_walker"])
@pytest.mark.parametrize("synchronous", [False, True])
def test_unsettled_pair_never_starts_and_stops_before_rollback(
    case: WalkerCase, failure: str, *, synchronous: bool
) -> None:
    """Missing/stale publication cannot start an unobserved walker."""
    case.world.synchronous = synchronous
    case.world.failure = failure
    case.world.omit_walker = failure == "missing_walker"
    case.api.spawn_walkers(1)
    events = case.world.events
    assert ("start", CONTROLLER) not in events
    assert ("stop", CONTROLLER) in events
    assert events.index(("stop", CONTROLLER)) < _deletion_index(events, CONTROLLER)
    assert not case.world.actors


def _deletion_index(events: list[tuple[str, object]], actor_id: int) -> int:
    return next(
        i for i, event in enumerate(events) if event in (("destroy", actor_id), ("batch", actor_id))
    )


def test_changed_episode_at_barrier_preserves_known_pair_without_stop_or_delete(
    case: WalkerCase,
) -> None:
    """Replacement evidence cannot authorize Stop or deletion in a new episode."""
    case.world.change_episode = "barrier"
    case.api.spawn_walkers(1)
    assert not [
        event for event in case.world.events if event[0] in {"start", "stop", "destroy", "batch"}
    ]
    assert RunOwnership(case.journal_path).actor_ids() == (WALKER, CONTROLLER)


@pytest.mark.parametrize("synchronous", [False, True])
def test_barrier_uses_remaining_request_budget(
    case: WalkerCase, monkeypatch: pytest.MonkeyPatch, *, synchronous: bool
) -> None:
    """Explicit frame waits respect the unchanged parent execution deadline."""
    case.world.synchronous = synchronous
    monkeypatch.setattr(
        case.adapter,
        "_rpc_timeout_policy",
        RpcTimeoutPolicy(absolute_deadline=100.25, clock=lambda: 100.0),
    )
    case.api.spawn_walkers(1)
    cues = [item for item in case.world.events if item[0] in {"tick", "wait"}]
    assert len(cues) == 1
    assert cues[0][1] == pytest.approx(0.25)


def test_native_seed_failure_prevents_navigation_and_creation(case: WalkerCase) -> None:
    """A failed seeding RPC prevents every dependent native call."""
    case.world.failure = "seed"
    result = case.api.spawn_walkers(1, seed=0)
    assert result["ok"] is False
    assert case.world.events == [("seed", 0)]
    assert case.ownership.actor_ids() == ()


@pytest.mark.parametrize("synchronous", [False, True])
def test_barrier_recomputes_cap_after_final_episode_read(
    case: WalkerCase, monkeypatch: pytest.MonkeyPatch, *, synchronous: bool
) -> None:
    """An identity RPC's elapsed time cannot leave an earlier native wait cap stale."""
    case.world.synchronous = synchronous
    clock = {"now": 100.0, "spend": False}
    policy = RpcTimeoutPolicy(absolute_deadline=100.25, clock=lambda: clock["now"])
    monkeypatch.setattr(case.adapter, "_rpc_timeout_policy", policy)
    settings = case.world.get_settings
    require_episode = case.adapter._require_cleanup_episode  # noqa: SLF001

    def read_settings() -> object:
        clock["spend"] = True
        return settings()

    def delayed_identity(identity: int) -> None:
        require_episode(identity)
        if clock["spend"]:
            clock["now"] = 100.1
            clock["spend"] = False

    monkeypatch.setattr(case.world, "get_settings", read_settings)
    monkeypatch.setattr(case.adapter, "_require_cleanup_episode", delayed_identity)
    case.api.spawn_walkers(1)
    cues = [item for item in case.world.events if item[0] in {"tick", "wait"}]
    assert len(cues) == 1
    assert cues[0][1] == pytest.approx(0.15)


def test_failed_stop_during_setup_rollback_preserves_both_ids(
    case: WalkerCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop failure does not erase either member of a partially initialized pair."""
    case.world.failure = "speed"
    fail = case.world.fail

    def fail_stop_and_speed(stage: str) -> None:
        if stage == "stop":
            message = "controller Stop failed"
            raise RuntimeError(message)
        fail(stage)

    monkeypatch.setattr(case.world, "fail", fail_stop_and_speed)
    case.api.spawn_walkers(1)
    assert ("stop", CONTROLLER) in case.world.events
    assert not [event for event in case.world.events if event[0] in {"destroy", "batch"}]
    assert case.ownership.actor_ids() == (WALKER, CONTROLLER)
