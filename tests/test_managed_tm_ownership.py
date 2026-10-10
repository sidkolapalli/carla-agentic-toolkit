"""Managed TM hosting requires durable, current-process listener ownership."""

from __future__ import annotations

import os
import socket
import sys
from functools import partial
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from tests.managed_density_fakes import (
    DensityManager,
    density_spec,
)

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient


from tests.managed_tm_support import (
    EVIDENCE_KEY,
    RUN_ID,
    HostCase,
    _assert_async_setup,
    _assert_intent_provenance,
    _assert_owned_evidence,
    _assert_shutdown,
    _assert_unknown_refusal,
    _damage_shutdown,
    _direct_host,
    _durable_state,
    _evidence,
    _fail_phase_write,
    _lose_listener_on_reload_intent,
    _lose_listener_on_write_lookup,
    _manager_after_construct,
    _no_cleanup_frame,
    _no_tm_mutation,
    _no_world_mutation,
    _reject_open,
    _remove_authority,
    _replace_after_closed_evidence,
    _replace_on_mode,
)
from tests.managed_tm_support import (
    host_case as _host_case_fixture,
)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Hosting proof uses Linux procfs")
host_case = _host_case_fixture


def test_density_constructor_performs_no_tm_rpc(host_case: HostCase) -> None:
    """Binding the optional host cannot construct or alter a native manager."""
    assert host_case.client.manager is None
    assert not host_case.world.calls


def test_default_session_keeps_no_tm_contract(host_case: HostCase) -> None:
    """The default dedicated managed run remains independent of Traffic Manager."""
    session = ManagedSession(
        ExperimentSpec(), cast("CarlaClient", host_case.client), host_case.lease, RUN_ID
    )
    session.open()
    assert host_case.client.manager is None
    assert EVIDENCE_KEY not in host_case.lease.recovery_state
    assert session.close()["ok"] is True


def test_construction_intent_is_durable_before_native_tm_creation(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A killed or unresolved constructor must already have restrictive evidence."""
    observations: list[dict[str, object]] = []
    origin = host_case.world.id

    native = host_case.client.get_trafficmanager

    def inspect_intent(port: int) -> DensityManager:
        state = _durable_state(host_case)
        observations.append(cast("dict[str, object]", state[EVIDENCE_KEY]))
        return native(port)

    monkeypatch.setattr(host_case.client, "get_trafficmanager", inspect_intent)
    host_case.session.open()
    assert observations
    state = observations[0]
    assert state["phase"] == "construction_intent"
    _assert_intent_provenance(origin, state)


def test_owned_host_precedes_world_sync_and_acknowledges_shutdown(host_case: HostCase) -> None:
    """Only a newly proven local host may receive sync setters or shutdown."""
    host_case.session.open()
    calls = host_case.world.calls
    _assert_async_setup(host_case)
    setup_ticks = calls.count(("tick",))
    host_case.session.prepare_background()
    assert calls.count(("tick",)) == setup_ticks
    assert ("tm_mode", True) in calls
    _assert_owned_evidence(host_case)
    _assert_shutdown(host_case)


def test_remote_tm_is_not_configured_or_shut_down(host_case: HostCase) -> None:
    """A healthy remote handle and matching get_port are not local ownership."""
    host_case.client.local = False
    _reject_open(host_case)
    _no_tm_mutation(host_case)
    assert host_case.session.close()["ok"] is False
    _no_world_mutation(host_case)


def test_preexisting_same_pid_listener_cannot_be_adopted(host_case: HostCase) -> None:
    """Even a current-PID socket must be newly created by this construction."""
    host_case.client.manager = DensityManager(host_case.world, host_case.port)
    _reject_open(host_case)
    _no_tm_mutation(host_case)


@pytest.mark.parametrize("capability", ["get_port", "set_synchronous_mode", "shut_down"])
def test_missing_tm_capability_refuses_world_settings(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch, capability: str
) -> None:
    """A local socket alone does not prove the required native cleanup protocol."""
    _manager_after_construct(
        host_case, monkeypatch, lambda manager: monkeypatch.setattr(manager, capability, None)
    )
    _reject_open(host_case)
    _no_tm_mutation(host_case)


@pytest.mark.parametrize("reported_port", [True, "8000", -1, 1])
def test_noninteger_or_wrong_tm_port_cannot_authorize_setters(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch, reported_port: object
) -> None:
    """Native port evidence must be an exact integer matching the locked endpoint."""
    _manager_after_construct(
        host_case,
        monkeypatch,
        lambda manager: monkeypatch.setattr(manager, "get_port", lambda: reported_port),
    )
    _reject_open(host_case)
    _no_tm_mutation(host_case)


def test_native_constructor_failure_retains_unresolved_intent(host_case: HostCase) -> None:
    """A missing RPC response cannot become proof that no TM was constructed."""
    host_case.client.manager_error = "native constructor timeout"
    _reject_open(host_case)
    assert _evidence(host_case)["phase"] == "construction_intent"
    assert host_case.lease.recovery_state
    _no_tm_mutation(host_case)


@pytest.mark.parametrize("phase", ["construction_intent", "owned"])
def test_failed_host_journal_freezes_all_later_native_actions(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    """Persistence failure cannot be ignored before setters or world ApplySettings."""
    _fail_phase_write(host_case, monkeypatch, phase)
    _reject_open(host_case)
    _no_tm_mutation(host_case)
    assert host_case.session.close()["ok"] is False
    _no_tm_mutation(host_case)


def test_cooperative_port_lock_refuses_a_second_owner(host_case: HostCase) -> None:
    """The local-TM port is independently locked across simulator endpoint leases."""
    import fcntl  # noqa: PLC0415 -- Linux-only runtime contract.

    root = host_case.lease._root / "managed-tm"  # noqa: SLF001 -- exact test fixture root.
    root.mkdir(mode=0o700)
    descriptor = os.open(root / f"{host_case.port}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _reject_open(host_case)
        assert host_case.client.manager is None
    finally:
        os.close(descriptor)


def test_open_episode_change_after_async_ack_refuses_world_mutation(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manager construction cannot grant authority to a replacement episode."""
    _manager_after_construct(
        host_case, monkeypatch, partial(_replace_on_mode, monkeypatch=monkeypatch)
    )
    _reject_open(host_case)
    assert ("tm_shutdown",) not in host_case.world.calls


@pytest.mark.parametrize("failure", ["async_setter", "shutdown", "still_listening"])
def test_uncertain_shutdown_keeps_settings_and_lease_dirty(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Cleanup requires both native acknowledgements and closure of the owned listener."""
    host_case.session.open()
    manager = host_case.client.manager
    assert manager is not None
    _damage_shutdown(manager, monkeypatch, failure)
    host_case.world.calls.clear()
    report = host_case.session.close()
    assert report["ok"] is False
    assert report["failures"]
    _no_world_mutation(host_case)
    assert host_case.lease.recovery_state


def test_close_replacement_world_cannot_touch_original_or_remote_tm(host_case: HostCase) -> None:
    """An externally replaced episode cannot receive TM writes or actor/world cleanup."""
    host_case.session.open()
    host_case.world.id += 1
    host_case.world.calls.clear()
    assert host_case.session.close()["ok"] is False
    _no_tm_mutation(host_case)
    _no_world_mutation(host_case)


def test_multiple_new_current_pid_listeners_are_ambiguous(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A set of newly opened sockets is not proof of one exclusive TM listener."""
    extra = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def another_listener(_manager: DensityManager) -> None:
        extra.bind(("127.0.0.2", host_case.port))
        extra.listen()

    _manager_after_construct(host_case, monkeypatch, another_listener)
    try:
        with pytest.raises(RuntimeError):
            _direct_host(host_case).open()
        _no_tm_mutation(host_case)
    finally:
        extra.close()


def test_durable_construction_intent_does_not_authorize_a_replaced_episode(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The persistence boundary itself cannot hide a replacement before construction."""
    native = host_case.lease.mark_dirty
    replaced = False

    def persist(state: dict[str, object]) -> None:
        nonlocal replaced
        native(state)
        evidence = cast("dict[str, object]", state[EVIDENCE_KEY])
        if evidence["phase"] == "construction_intent" and not replaced:
            replaced = True
            host_case.world.id += 1

    monkeypatch.setattr(host_case.lease, "mark_dirty", persist)
    host = _direct_host(host_case)
    with pytest.raises(RuntimeError):
        host.open()
    assert host_case.client.manager is None
    assert _evidence(host_case)["phase"] == "construction_intent"
    assert host.failures()
    _no_tm_mutation(host_case)
    _no_world_mutation(host_case)
    _no_cleanup_frame(host_case)


def test_fields_are_detached_from_live_mutable_evidence(host_case: HostCase) -> None:
    """An enclosing shallow lease snapshot cannot retroactively observe later state edits."""
    host = _direct_host(host_case)
    host.open()
    before = host.fields()
    host.enable_sync(lambda: host_case.world.id)
    old = cast("dict[str, object]", before[EVIDENCE_KEY])
    assert old["sync_attempted"] is False
    assert _evidence(host_case)["sync_attempted"] is True


def test_acknowledged_rebind_preserves_original_host_proof(host_case: HostCase) -> None:
    """Only explicit successful map replacement can update the host's episode binding."""
    host = _direct_host(host_case)
    host.open()
    original = _evidence(host_case).copy()
    host_case.world.id += 1
    host_case.world.calls.clear()
    host.rebind_world(host_case.world.id, lambda: host_case.world.id)
    current = _evidence(host_case)
    assert current["world_id"] == host_case.world.id
    assert current["listener_inodes"] == original["listener_inodes"]
    assert current["host_start_time"] == original["host_start_time"]
    _no_tm_mutation(host_case)
    host.enable_sync(lambda: host_case.world.id)
    assert host.close(lambda: host_case.world.id)["ok"] is True


@pytest.mark.parametrize("failure", ["unacknowledged", "listener_closed", "process_changed"])
def test_rebind_refuses_lost_original_host_authority(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A cached handle or current world alone cannot authorize rebind or later setters."""
    host = _direct_host(host_case)
    host.open()
    _remove_authority(host_case, monkeypatch, failure)
    host_case.world.calls.clear()
    acknowledged = host_case.world.id + 1 if failure == "unacknowledged" else host_case.world.id
    with pytest.raises(RuntimeError):
        host.rebind_world(acknowledged, lambda: host_case.world.id)
    assert host.close(lambda: host_case.world.id)["ok"] is False
    _no_tm_mutation(host_case)


@pytest.mark.parametrize("failure", ["listener_closed", "process_changed"])
def test_later_read_only_owner_guard_freezes_lost_authority(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """Later runtime boundaries recheck origin rather than trusting the cached manager."""
    host = _direct_host(host_case)
    host.open()
    _remove_authority(host_case, monkeypatch, failure)
    host_case.world.calls.clear()
    with pytest.raises(RuntimeError):
        host.assert_owned(lambda: host_case.world.id)
    with pytest.raises(RuntimeError):
        host.enable_sync(lambda: host_case.world.id)
    assert host.close(lambda: host_case.world.id)["ok"] is False
    _no_tm_mutation(host_case)


def test_replacement_during_final_host_evidence_refuses_old_world_settings(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New host-close work cannot bridge a world change before original-settings ApplySettings."""
    host_case.session.open()
    _replace_after_closed_evidence(host_case, monkeypatch)
    host_case.world.calls.clear()
    report = host_case.session.close()
    assert report["ok"] is False
    assert report["settings_restored"] is False
    assert host_case.lease.recovery_state
    _no_world_mutation(host_case)


def test_episode_above_native_uint64_range_refuses_host_mutation(host_case: HostCase) -> None:
    """Unrepresentable episode identity cannot authorize local TM construction or setters."""
    host_case.world.id = 2**64
    host_case.session = ManagedSession(
        density_spec(host_case.port), cast("CarlaClient", host_case.client), host_case.lease, RUN_ID
    )
    _reject_open(host_case)
    _no_tm_mutation(host_case)
    assert host_case.client.manager is None


def test_startup_settings_lookup_requires_fresh_host_proof(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final settings lookup cannot invalidate previously proven host authority."""
    _lose_listener_on_write_lookup(host_case, monkeypatch)
    with pytest.raises(RuntimeError, match=r"listener|ownership|provenance"):
        host_case.session.open()
    _no_world_mutation(host_case)
    assert not [call for call in host_case.world.calls if call[0] == "reload"]


def test_pending_reload_requires_fresh_host_proof(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pending reload journal cannot authorize a native reload after listener loss."""
    _lose_listener_on_reload_intent(host_case, monkeypatch)
    with pytest.raises(RuntimeError, match=r"listener|ownership|provenance"):
        host_case.session.open()
    assert not [call for call in host_case.world.calls if call[0] == "reload"]
    reload = cast("dict[str, object]", host_case.lease.recovery_state["reload"])
    assert reload["phase"] == "pending"


def test_pending_spawn_requires_fresh_host_proof(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The durable intent remains pending if its write invalidates local-host proof."""
    host_case.session.open()
    native = host_case.lease.mark_dirty

    def persist(state: dict[str, object]) -> None:
        native(state)
        if state["spawn_intents"]:
            host_case.client.release_listener()

    monkeypatch.setattr(host_case.lease, "mark_dirty", persist)
    host_case.world.calls.clear()
    with pytest.raises(RuntimeError, match=r"listener|ownership|provenance"):
        host_case.session.prepare_background()
    assert host_case.lease.recovery_state["spawn_intents"]
    assert not [call for call in host_case.world.calls if call[0] == "spawn"]


def test_episode_change_during_host_shutdown_reports_unknown_identity(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pre-shutdown identity does not establish authority after its native calls."""
    host_case.session.open()
    manager = host_case.client.manager
    assert manager is not None
    native = manager.shut_down

    def replace() -> None:
        native()
        host_case.world.id += 1

    monkeypatch.setattr(manager, "shut_down", replace)
    host_case.world.calls.clear()
    _assert_unknown_refusal(host_case.session.close())
    _no_world_mutation(host_case)
