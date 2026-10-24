"""Density work is bounded by the sole frame owner and never adopts scene actors."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import managed_tm
from carla_agentic_toolkit.managed_session import ManagedSession, SessionInvariantError
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.managed_density_fakes import (
    DensityActor,
    DensityBlueprint,
    DensityClient,
    DensityWorld,
    transform,
)
from tests.managed_density_support import (
    DEFAULT_TARGET,
    WORK_BUDGET,
    _assert_cleanup_order,
    _assert_external_untouched,
    _assert_host_cleanup_refused,
    _assert_population_and_attempts,
    _assert_removal_budget,
    _assert_uncertain_registration,
    _destroyed_ids,
    _owned_ids,
    _spawn_attempts,
    _traffic_port,
    backgrounds,
    prepare_background,
    running,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import SimpleNamespace

    from carla_agentic_toolkit.carla_protocols import CarlaBlueprint, CarlaClient

EXTERNAL_ID = 99


def test_open_proves_async_host_before_world_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Host ownership is acknowledged before any world synchronization mutation."""
    with running(tmp_path, monkeypatch) as (_session, world, _client, lease):
        first_settings = world.calls.index(("settings", True))
        assert world.calls.index(("tm_mode", False)) < first_settings
        host = cast("dict[str, object]", lease.recovery_state["managed_traffic_manager"])
        assert host["phase"] == "owned"
        assert not backgrounds(_session)


def test_default_disabled_never_constructs_manager(tmp_path: Path) -> None:
    """Existing disabled callers never construct a TM or populate background actors."""
    world, client = DensityWorld(), DensityClient(DensityWorld())
    client.world = world
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(ExperimentSpec(), cast("CarlaClient", client), lease, "disabled")
        session.open()
        session.step()
        assert session.close()["ok"] is True
    assert not any(str(call[0]).startswith("tm_") for call in world.calls)


def test_interrupt_after_host_open_uses_verified_cleanup_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proven native host cannot fall between the journal and the cleanup lifecycle."""
    with running(tmp_path, monkeypatch, start=False) as (session, world, _client, lease):
        original = session.background.open

        def interrupt_after_open() -> None:
            original()
            raise KeyboardInterrupt

        monkeypatch.setattr(session.background, "open", interrupt_after_open)
        with pytest.raises(KeyboardInterrupt):
            session.open()
        report = session.close()
        assert report["ok"] is True
        assert not lease.recovery_state
        assert ("tm_shutdown",) in world.calls
        assert report["settings_restored"] is True


def test_initial_and_owner_step_population_are_each_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A target of 100 cannot turn one owner boundary into 100 spawn attempts."""
    with running(tmp_path, monkeypatch, count=100) as (session, world, _client, _lease):
        prepare_background(session)
        assert len(backgrounds(session)) == WORK_BUDGET
        before_ticks, before_waits = world.tick_calls, world.wait_calls
        session.step()
        assert len(backgrounds(session)) == WORK_BUDGET * 2
        assert (world.tick_calls - before_ticks, world.wait_calls - before_waits) == (1, 0)


def test_density_journals_each_raw_id_before_autopilot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Raw owned IDs and completed intents precede native registration."""
    with running(tmp_path, monkeypatch) as (session, world, _client, lease):
        original = DensityActor.set_autopilot

        def inspect(actor: DensityActor, enabled: bool, port: int) -> None:  # noqa: FBT001
            owned = cast("list[dict[str, object]]", lease.recovery_state["actors"])
            record = next(item for item in owned if item["actor_id"] == actor.id)
            assert record["controller"] == "managed-density"
            assert record["protected"] is False
            assert lease.recovery_state["spawn_intents"] == []
            original(actor, enabled, port)

        monkeypatch.setattr(DensityActor, "set_autopilot", inspect)
        prepare_background(session)
        assert len(backgrounds(session)) == DEFAULT_TARGET
        assert all(
            actor.autopilot_calls == [(True, _traffic_port(session))] for actor in world.actors
        )


def test_only_missing_owned_backgrounds_are_replenished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only authoritative deletion of this controller's actors can release its IDs."""
    with running(tmp_path, monkeypatch) as (session, world, _client, _lease):
        prepare_background(session)
        records = backgrounds(session)
        assert len(records) == DEFAULT_TARGET
        removed_id = records[0].actor_id
        world.actors[:] = [a for a in world.actors if a.id != removed_id]
        external = DensityActor(EXTERNAL_ID, world, attributes={"role_name": "external"})
        world.actors.append(cast("SimpleNamespace", external))
        session.step()
        assert len(backgrounds(session)) == DEFAULT_TARGET
        _assert_external_untouched(session, world, external)


def test_collision_none_resolves_intent_and_attempt_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An acknowledged collision consumes a bounded attempt but leaves no intent."""
    with running(tmp_path, monkeypatch, count=100) as (session, world, _client, lease):
        world.spawn_results = [None] * 12
        before = _spawn_attempts(world)
        prepare_background(session)
        assert _spawn_attempts(world) - before == WORK_BUDGET
        assert lease.recovery_state["spawn_intents"] == []
        assert session.close()["ok"] is True


def test_ordinary_spawn_none_does_not_claim_confirmed_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only try_spawn's documented None result can resolve a no-actor intent."""
    with running(tmp_path, monkeypatch) as (session, world, _client, lease):
        world.spawn_results = [None]
        blueprint = DensityBlueprint(attributes={"base_type": "car", "role_name": "fixture"})
        with pytest.raises(SessionInvariantError, match=r"spawn|actor"):
            session.spawn_actor(
                cast("CarlaBlueprint", blueprint),
                transform(),
                role_name="fixture",
                controller="fixture-owner",
            )
        assert lease.recovery_state["spawn_intents"]
        assert session.close()["ok"] is False


def test_unknown_spawn_reply_stays_dirty_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown native results retain quarantine without a second creation call."""
    with running(tmp_path, monkeypatch) as (session, world, _client, lease):
        world.spawn_results = [RuntimeError("lost spawn reply")]
        with pytest.raises(RuntimeError, match="lost spawn reply"):
            prepare_background(session)
        assert len([call for call in world.calls if call[0] == "spawn"]) == 1
        assert session.close()["ok"] is False
        assert lease.recovery_state["spawn_intents"]


@pytest.mark.parametrize("drift", ["episode", "frame"])
def test_invalid_owner_boundary_does_not_begin_population(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    """Invalid episode or owner-frame evidence refuses synchronization and creation."""
    with running(tmp_path, monkeypatch) as (session, world, _client, _lease):
        if drift == "episode":
            world.id += 1
        else:
            world.frame += 1
        with pytest.raises(SessionInvariantError):
            prepare_background(session)
        assert not any(call[0] == "spawn" for call in world.calls)
        assert ("tm_mode", True) not in world.calls


def test_cleanup_unregisters_only_owned_then_closes_host_before_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native unregister, authoritative delete, and host close precede restoration."""
    with running(tmp_path, monkeypatch) as (session, world, _client, lease):
        prepare_background(session)
        ids = [record.actor_id for record in backgrounds(session)]
        assert ids
        report = session.close()
        assert report["ok"] is True
        _assert_cleanup_order(world, ids, _traffic_port(session))
        assert not lease.recovery_state


def test_failed_autopilot_still_cleans_known_id_and_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected registration retains its known ID for guarded cleanup."""

    def reject_autopilot(*_args: object) -> None:
        message = "autopilot rejected"
        raise RuntimeError(message)

    with running(tmp_path, monkeypatch) as (session, world, _client, _lease):
        monkeypatch.setattr(
            DensityActor,
            "set_autopilot",
            reject_autopilot,
        )
        with pytest.raises(RuntimeError, match="autopilot rejected"):
            prepare_background(session)
        assert len(backgrounds(session)) == 1
        assert session.close()["ok"] is False
        assert any(call[0] == "destroy" for call in world.calls)


def test_density_waits_for_explicit_fixture_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixture setup owns its clock before background synchronization is enabled."""
    with running(tmp_path, monkeypatch) as (session, world, _client, _lease):
        session.step()
        assert ("tm_mode", True) not in world.calls
        assert backgrounds(session) == []
        prepare_background(session)
        assert ("tm_mode", True) in world.calls


def test_changed_host_during_fixture_setup_refuses_owner_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Async fixture setup still requires the original proven local host."""
    with running(tmp_path, monkeypatch) as (session, world, client, lease):
        assert client.manager is not None
        client.manager.release_listener()
        before = world.calls.copy()
        with pytest.raises(RuntimeError, match=r"ownership|provenance|listener"):
            session.step()
        assert world.calls == before
        assert lease.recovery_state


def test_interval_skips_population_work_on_other_owner_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only configured owner boundaries perform bounded population work."""
    with running(tmp_path, monkeypatch, count=100, interval=3) as (session, world, _client, _lease):
        prepare_background(session)
        assert len(backgrounds(session)) == WORK_BUDGET
        for _index in range(2):
            session.step()
        assert len(backgrounds(session)) == WORK_BUDGET
        session.step()
        _assert_population_and_attempts(
            session, world, count=WORK_BUDGET * 2, attempts=WORK_BUDGET * 2
        )


def test_fixture_remains_protected_when_density_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional density cannot register or destroy a protected fixture actor."""
    with running(tmp_path, monkeypatch) as (session, world, _client, _lease):
        blueprint = DensityBlueprint(attributes={"base_type": "car", "role_name": "fixture"})
        fixture = session.spawn_actor(
            cast("CarlaBlueprint", blueprint),
            transform(),
            role_name="fixture",
            controller="fixture-owner",
        )
        prepare_background(session)
        assert len(backgrounds(session)) == DEFAULT_TARGET
        with pytest.raises(SessionInvariantError, match="protected"):
            session.destroy_actor(fixture.id)
        assert cast("DensityActor", fixture).autopilot_calls == []
        assert ("destroy", fixture.id) not in world.calls


def test_unknown_creation_freezes_later_owner_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catching an unknown outcome cannot authorize another native spawn or tick."""
    with running(tmp_path, monkeypatch) as (session, world, _client, _lease):
        world.spawn_results = [RuntimeError("lost spawn reply")]
        with pytest.raises(RuntimeError, match="lost spawn reply"):
            prepare_background(session)
        before = world.calls.copy()
        with pytest.raises(RuntimeError, match=r"uncertain|unresolved|frozen"):
            session.step()
        assert world.calls == before


def test_cancel_before_first_snapshot_still_unregisters_original_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing publication is not evidence that acknowledged registration is absent."""
    with running(tmp_path, monkeypatch) as (session, world, _client, _lease):
        prepare_background(session)
        ids = [record.actor_id for record in backgrounds(session)]
        assert ids
        original_snapshot = world.get_snapshot

        def unpublished() -> SimpleNamespace:
            snapshot = original_snapshot()
            snapshot.find = lambda _identity: None
            return snapshot

        monkeypatch.setattr(world, "get_snapshot", unpublished)
        session.close()
        port = _traffic_port(session)
        assert all(("autopilot", identity, False, port) in world.calls for identity in ids)


def test_same_frame_absence_does_not_release_newly_created_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner must publish a later frame before interpreting snapshot absence."""
    with running(tmp_path, monkeypatch) as (session, world, _client, lease):
        prepare_background(session)
        original = world.get_snapshot
        ids = _owned_ids(session)
        assert len(ids) == DEFAULT_TARGET

        def unpublished() -> SimpleNamespace:
            snapshot = original()
            snapshot.find = lambda _identity: None
            return snapshot

        monkeypatch.setattr(world, "get_snapshot", unpublished)
        session.step()
        retained = cast("list[dict[str, object]]", lease.recovery_state["actors"])
        assert {record["actor_id"] for record in retained} == ids
        assert not _destroyed_ids(world)
        assert _spawn_attempts(world) == DEFAULT_TARGET


def test_missing_removal_has_its_own_four_command_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removal and creation budgets are independent even with eight missing actors."""
    target = WORK_BUDGET * 2
    with running(tmp_path, monkeypatch, count=target) as (session, world, _client, _lease):
        prepare_background(session)
        session.step()
        previous = _owned_ids(session)
        assert len(previous) == target
        world.actors.clear()
        session.step()
        _assert_removal_budget(world, previous)
        _assert_population_and_attempts(session, world, count=target, attempts=WORK_BUDGET * 3)


@pytest.mark.parametrize("changed", ["listener", "port", "process"])
def test_changed_host_proof_after_spawn_refuses_autopilot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    """A returned raw ID is retained, but old TM proof cannot authorize registration."""
    with running(tmp_path, monkeypatch) as (session, world, client, lease):
        assert client.manager is not None
        manager = client.manager

        def change_proof() -> None:
            if changed == "listener":
                manager.release_listener()
            elif changed == "port":
                manager.port += 1
            else:
                monkeypatch.setattr(managed_tm, "process_record", lambda _pid: {})

        world.before_spawn = change_proof
        with pytest.raises(RuntimeError, match=r"ownership|provenance|listener|port"):
            prepare_background(session)
        _assert_uncertain_registration(session, world, lease)


def test_changed_host_before_close_refuses_unregister_and_settings_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expired host proof forbids unregister calls and settings restoration."""
    with running(tmp_path, monkeypatch) as (session, world, client, lease):
        prepare_background(session)
        assert client.manager is not None
        client.manager.release_listener()
        report = session.close()
        _assert_host_cleanup_refused(report, world, lease)
