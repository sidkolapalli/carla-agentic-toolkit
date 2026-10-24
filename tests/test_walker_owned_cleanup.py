"""Stop only known same-episode AI controllers before owned deletion and recovery."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit import adapter as adapter_module
from carla_agentic_toolkit import script_recovery
from carla_agentic_toolkit.errors import OwnershipError
from carla_agentic_toolkit.ownership import RunOwnership, cleanup_owned_actors
from tests.walker_lifecycle_helpers import CONTROLLER, WALKER, WalkerActor, WalkerCase, walker_case

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


@pytest.fixture
def case(monkeypatch: pytest.MonkeyPatch) -> Iterator[WalkerCase]:
    """Create the private journal at a genuine trusted-worker root."""
    with tempfile.TemporaryDirectory(prefix="carla-agentic-toolkit-script-") as root:
        result = walker_case(Path(root) / "owned-actors.json", monkeypatch)
        result.api.spawn_walkers(1)
        result.world.events.clear()
        yield result


@pytest.mark.parametrize("order", [(WALKER, CONTROLLER), (CONTROLLER, WALKER)])
@pytest.mark.parametrize("cached_destroy", [True, False])
def test_stop_precedes_any_pair_deletion_even_when_cached_destroy_is_false(
    case: WalkerCase, order: tuple[int, int], *, cached_destroy: bool
) -> None:
    """Stop all known AI controllers first, regardless of delete ordering."""
    case.world.cached_destroy = cached_destroy
    results = case.adapter.destroy_actors(order)
    assert all(result.destroyed for result in results)
    assert case.world.events[0] == ("stop", CONTROLLER)
    assert not case.world.actors


def test_stop_failure_retains_both_journal_ids_for_retry(case: WalkerCase) -> None:
    """An unacknowledged Stop keeps IDs durable until an actual retry succeeds."""
    case.world.failure = "stop"
    report = cleanup_owned_actors(case.adapter, RunOwnership(case.journal_path))
    _assert_failed_pair_cleanup(case, report)
    case.world.failure = ""
    retry = cleanup_owned_actors(case.adapter, RunOwnership(case.journal_path))
    assert not retry["failures"]
    assert RunOwnership(case.journal_path).actor_ids() == ()


def _assert_failed_pair_cleanup(case: WalkerCase, report: dict[str, object]) -> None:
    assert report["failures"]
    assert case.world.events == [("stop", CONTROLLER)]
    assert RunOwnership(case.journal_path).actor_ids() == (WALKER, CONTROLLER)


def test_episode_change_during_stop_prevents_all_deletion_and_release(case: WalkerCase) -> None:
    """A successful native Stop cannot authorize later new-episode deletion."""
    case.world.change_episode = "stop"
    report = cleanup_owned_actors(case.adapter, case.ownership)
    assert report["failures"]
    assert case.world.events == [("stop", CONTROLLER)]
    assert case.ownership.actor_ids() == (WALKER, CONTROLLER)


def test_wrong_explicit_lookup_identity_cannot_stop_or_delete_unowned_actor(
    case: WalkerCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected lookup result is not authority to mutate another controller."""
    foreign = WalkerActor(999, "controller.ai.walker", case.world)
    monkeypatch.setattr(
        case.world,
        "get_actors",
        lambda _ids=None: SimpleNamespace(
            find=lambda actor_id: (
                foreign if actor_id == CONTROLLER else case.world.actors.get(actor_id)
            )
        ),
    )
    report = cleanup_owned_actors(case.adapter, case.ownership)
    assert report["failures"]
    assert not case.world.events
    assert case.ownership.actor_ids() == (WALKER, CONTROLLER)


def test_authoritative_absence_releases_owned_ids_without_stop(
    case: WalkerCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Confirmed server absence does not require an unavailable controller handle."""
    case.world.actors.clear()
    monkeypatch.setattr(
        case.adapter,
        "apply_batch",
        lambda *_args, **_kwargs: {
            "responses": [{"actor_id": 0, "error": "unable to destroy actor: not found"}]
        },
    )
    report = cleanup_owned_actors(case.adapter, case.ownership)
    assert not report["failures"]
    assert not case.world.events
    assert case.ownership.actor_ids() == ()


@pytest.mark.parametrize("stop_fails", [False, True])
def test_trusted_worker_stops_controller_using_fresh_durable_reader(
    case: WalkerCase, monkeypatch: pytest.MonkeyPatch, *, stop_fails: bool
) -> None:
    """The actual trusted worker delegates Stop while preserving durable failures."""
    case.world.failure = "stop" if stop_fails else ""
    monkeypatch.setattr(adapter_module, "PythonCarlaAdapter", lambda **_kwargs: case.adapter)
    request: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 3999,
        "ownership_path": str(case.journal_path),
    }
    report, _adapter = script_recovery._worker_cleanup(request)  # noqa: SLF001
    assert bool(report["failures"]) is stop_fails
    events = case.world.events
    assert ("stop", CONTROLLER) in events
    remaining = RunOwnership(case.journal_path).actor_ids()
    assert remaining == ((WALKER, CONTROLLER) if stop_fails else ())


def test_replaced_world_worker_does_not_stop_or_destroy_old_ids(
    case: WalkerCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing changed-episode path issues no old-controller mutation."""
    case.world.id += 1
    monkeypatch.setattr(adapter_module, "PythonCarlaAdapter", lambda **_kwargs: case.adapter)
    request: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 3999,
        "ownership_path": str(case.journal_path),
    }
    report, _adapter = script_recovery._worker_cleanup(request)  # noqa: SLF001
    assert not report["failures"]
    assert not case.world.events


def test_failed_controller_journal_write_stops_before_known_id_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Known-ID rollback after a fatal journal write still acknowledges Stop first."""
    case = walker_case(tmp_path / "owned-actors.json", monkeypatch)
    add = case.ownership.add

    def fail_controller(ids: Iterable[int], *, world_id: int | None = None) -> None:
        if ids == (CONTROLLER,):
            message = "controller journal failed"
            raise OwnershipError(message)
        add(ids, world_id=world_id)

    monkeypatch.setattr(case.ownership, "add", fail_controller)
    result = case.api.spawn_walkers(1)
    assert result["ok"] is False
    events = case.world.events
    assert ("start", CONTROLLER) not in events
    assert events.index(("stop", CONTROLLER)) < events.index(("batch", CONTROLLER))


def test_controller_journal_rollback_stop_failure_stays_sticky(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fatal journal state retains a known controller whose Stop could not complete."""
    case = walker_case(tmp_path / "owned-actors.json", monkeypatch)
    add = case.ownership.add

    def fail_controller(ids: Iterable[int], *, world_id: int | None = None) -> None:
        if ids == (CONTROLLER,):
            case.world.failure = "stop"
            message = "controller journal failed"
            raise OwnershipError(message)
        add(ids, world_id=world_id)

    monkeypatch.setattr(case.ownership, "add", fail_controller)
    case.api.spawn_walkers(1)
    assert ("stop", CONTROLLER) in case.world.events
    assert ("batch", CONTROLLER) not in case.world.events
    assert case.ownership.creation_health.failures()
