"""CARLA's empty-success and zero-ID error responses must preserve cleanup truth."""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import adapter as adapter_module
from carla_agentic_toolkit import script_recovery
from carla_agentic_toolkit import traffic_controller_step as step_module
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.models import TrafficControllerStartRequest, TrafficDensityRequest
from carla_agentic_toolkit.ownership import RunOwnership, release_batch_destroyed
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from carla_agentic_toolkit.snapshots import RunSnapshots
from carla_agentic_toolkit.traffic_density import ControllerActors
from tests.test_script_recovery import journal
from tests.test_settings_recovery import SettingsWorld
from tests.test_traffic_controller_service import TrafficManager, VehicleActor, World

if TYPE_CHECKING:
    import subprocess
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.traffic_controller_step import TrafficControllerStep

ACTOR_ID = 17
REPLACEMENT_WORLD_ID = 8


@dataclass
class EpisodeWorld(World):
    """Bind traffic-controller test actors to one simulator episode."""

    id: int = 7


def _adapter(monkeypatch: pytest.MonkeyPatch, world: object) -> PythonCarlaAdapter:
    adapter = PythonCarlaAdapter()
    client = cast("CarlaClient", SimpleNamespace(get_world=lambda: world))
    monkeypatch.setattr(adapter, "_client", lambda: client)
    monkeypatch.setattr(
        adapter,
        "apply_batch",
        lambda _commands, **_kwargs: {
            "responses": [{"actor_id": 0, "error": "unable to destroy actor: not found"}]
        },
    )
    return adapter


def _worker(monkeypatch: pytest.MonkeyPatch, adapter: PythonCarlaAdapter) -> None:
    def spawn(job: Path, _descriptor: int) -> subprocess.Popen[bytes]:
        report, _owner = script_recovery._worker_cleanup(read_control(job / "request.json"))  # noqa: SLF001
        write_control(job / "result.json", report)
        return cast("subprocess.Popen[bytes]", SimpleNamespace(wait=lambda **_kwargs: 0))

    monkeypatch.setattr(adapter_module, "PythonCarlaAdapter", lambda **_kwargs: adapter)
    monkeypatch.setattr(script_recovery, "_spawn_cleanup", spawn)
    monkeypatch.setattr(script_recovery, "terminate_tree", lambda _process: None)


@pytest.mark.skipif(sys.platform != "linux", reason="Recovery leases require Linux")
@pytest.mark.parametrize("scenario", ["batch", "out_of_band", "recovery"])
def test_absent_actor_cleanup_releases_journal_and_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    """Both explicit destruction and abandoned cleanup accept authoritative absence."""
    world = SettingsWorld()
    monkeypatch.setattr(
        world,
        "get_actors",
        lambda *_args: SimpleNamespace(find=lambda _actor_id: None),
        raising=False,
    )
    adapter = _adapter(monkeypatch, world)
    path = journal(tmp_path, monkeypatch, [])
    ownership = RunOwnership(path)
    ownership.add((ACTOR_ID,), world_id=world.id)
    api = CarlaScriptApi(adapter=adapter, snapshots=RunSnapshots(), ownership=ownership)
    _perform_destroy_scenario(monkeypatch, api, ownership, adapter, scenario)
    _worker(monkeypatch, adapter)
    _recover_and_assert_clean(tmp_path, path, ownership)


def _perform_destroy_scenario(
    monkeypatch: pytest.MonkeyPatch,
    api: CarlaScriptApi,
    ownership: RunOwnership,
    adapter: PythonCarlaAdapter,
    scenario: str,
) -> None:
    if scenario == "batch":
        monkeypatch.setattr(
            adapter,
            "apply_batch",
            lambda _commands, **_kwargs: {"responses": [{"actor_id": ACTOR_ID, "error": ""}]},
        )
        api.apply_batch([{"action": "destroy_actor", "actor_id": ACTOR_ID}], do_tick=False)
        assert ownership.actor_ids() == ()
    elif scenario == "out_of_band":
        assert api.cleanup_owned_actors()["failures"] == []


def _recover_and_assert_clean(tmp_path: Path, path: Path, ownership: RunOwnership) -> None:
    state = tmp_path / "state"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty({"kind": "script", "ownership_path": str(path)})
    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)
    assert (result["ok"], ownership.actor_ids()) == (True, ())
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        assert lease.recovery_state == {}


@pytest.mark.parametrize(
    "error", ["server refused destruction", "unable to destroy actor: not found?"]
)
def test_other_zero_id_errors_keep_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    """Only the server's exact absence acknowledgement counts as already cleaned."""
    world = SettingsWorld()
    monkeypatch.setattr(
        world,
        "get_actors",
        lambda *_args: SimpleNamespace(find=lambda _actor_id: None),
        raising=False,
    )
    adapter = _adapter(monkeypatch, world)
    monkeypatch.setattr(
        adapter,
        "apply_batch",
        lambda _commands, **_kwargs: {"responses": [{"actor_id": 0, "error": error}]},
    )
    path = tmp_path / "owned-actors.json"
    ownership = RunOwnership(path)
    ownership.add((ACTOR_ID,), world_id=world.id)
    api = CarlaScriptApi(adapter=adapter, snapshots=RunSnapshots(), ownership=ownership)
    assert api.cleanup_owned_actors()["failures"]
    assert ownership.actor_ids() == (ACTOR_ID,)


@pytest.mark.parametrize("actor_id", [0, -1, True, "17"])
def test_empty_error_cannot_release_invalid_actor_id(tmp_path: Path, actor_id: object) -> None:
    """Empty-success handling must still reject non-positive or noninteger IDs."""
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((ACTOR_ID,), world_id=7)
    release_batch_destroyed(ownership, {"responses": [{"actor_id": actor_id, "error": ""}]})
    assert ownership.actor_ids() == (ACTOR_ID,)


@pytest.mark.skipif(sys.platform != "linux", reason="Recovery leases require Linux")
@pytest.mark.parametrize("generation", [0, -1])
def test_density_trim_discards_only_confirmed_destroyed_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, generation: int
) -> None:
    """The completed density pass releases its successful trim from script ownership."""
    actor = VehicleActor()
    world = EpisodeWorld(actor, [])
    request = TrafficDensityRequest(vehicle_count=0)
    path = journal(tmp_path, monkeypatch, [])
    ownership = RunOwnership(path)
    ownership.add((actor.id,), world_id=7)
    adapter = _adapter(monkeypatch, world)
    api = CarlaScriptApi(adapter=adapter, snapshots=RunSnapshots(), ownership=ownership)
    step = _density_step(monkeypatch, world, request)
    assert step.destroyed_actor_ids == (actor.id,)
    api._traffic_controller._record_step(step, generation=generation, revision=0)  # noqa: SLF001
    assert ownership.actor_ids() == ()
    assert api.cleanup_owned_actors()["failures"] == []
    _recover_and_assert_clean(tmp_path, path, ownership)


def _density_step(
    monkeypatch: pytest.MonkeyPatch, world: EpisodeWorld, request: TrafficDensityRequest
) -> TrafficControllerStep:
    monkeypatch.setattr(step_module, "traffic_manager", lambda _client, _port: TrafficManager())
    actor_ids = frozenset((world.actor.id,))
    return step_module.maintain_traffic_once(
        cast("CarlaClient", SimpleNamespace(get_world=lambda: world)),
        TrafficControllerStartRequest(density=request),
        {},
        ControllerActors(registered=actor_ids, owned=actor_ids),
        configure_manager=False,
    )


@pytest.mark.parametrize("replacement", ["adapter", "journal", "during_check"])
def test_old_density_step_cannot_release_reused_episode_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    """An old pass must not discard a newly bound journal's reused actor ID."""
    world = EpisodeWorld(VehicleActor(), [])
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((world.actor.id,), world_id=world.id)
    adapter = _adapter(monkeypatch, world)
    api = CarlaScriptApi(adapter=adapter, snapshots=RunSnapshots(), ownership=ownership)
    step = _density_step(monkeypatch, world, TrafficDensityRequest(vehicle_count=0))
    if replacement == "during_check":
        monkeypatch.setattr(
            adapter, "get_world_identity", lambda: _rebind_journal(ownership, world)
        )
    else:
        _rebind_journal(ownership, world)
    if replacement == "adapter":
        world.id = REPLACEMENT_WORLD_ID
    api._traffic_controller._record_step(step, generation=-1, revision=0)  # noqa: SLF001
    assert ownership.actor_ids() == (world.actor.id,)
    assert ownership.world_id() == REPLACEMENT_WORLD_ID


def _rebind_journal(ownership: RunOwnership, world: EpisodeWorld) -> int:
    ownership.clear()
    ownership.add((world.actor.id,), world_id=REPLACEMENT_WORLD_ID)
    return world.id


def test_density_release_without_episode_evidence_retains_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy or malformed maintenance results cannot authorize journal removal."""
    world = EpisodeWorld(VehicleActor(), [])
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((world.actor.id,), world_id=world.id)
    api = CarlaScriptApi(
        adapter=_adapter(monkeypatch, world), snapshots=RunSnapshots(), ownership=ownership
    )
    step = replace(
        _density_step(monkeypatch, world, TrafficDensityRequest(vehicle_count=0)), world_id=None
    )
    api._traffic_controller._record_step(step, generation=0, revision=0)  # noqa: SLF001
    assert ownership.actor_ids() == (world.actor.id,)


@pytest.mark.parametrize("outcome", ["false", "error"])
def test_failed_density_trim_retains_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """False or exceptional destroy calls never become successful lifecycle evidence."""
    world = EpisodeWorld(VehicleActor(), [])
    monkeypatch.setattr(world.actor, "destroy", lambda: _failed_destroy(outcome))
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((world.actor.id,), world_id=world.id)
    api = CarlaScriptApi(
        adapter=_adapter(monkeypatch, world), snapshots=RunSnapshots(), ownership=ownership
    )
    step = _density_step(monkeypatch, world, TrafficDensityRequest(vehicle_count=0))
    api._traffic_controller._record_step(step, generation=0, revision=0)  # noqa: SLF001
    assert step.destroyed_actor_ids == ()
    assert ownership.actor_ids() == (world.actor.id,)


def _failed_destroy(outcome: str) -> bool:
    if outcome == "error":
        message = "Actor destruction failed."
        raise RuntimeError(message)
    return False


def test_reset_existing_pass_reports_destroyed_episode_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real maintenance pass carries reset destruction and its originating episode."""
    world = EpisodeWorld(VehicleActor(), [])
    step = _density_step(
        monkeypatch, world, TrafficDensityRequest(vehicle_count=0, reset_existing=True)
    )
    assert step.destroyed_actor_ids == (world.actor.id,)
    assert getattr(step, "world_id", None) == world.id


@pytest.mark.parametrize(
    "responses",
    [[], [None], [{"actor_id": 18, "error": ""}], [{"actor_id": 17, "error": ""}] * 2],
)
def test_malformed_cleanup_acknowledgement_keeps_owned_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, responses: list[object]
) -> None:
    """Missing, invalid, or mismatched acknowledgements cannot release the requested actor."""
    world = SettingsWorld()
    monkeypatch.setattr(
        world,
        "get_actors",
        lambda *_args: SimpleNamespace(find=lambda _actor_id: None),
        raising=False,
    )
    adapter = _adapter(monkeypatch, world)
    monkeypatch.setattr(
        adapter, "apply_batch", lambda _commands, **_kwargs: {"responses": responses}
    )
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((ACTOR_ID,), world_id=world.id)
    api = CarlaScriptApi(adapter=adapter, snapshots=RunSnapshots(), ownership=ownership)
    assert api.cleanup_owned_actors()["failures"]
    assert ownership.actor_ids() == (ACTOR_ID,)


@pytest.mark.parametrize("actor_id", [True, 1.0, "1", 0, -1])
def test_invalid_cleanup_acknowledgement_is_not_actor_one(actor_id: object) -> None:
    """Python's scalar equality must not validate a malformed actor identity."""
    result = adapter_module._destroy_batch_result(  # noqa: SLF001
        1, {"responses": [{"actor_id": actor_id, "error": ""}]}
    )
    assert result.destroyed is False


@pytest.mark.parametrize(
    "responses", [[], [{"actor_id": 18, "error": ""}], [{"actor_id": 17, "error": ""}] * 2]
)
def test_batch_does_not_release_unacknowledged_or_unrequested_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, responses: list[object]
) -> None:
    """Batch lifecycle evidence must match each requested destruction, not just owned IDs."""
    adapter = _adapter(monkeypatch, SettingsWorld())
    monkeypatch.setattr(
        adapter, "apply_batch", lambda _commands, **_kwargs: {"responses": responses}
    )
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((ACTOR_ID, 18), world_id=7)
    api = CarlaScriptApi(adapter=adapter, snapshots=RunSnapshots(), ownership=ownership)
    api.apply_batch([{"action": "destroy_actor", "actor_id": ACTOR_ID}], do_tick=False)
    assert ownership.actor_ids() == (ACTOR_ID, 18)


def test_mixed_batch_releases_only_matching_successes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed command does not invalidate another command's matching acknowledgement."""
    adapter = _adapter(monkeypatch, SettingsWorld())
    monkeypatch.setattr(
        adapter,
        "apply_batch",
        lambda _commands, **_kwargs: {
            "responses": [
                {"actor_id": ACTOR_ID, "error": ""},
                {"actor_id": 0, "error": "server refused destruction"},
            ]
        },
    )
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.add((ACTOR_ID, 18), world_id=7)
    api = CarlaScriptApi(adapter=adapter, snapshots=RunSnapshots(), ownership=ownership)
    commands = [{"action": "destroy_actor", "actor_id": actor_id} for actor_id in (ACTOR_ID, 18)]
    api.apply_batch(commands, do_tick=False)
    assert ownership.actor_ids() == (18,)
