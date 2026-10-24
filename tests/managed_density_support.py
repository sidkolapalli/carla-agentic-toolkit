"""Durable native-like session cases and focused owned-density assertions."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit import experiment_replay
from carla_agentic_toolkit.managed_creation import OwnedActor
from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.managed_density_fakes import DensityClient, DensityWorld, density_spec, unused_port

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest

    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from tests.managed_density_fakes import DensityActor

DEFAULT_TARGET = 2
WORK_BUDGET = 4


@contextmanager
def running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    count: int = 2,
    interval: int = 1,
    start: bool = True,
) -> Iterator[tuple[ManagedSession, DensityWorld, DensityClient, SimulatorLease]]:
    """Use the actual durable lease and authoritative non-ticking response boundary."""
    world, port = DensityWorld(), unused_port()
    client = DensityClient(world)
    monkeypatch.setattr(experiment_replay, "apply_batch", _destroy_batch)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = ManagedSession(
            density_spec(port, count=count, interval=interval),
            cast("CarlaClient", client),
            lease,
            "density-test",
        )
        try:
            if start:
                session.open()
            yield session, world, client, lease
        finally:
            try:
                session.close()
            finally:
                client.release_listener()
                _release_fixture_lock(session)


def _release_fixture_lock(session: ManagedSession) -> None:
    host = session.background.host
    if host is not None:
        host._release_port_lock()  # noqa: SLF001 -- Exact fake-owned lock after assertions.


def _destroy_batch(
    client: DensityClient, commands: list[dict[str, object]], *, do_tick: bool
) -> dict[str, object]:
    assert do_tick is False
    return {
        "responses": [_destroy_response(client, cast("int", item["actor_id"])) for item in commands]
    }


def _destroy_response(client: DensityClient, identity: int) -> dict[str, object]:
    client.world.calls.append(("destroy", identity))
    existing = next((actor for actor in client.world.actors if actor.id == identity), None)
    if existing is None:
        return {"actor_id": 0, "error": "unable to destroy actor: not found"}
    client.world.actors.remove(existing)
    return {"actor_id": identity, "error": None}


def prepare_background(session: ManagedSession) -> None:
    """Preserve the public-entrypoint test used before the optional hook existed."""
    callback = getattr(session, "prepare_background", None)
    if callback is not None:
        callback()


def backgrounds(session: ManagedSession) -> list[OwnedActor]:
    """Inspect durable creation ownership instead of adopting scene inventory."""
    records = cast("list[dict[str, Any]]", session.lease.recovery_state.get("actors", []))
    return [OwnedActor(**record) for record in records if record["controller"] == "managed-density"]


def _traffic_port(session: ManagedSession) -> int:
    config = session.spec.background_density
    assert config is not None
    return config.traffic_manager_port


def _spawn_attempts(world: DensityWorld) -> int:
    return sum(call[0] == "spawn" for call in world.calls)


def _owned_ids(session: ManagedSession) -> set[int]:
    return {record.actor_id for record in backgrounds(session)}


def _destroyed_ids(world: DensityWorld) -> set[object]:
    return {call[1] for call in world.calls if call[0] == "destroy"}


def _assert_population_and_attempts(
    session: ManagedSession, world: DensityWorld, *, count: int, attempts: int
) -> None:
    assert len(backgrounds(session)) == count
    assert _spawn_attempts(world) == attempts


def _assert_external_untouched(
    session: ManagedSession, world: DensityWorld, external: DensityActor
) -> None:
    assert external.autopilot_calls == []
    assert external.id not in _owned_ids(session)
    assert ("destroy", external.id) not in world.calls


def _assert_cleanup_order(world: DensityWorld, identities: list[int], port: int) -> None:
    shutdown = world.calls.index(("tm_shutdown",))
    assert all(
        world.calls.index(("autopilot", identity, False, port))
        < world.calls.index(("destroy", identity))
        < shutdown
        for identity in identities
    )
    assert shutdown < world.calls.index(("settings", False))


def _assert_no_autopilot(world: DensityWorld, *, enabled: bool) -> None:
    assert not any(call[0] == "autopilot" and call[2] is enabled for call in world.calls)


def _assert_uncertain_registration(
    session: ManagedSession, world: DensityWorld, lease: SimulatorLease
) -> None:
    assert len(backgrounds(session)) == 1
    _assert_no_autopilot(world, enabled=True)
    assert lease.recovery_state["actors"]
    assert session.close()["ok"] is False
    assert world.settings["synchronous_mode"] is True


def _assert_host_cleanup_refused(
    report: dict[str, object], world: DensityWorld, lease: SimulatorLease
) -> None:
    assert report["ok"] is False
    assert report["settings_restored"] is False
    _assert_no_autopilot(world, enabled=False)
    assert world.settings["synchronous_mode"] is True
    assert lease.recovery_state


def _assert_removal_budget(world: DensityWorld, previous: set[int]) -> None:
    removed = _destroyed_ids(world)
    assert len(removed) == WORK_BUDGET
    assert removed <= previous
