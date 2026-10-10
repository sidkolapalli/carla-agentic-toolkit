"""Native-like owned-listener fixtures and shared managed-TM assertions."""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.managed_control_io import read_control
from carla_agentic_toolkit.managed_creation import OwnedActor, SpawnIntent
from carla_agentic_toolkit.managed_liveness import process_record
from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_tm import ManagedTrafficManager
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.managed_density_fakes import (
    DensityActor,
    DensityClient,
    DensityManager,
    DensityWorld,
    density_spec,
    unused_port,
)
from tests.managed_density_support import _destroy_batch

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path
    from types import SimpleNamespace

    from carla_agentic_toolkit.carla_protocols import CarlaClient

RUN_ID = "owned-tm-run"
EVIDENCE_KEY = "managed_traffic_manager"
SETTINGS_WRITE_LOOKUP = 2


@dataclass
class HostCase:
    """Retain a real lease and test-owned local socket, never a CARLA server."""

    world: DensityWorld
    client: DensityClient
    lease: SimulatorLease
    session: ManagedSession
    port: int


@pytest.fixture
def host_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[HostCase]:
    """Exercise real session lifecycle against explicitly owned local socket fakes."""
    world = DensityWorld()
    client = DensityClient(world)
    monkeypatch.setattr("carla_agentic_toolkit.experiment_replay.apply_batch", _destroy_batch)
    port = unused_port()
    with SimulatorLease("127.0.0.1", 39001, state_root=tmp_path / "state") as lease:
        session = ManagedSession(density_spec(port), cast("CarlaClient", client), lease, RUN_ID)
        try:
            yield HostCase(world, client, lease, session, port)
        finally:
            with suppress(RuntimeError, OSError, ValueError):
                session.close()
            client.release_listener()
            _release_test_lock(session)


def _release_test_lock(session: ManagedSession) -> None:
    host = getattr(session, "_traffic_manager_host", None)
    release = getattr(host, "_release_port_lock", None)
    if callable(release):
        release()


def _evidence(case: HostCase) -> dict[str, object]:
    value = case.lease.recovery_state[EVIDENCE_KEY]
    assert isinstance(value, dict)
    return cast("dict[str, object]", value)


def _no_world_mutation(case: HostCase) -> None:
    assert not [call for call in case.world.calls if call[0] in {"settings", "spawn"}]


def _no_tm_mutation(case: HostCase) -> None:
    assert not [call for call in case.world.calls if call[0] in {"tm_mode", "tm_shutdown"}]


def _no_cleanup_frame(case: HostCase) -> None:
    assert not [call for call in case.world.calls if call[0] in {"tick", "wait"}]


def _reject_open(case: HostCase) -> None:
    with pytest.raises(RuntimeError):
        case.session.open()
    _no_world_mutation(case)


def _manager_after_construct(
    case: HostCase, monkeypatch: pytest.MonkeyPatch, change: Callable[[DensityManager], None]
) -> None:
    native = case.client.get_trafficmanager

    def construct(port: int) -> DensityManager:
        manager = native(port)
        change(manager)
        return manager

    monkeypatch.setattr(case.client, "get_trafficmanager", construct)


def _assert_intent_provenance(origin: int, state: dict[str, object]) -> None:
    assert (state["world_id"], state["run_id"], state["host_pid"]) == (
        origin,
        RUN_ID,
        os.getpid(),
    )
    identity = process_record(os.getpid())
    assert state["host_start_time"] == identity["start_ticks"]
    assert state["host_boot_id"] == identity["boot_id"]


def _assert_async_setup(case: HostCase) -> None:
    calls = case.world.calls
    assert ("tm_construct", case.port) in calls
    assert calls.index(("tm_mode", False)) < calls.index(("settings", True))
    assert ("tm_mode", True) not in calls


def _assert_shutdown(case: HostCase) -> None:
    calls = case.world.calls
    assert case.session.close()["ok"] is True
    assert calls.index(("tm_shutdown",)) < calls.index(("settings", False))
    assert case.client.manager is not None
    assert case.client.manager.listener is None


def _assert_owned_evidence(case: HostCase) -> None:
    state = _evidence(case)
    assert state["world_id"] == case.world.id
    assert state["port"] == case.port
    assert state["listener_inodes"]
    assert state["sync_attempted"] is True


def _fail_phase_write(case: HostCase, monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    native = case.lease.mark_dirty

    def persist(state: dict[str, object]) -> None:
        host = state.get(EVIDENCE_KEY)
        if isinstance(host, dict) and host.get("phase") == phase:
            message = "Host evidence volume failed."
            raise OSError(message)
        native(state)

    monkeypatch.setattr(case.lease, "mark_dirty", persist)


def _replace_on_mode(manager: DensityManager, *, monkeypatch: pytest.MonkeyPatch) -> None:
    native = manager.set_synchronous_mode

    def change(enabled: bool) -> None:  # noqa: FBT001 -- native TM setter signature.
        native(enabled)
        manager.world.id += 1

    monkeypatch.setattr(manager, "set_synchronous_mode", change)


def _damage_shutdown(
    manager: DensityManager, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    if failure == "async_setter":
        manager.mode_error = "async setter timeout"
    elif failure == "shutdown":
        manager.shutdown_error = "shutdown timeout"
    else:
        monkeypatch.setattr(manager, "shut_down", lambda: None)


def _direct_host(case: HostCase) -> ManagedTrafficManager:
    def persist() -> None:
        case.lease.mark_dirty(
            {"kind": "managed", "run_id": RUN_ID, "world_id": case.world.id, **host.fields()}
        )

    host = ManagedTrafficManager(
        cast("CarlaClient", case.client),
        port=case.port,
        world_id=case.world.id,
        run_id=RUN_ID,
        state_root=case.lease.state_root,
        persist=persist,
    )
    case.session._traffic_manager_host = host  # noqa: SLF001 -- fixture-owned lock teardown.
    return host


def _closed_state(case: HostCase) -> dict[str, object]:
    host = _direct_host(case)
    host.open()
    assert host.close(lambda: case.world.id)["ok"] is True
    return case.lease.recovery_state.copy()


def _remove_authority(case: HostCase, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    if failure == "listener_closed":
        case.client.release_listener()
    elif failure == "process_changed":
        record = process_record(os.getpid())
        record["start_ticks"] = -1
        monkeypatch.setattr("carla_agentic_toolkit.managed_tm.process_record", lambda _pid: record)


def _durable_state(case: HostCase) -> dict[str, object]:
    return read_control(case.lease._state_path)  # noqa: SLF001 -- exact fixture-owned journal.


def _recovery_without_host(case: HostCase) -> dict[str, object]:
    return {
        "kind": "managed",
        "run_id": RUN_ID,
        "world_id": case.world.id,
        "world_generation": "original",
        "actors": [],
        "settings": case.world.settings.copy(),
        "spawn_journal_version": 1,
        "spawn_intents": [],
    }


def _replace_after_closed_evidence(case: HostCase, monkeypatch: pytest.MonkeyPatch) -> None:
    native = case.lease.mark_dirty
    replaced = False

    def persist(state: dict[str, object]) -> None:
        nonlocal replaced
        native(state)
        host = cast("dict[str, object]", state[EVIDENCE_KEY])
        if host["phase"] == "closed" and not replaced:
            replaced = True
            case.world.id += 1

    monkeypatch.setattr(case.lease, "mark_dirty", persist)


def _recovery_record(case: HostCase, source: str, controller: str) -> dict[str, object]:
    role = f"managed:{RUN_ID}:recorded-actor"
    actor = DensityActor(11, case.world, attributes={"role_name": role})
    case.world.actors.append(cast("SimpleNamespace", actor))
    state = _recovery_without_host(case)
    if source == "actors":
        state[source] = [
            asdict(OwnedActor(actor.id, actor.type_id, role, controller, protected=False))
        ]
    else:
        plan = SpawnIntent(
            1,
            case.world.id,
            actor.type_id,
            role,
            controller,
            protected=False,
            transform={
                "location": {"x": 0.0, "y": 0.0, "z": 0.0},
                "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
            },
            attach_to=None,
            actor_id=actor.id,
        )
        state[source] = [asdict(plan)]
    return state


def _assert_quarantined_recovery(case: HostCase, report: dict[str, object], before: bytes) -> None:
    assert report["ok"] is False
    assert _durable_bytes(case) == before
    _no_tm_mutation(case)
    _no_world_mutation(case)
    _no_cleanup_frame(case)


def _durable_bytes(case: HostCase) -> bytes:
    return case.lease._state_path.read_bytes()  # noqa: SLF001 -- fixture-owned control file.


def _assert_unknown_refusal(report: dict[str, object]) -> None:
    assert report["ok"] is False
    assert report["world_replaced"] is None
    assert report["world_identity_checked"] is False
    assert report["settings_restored"] is False


def _lose_listener_on_write_lookup(case: HostCase, monkeypatch: pytest.MonkeyPatch) -> None:
    native = case.world.get_settings
    reads = 0

    def lookup() -> object:
        nonlocal reads
        settings = native()
        reads += 1
        if reads == SETTINGS_WRITE_LOOKUP:
            case.client.release_listener()
        return settings

    monkeypatch.setattr(case.world, "get_settings", lookup)


def _lose_listener_on_reload_intent(case: HostCase, monkeypatch: pytest.MonkeyPatch) -> None:
    native = case.lease.mark_dirty

    def persist(state: dict[str, object]) -> None:
        native(state)
        reload = cast("dict[str, object]", state.get("reload", {}))
        if reload.get("phase") == "pending":
            case.client.release_listener()

    monkeypatch.setattr(case.lease, "mark_dirty", persist)
