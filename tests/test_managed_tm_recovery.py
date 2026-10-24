"""Managed TM recovery requires complete, acknowledged local-host closure evidence."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.managed_tm import failures_from_state
from tests.managed_density_fakes import (
    density_spec,
)

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient


from tests.managed_tm_support import (
    EVIDENCE_KEY,
    RUN_ID,
    HostCase,
    _assert_quarantined_recovery,
    _closed_state,
    _durable_bytes,
    _durable_state,
    _evidence,
    _no_cleanup_frame,
    _no_tm_mutation,
    _no_world_mutation,
    _recovery_record,
    _recovery_without_host,
)
from tests.managed_tm_support import (
    host_case as _host_case_fixture,
)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Hosting proof uses Linux procfs")
host_case = _host_case_fixture


def test_recovery_never_reconstructs_an_unresolved_host(host_case: HostCase) -> None:
    """Unknown server registration after worker death requires explicit investigation."""
    state: dict[str, object] = {
        "kind": "managed",
        "run_id": RUN_ID,
        "world_id": host_case.world.id,
        "world_generation": "original",
        "actors": [],
        "spawn_journal_version": 1,
        "spawn_intents": [],
        "settings": host_case.world.settings.copy(),
        EVIDENCE_KEY: {
            "schema_version": 1,
            "run_id": RUN_ID,
            "world_id": host_case.world.id,
            "host_pid": os.getpid(),
            "host_start_time": "123",
            "port": host_case.port,
            "phase": "construction_intent",
            "listener_inodes": [],
            "sync_attempted": False,
            "async_restored": False,
            "shutdown_acknowledged": False,
            "listener_closed": False,
            "failures": [],
        },
    }
    host_case.lease.mark_dirty(state)
    report = ManagedSession.recover(
        cast("CarlaClient", host_case.client), host_case.lease, density_spec(host_case.port)
    )
    assert report["ok"] is False
    assert host_case.lease.recovery_state
    _no_tm_mutation(host_case)
    _no_world_mutation(host_case)
    assert host_case.client.manager is None
    _no_cleanup_frame(host_case)


@pytest.mark.parametrize("field", ["run_id", "world_id"])
def test_closed_host_evidence_must_belong_to_enclosing_run_and_episode(
    host_case: HostCase, field: str
) -> None:
    """A prior run's successful shutdown cannot authorize a different recovery."""
    state = _closed_state(host_case)
    state[field] = "other-run" if field == "run_id" else host_case.world.id + 1
    assert failures_from_state(state)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("world_id", -1),
        ("host_pid", 0),
        ("host_start_time", -1),
        ("port", 65_536),
        ("listener_inodes", [0]),
        ("unknown_proof", True),
    ],
)
def test_closed_evidence_rejects_invalid_provenance(
    host_case: HostCase, field: str, value: object
) -> None:
    """Verified closure still requires canonical typed ownership provenance."""
    state = _closed_state(host_case)
    host = cast("dict[str, object]", state[EVIDENCE_KEY])
    host[field] = value
    assert failures_from_state(state)


def test_closed_host_evidence_is_valid_only_after_verified_shutdown(host_case: HostCase) -> None:
    """Fresh bounded disk evidence distinguishes proven shutdown from worker death."""
    state = _closed_state(host_case)
    assert failures_from_state(state) == []
    durable = _durable_state(host_case)
    assert failures_from_state(durable) == []


def test_density_recovery_missing_host_proof_keeps_lease_dirty(host_case: HostCase) -> None:
    """A declared density owner cannot recover using the legacy no-TM shortcut."""
    state = _recovery_without_host(host_case)
    state["background_density"] = {"configured_vehicle_count": 1}
    host_case.lease.mark_dirty(state)
    report = ManagedSession.recover(
        cast("CarlaClient", host_case.client), host_case.lease, density_spec(host_case.port)
    )
    assert report["ok"] is False
    assert report["failures"]
    assert host_case.lease.recovery_state
    _no_tm_mutation(host_case)
    _no_world_mutation(host_case)
    _no_cleanup_frame(host_case)


def test_legacy_recovery_without_density_or_host_keeps_no_tm_compatibility(
    host_case: HostCase,
) -> None:
    """Older default managed journals did not declare any hosted Traffic Manager."""
    host_case.lease.mark_dirty(_recovery_without_host(host_case))
    report = ManagedSession.recover(
        cast("CarlaClient", host_case.client), host_case.lease, ExperimentSpec()
    )
    assert report["ok"] is True
    assert host_case.lease.recovery_state == {}
    assert host_case.client.manager is None
    _no_tm_mutation(host_case)


@pytest.mark.parametrize("source", ["actors", "spawn_intents"])
def test_density_owned_record_cannot_hide_missing_host_evidence(
    host_case: HostCase, source: str
) -> None:
    """An explicit controller declaration is restrictive even without top-level TM keys."""
    host_case.lease.mark_dirty(_recovery_record(host_case, source, "managed-density"))
    before = _durable_bytes(host_case)
    report = ManagedSession.recover(
        cast("CarlaClient", host_case.client), host_case.lease, ExperimentSpec()
    )
    _assert_quarantined_recovery(host_case, report, before)
    assert not [call for call in host_case.world.calls if call[0] in {"destroy", "autopilot"}]
    assert host_case.client.manager is None


@pytest.mark.parametrize("source", ["actors", "spawn_intents"])
def test_fixture_only_recovery_records_preserve_no_tm_compatibility(
    host_case: HostCase, source: str
) -> None:
    """A genuine pre-TM fixture owner still has its known actor cleaned authoritatively."""
    host_case.lease.mark_dirty(_recovery_record(host_case, source, "fixture"))
    report = ManagedSession.recover(
        cast("CarlaClient", host_case.client), host_case.lease, ExperimentSpec()
    )
    assert report["ok"] is True
    assert host_case.lease.recovery_state == {}
    assert ("destroy", 11) in host_case.world.calls
    assert host_case.client.manager is None


@pytest.mark.parametrize("episode", [2**63 + 1, 2**64 - 1])
def test_full_uint64_episode_closed_host_proof_allows_actual_recovery(
    host_case: HostCase, monkeypatch: pytest.MonkeyPatch, episode: int
) -> None:
    """Native uint64 episode IDs are not constrained by the signed OS integer width."""
    host_case.world.id = episode - 1
    host_case.session = ManagedSession(
        density_spec(host_case.port), cast("CarlaClient", host_case.client), host_case.lease, RUN_ID
    )
    host_case.session.open()
    native = host_case.lease.mark_clean
    monkeypatch.setattr(host_case.lease, "mark_clean", lambda: None)
    assert host_case.session.close()["ok"] is True
    monkeypatch.setattr(host_case.lease, "mark_clean", native)
    assert _evidence(host_case)["world_id"] == episode
    report = ManagedSession.recover(
        cast("CarlaClient", host_case.client), host_case.lease, ExperimentSpec()
    )
    assert report["ok"] is True
    assert host_case.lease.recovery_state == {}
