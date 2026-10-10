"""Managed cleanup verifies server destruction without trusting cached handles."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import experiment_replay
from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_managed_session import FakeWorld

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaClient


@dataclass
class CleanupCase:
    """Retain the real journal and a deliberately unreliable cached actor."""

    session: ManagedSession
    world: FakeWorld
    lease: SimulatorLease
    actor: SimpleNamespace
    client: CarlaClient


@pytest.fixture
def cleanup_case(tmp_path: Path) -> Iterator[CleanupCase]:
    """Own an actor whose cached destroy result cannot prove server state."""
    world = FakeWorld()
    client = cast("CarlaClient", SimpleNamespace(get_world=lambda: world))
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(ExperimentSpec(), client, lease, "authoritative")
        session.open()
        actor = SimpleNamespace(
            id=11,
            type_id="vehicle.test",
            attributes={"role_name": "managed:authoritative:ego"},
            destroy=Mock(return_value=False),
        )
        world.actors.append(actor)
        session.own(cast("CarlaActor", actor), controller="ego")
        yield CleanupCase(session, world, lease, actor, client)


def _batch(monkeypatch: pytest.MonkeyPatch, error: str = "") -> Mock:
    actor_id = 0 if error else 11
    batch = Mock(return_value={"responses": [{"actor_id": actor_id, "error": error}]})
    monkeypatch.setattr(experiment_replay, "apply_batch", batch)
    return batch


def _assert_non_ticking_batch(batch: Mock, client: CarlaClient) -> None:
    batch.assert_called_once_with(
        client, [{"action": "destroy_actor", "actor_id": 11}], do_tick=False
    )


def test_cleanup_uses_server_result_not_cached_destroy(
    cleanup_case: CleanupCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty CARLA response error proves success even when cached destroy is False."""
    batch = _batch(monkeypatch)
    frame = cleanup_case.world.frame

    report = cleanup_case.session.close()

    assert report["ok"] is True
    cleanup_case.actor.destroy.assert_not_called()
    _assert_non_ticking_batch(batch, cleanup_case.client)
    assert cleanup_case.world.frame == frame
    assert not cleanup_case.lease.recovery_state


@pytest.mark.parametrize("error", ["", "unable to destroy actor: not found"])
def test_recovery_verifies_absent_cached_actor_with_server(
    cleanup_case: CleanupCase, monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    """A new client's empty actor cache cannot bypass authoritative cleanup."""
    batch = _batch(monkeypatch, error)
    cleanup_case.world.actors.clear()

    report = ManagedSession.recover(cleanup_case.client, cleanup_case.lease, ExperimentSpec())

    assert report["ok"] is True
    _assert_non_ticking_batch(batch, cleanup_case.client)
    assert not cleanup_case.lease.recovery_state


@pytest.mark.parametrize(
    ("error", "clean"),
    [("unable to destroy actor: not found", True), ("permission denied", False)],
)
def test_cleanup_distinguishes_confirmed_absence_from_server_failure(
    cleanup_case: CleanupCase,
    monkeypatch: pytest.MonkeyPatch,
    error: str,
    *,
    clean: bool,
) -> None:
    """Only exact authoritative absence releases the recovery barrier."""
    batch = _batch(monkeypatch, error)

    report = cleanup_case.session.close()

    assert report["ok"] is clean
    assert bool(cleanup_case.lease.recovery_state) is not clean
    _assert_non_ticking_batch(batch, cleanup_case.client)
    cleanup_case.actor.destroy.assert_not_called()


@pytest.mark.parametrize("changed", ["type", "role"])
def test_changed_actor_identity_refuses_authoritative_destroy(
    cleanup_case: CleanupCase, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    """Server mutation remains forbidden when a cached actor no longer matches ownership."""
    batch = _batch(monkeypatch)
    if changed == "type":
        cleanup_case.actor.type_id = "vehicle.other"
    else:
        cleanup_case.actor.attributes["role_name"] = "unrelated"

    assert cleanup_case.session.close()["ok"] is False
    batch.assert_not_called()
    cleanup_case.actor.destroy.assert_not_called()


def test_world_replacement_during_destroy_prevents_settings_write(
    cleanup_case: CleanupCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A replacement during a destroy RPC cannot receive old-episode settings."""
    batch = _batch(monkeypatch)

    def replace_world(*_args: object, **_kwargs: object) -> dict[str, object]:
        cleanup_case.world.id += 1
        return {"responses": [{"actor_id": 11, "error": ""}]}

    batch.side_effect = replace_world
    apply = Mock(wraps=cleanup_case.world.apply_settings)
    monkeypatch.setattr(cleanup_case.world, "apply_settings", apply)

    report = cleanup_case.session.close()

    assert report["ok"] is False
    assert report["settings_restored"] is False
    apply.assert_not_called()
    assert cleanup_case.lease.recovery_state
