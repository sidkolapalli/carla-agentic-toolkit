"""Managed repetitions configure, durably reload and publish actual initial light states."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_merge_experiment import _prepare as prepare_merge
from tests.test_route_backend import prepared as prepare_route

if TYPE_CHECKING:
    from pathlib import Path


from tests.managed_reload_helpers import (
    ORIGINAL_WORLD_ID,
    RELOADED_WORLD_ID,
    RESET_FRAME,
    ReloadWorld,
    _assert_light_metadata,
    _assert_original_restored,
    _assert_reload_binding,
    _assert_repetition_closed,
    _assert_reset_publication,
    _assert_setup_order,
    _assert_stable_light,
    _case,
    _first_light,
    _reload_evidence,
    _session,
)


def test_settings_precede_one_preserving_reload_and_new_episode_binding(tmp_path: Path) -> None:
    """The original settings remain the restoration target after replacing the world."""
    old, new, client = _case()
    original = old.settings.copy()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        session.open()
        _assert_setup_order(old, client)
        _assert_reload_binding(session, new, lease, original)
        assert session.step().frame == session.expected_frame
        _assert_original_restored(session, new, original)


def test_raw_reload_identity_is_durable_before_map_query(tmp_path: Path) -> None:
    """A slow or failed map RPC cannot hide an acknowledged new episode."""
    _old, new, client = _case()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)

        def inspect_binding() -> None:
            state = lease.recovery_state
            assert state["world_id"] == RELOADED_WORLD_ID
            assert _reload_evidence(state)["phase"] == "acknowledged"

        new.on_map = inspect_binding
        session.open()
        assert _reload_evidence(lease.recovery_state)["map_name"] == "Town10HD_Opt"
        session.close()


def test_pending_reload_is_durable_before_native_call(tmp_path: Path) -> None:
    """The first server mutation has explicit unresolved reload evidence."""
    _old, _new, client = _case()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)

        def inspect_intent() -> None:
            assert _reload_evidence(lease.recovery_state)["phase"] == "pending"
            assert _reload_evidence(lease.recovery_state)["previous_world_id"] == ORIGINAL_WORLD_ID

        client.on_reload = inspect_intent
        session.open()
        assert client.calls == [False]
        session.close()


def test_reset_states_are_published_outside_scheduled_runtime_frames(tmp_path: Path) -> None:
    """Only an actual setup publication can make reset getter evidence current."""
    _old, new, client = _case()
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        session.open()
        _assert_reset_publication(session, new)
        _assert_light_metadata(session)
        assert session.step().frame == RESET_FRAME + 1
        session.close()


def test_repetition_records_reset_states_with_different_actor_ids(tmp_path: Path) -> None:
    """A stable light identity is comparable without pretending actor IDs survive reload."""
    _old, _new, client = _case()
    observations = []
    for _index in range(2):
        with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
            session = _session(client, lease)
            session.open()
            observations.append(session.initial_traffic_lights)
            _assert_repetition_closed(session)
        client.replacement = ReloadWorld(id=9, frame=6)
        client.replacement.light.id = 31
    assert _first_light(observations[0])["actor_id"] != _first_light(observations[1])["actor_id"]
    for evidence in observations:
        _assert_stable_light(evidence)
    assert client.world.settings["synchronous_mode"] is False


@pytest.mark.parametrize("backend", ["merge", "route"])
def test_initial_light_evidence_is_fixture_metadata_only(
    monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    """Reset state provenance is recorded without entering the selector input contract."""
    prepare = prepare_merge if backend == "merge" else prepare_route
    session, experiment = prepare(monkeypatch)
    evidence: dict[str, object] = {"frame": 4, "lights": [{"actor_id": 30, "state": "Red"}]}
    session.initial_traffic_lights = evidence
    assert experiment.fixture_metadata()["initial_traffic_lights"] == evidence
