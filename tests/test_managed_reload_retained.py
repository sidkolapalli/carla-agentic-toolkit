"""Reload cannot discard pre-existing native ownership or unresolved creation evidence."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.managed_creation import CreationOptions
from carla_agentic_toolkit.simulator_lease import RecoveryRequiredError, SimulatorLease
from tests.managed_reload_helpers import (
    ORIGINAL_WORLD_ID,
    ReloadClient,
    ReloadWorld,
    _case,
    _session,
)

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaBlueprint
    from carla_agentic_toolkit.managed_session import ManagedSession


def _retain_evidence(session: ManagedSession, evidence: str) -> None:
    if evidence == "owned":
        actor = SimpleNamespace(
            id=33, type_id="static.prop.trafficcone01", attributes={"role_name": "fixture"}
        )
        with pytest.raises(RuntimeError, match="not running"):
            session.own(cast("CarlaActor", actor), controller="fixture")
        return
    transform = SimpleNamespace(
        location=SimpleNamespace(x=1.0, y=2.0, z=3.0),
        rotation=SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0),
    )
    plan = session.creation.begin(
        cast("CarlaBlueprint", SimpleNamespace(id="static.prop.trafficcone01")),
        transform,
        CreationOptions(role_name="fixture", controller="fixture"),
    )
    if evidence == "returned_intent":
        session.creation.returned(plan, 33)


def _assert_no_reload(client: ReloadClient, old: ReloadWorld, new: ReloadWorld) -> None:
    assert client.calls == []
    assert old.events == []
    assert new.events == []


def _assert_original_world(session: ManagedSession, old: ReloadWorld, settings: object) -> None:
    assert old.settings == settings
    assert session.world_id == ORIGINAL_WORLD_ID


@pytest.mark.parametrize("evidence", ["owned", "unknown_intent", "returned_intent"])
def test_retained_creation_evidence_refuses_reload_without_rebinding(
    tmp_path: Path, evidence: str
) -> None:
    """Known IDs and unknown outcomes remain quarantined in their original episode."""
    old, new, client = _case()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        _retain_evidence(session, evidence)
        saved = deepcopy(lease.recovery_state)
        marker = next(tmp_path.glob("*.json"))
        saved_bytes = marker.read_bytes()
        settings = deepcopy(old.settings)
        old.events.clear()

        with pytest.raises(RuntimeError, match=r"retained|ownership|intent"):
            session.open()

        _assert_no_reload(client, old, new)
        _assert_original_world(session, old, settings)
        assert lease.recovery_state == saved
        assert marker.read_bytes() == saved_bytes
        assert session.creation.fields()["spawn_intents"] == saved["spawn_intents"]
    with (
        pytest.raises(RecoveryRequiredError),
        SimulatorLease("localhost", 3000, state_root=tmp_path),
    ):
        pytest.fail("Retained creation evidence cannot authorize a fresh run.")
