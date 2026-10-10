"""Execution lifetimes must not become native RPC timeouts or reset at worker pickup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.rpc_timeouts import (
    MAP_OBSERVATION_SECONDS,
    NORMAL_RPC_SECONDS,
    RpcTimeoutPolicy,
    call_map_rpc,
)
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_sync_settings import FakeWorld

REPLACED_WORLD_ID = 18

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient


@dataclass
class MapClient:
    """Observe client-side caps independently of server replacement and lost replies."""

    world: FakeWorld = field(default_factory=FakeWorld)
    fail: bool = False
    timeouts: list[float] = field(default_factory=list)
    mutation_timeouts: list[float] = field(default_factory=list)
    mutations: int = 0
    reads: int = 0
    reads_at_mutation: list[int] = field(default_factory=list)

    def set_timeout(self, timeout: float) -> None:
        """Record the cap that the next native request will use."""
        self.timeouts.append(timeout)

    def get_world(self) -> FakeWorld:
        """Read the current episode without ticking or mutating it."""
        self.reads += 1
        return self.world

    def load_world(self, map_name: str, *, reset_settings: bool = True) -> FakeWorld:
        """Mutate once, including when the client loses the successful reply."""
        del map_name, reset_settings
        return self.replace()

    def reload_world(self, reset_settings: bool) -> FakeWorld:  # noqa: FBT001
        """Exercise the same native replacement boundary as load_world."""
        del reset_settings
        return self.replace()

    def replace(self) -> FakeWorld:
        """Keep a completed replacement visible after a client timeout."""
        self.reads_at_mutation.append(self.reads)
        self.mutations += 1
        self.mutation_timeouts.append(self.timeouts[-1] if self.timeouts else 0.0)
        self.world = FakeWorld(id=self.world.id + 1)
        if self.fail:
            message = "map RPC timed out after replacement"
            raise RuntimeError(message)
        return self.world


def _adapter(client: MapClient, *, timeout: float = 300.0) -> PythonCarlaAdapter:
    adapter = PythonCarlaAdapter(timeout=timeout)
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    return adapter


def test_ordinary_rpc_cap_is_not_the_execution_lifetime() -> None:
    """A long sandbox lifetime never turns an ordinary native call into a long block."""
    client = MapClient()
    adapter = _adapter(client)

    adapter.get_world_identity()
    adapter.get_world_identity()

    assert client.timeouts == [10.0, 10.0]


@pytest.mark.parametrize("operation", ["load", "reload", "opendrive"])
def test_only_native_map_mutation_uses_elevated_cap(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temporary map cap is removed before ordinary requests resume."""
    client = MapClient()
    adapter = _adapter(client)
    _replace(adapter, operation, monkeypatch)
    adapter.get_world_identity()

    assert client.mutation_timeouts == [120.0]
    assert client.timeouts[-1] == NORMAL_RPC_SECONDS


@pytest.mark.parametrize("operation", ["load", "reload", "opendrive"])
def test_failed_map_rpc_observes_once_without_retry_or_rebinding(
    operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lost reply does not authorize retrying a replacement that may already exist."""
    client = MapClient(fail=True)
    adapter = _adapter(client)
    api = CarlaScriptApi(adapter, RunSnapshots())
    _install_opendrive(client, monkeypatch)
    payload = _call_api(api, operation)

    _assert_failed_map_payload(payload)
    _assert_single_map_observation(client)
    assert client.timeouts[-1] == NORMAL_RPC_SECONDS
    assert any(0 < seconds <= MAP_OBSERVATION_SECONDS for seconds in client.timeouts)


def _assert_single_map_observation(client: MapClient) -> None:
    assert (client.mutations, client.reads) == (1, 2)
    assert client.reads_at_mutation == [1]
    assert client.reads - client.reads_at_mutation[0] == 1


def _assert_failed_map_payload(payload: dict[str, object]) -> None:
    assert payload["retryable"] is False
    assert payload["observed_world"] == {"world_id": 18, "map_name": "Town01", "error": None}
    assert "Do not retry" in str(payload["hint"])


def test_cached_client_refreshes_remaining_budget_between_native_calls() -> None:
    """Repeated calls cannot keep the earlier, larger remaining timeout."""
    now = [95.0]
    client = MapClient()
    policy = RpcTimeoutPolicy(absolute_deadline=100.0, clock=lambda: now[0])
    adapter = PythonCarlaAdapter(rpc_timeout_policy=policy)
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001

    adapter.get_world_identity()
    now[0] = 96.0
    adapter.get_world_identity()

    assert client.timeouts == [5.0, 4.0]


def test_map_timeout_and_final_restore_use_remaining_budget() -> None:
    """Elevating the map cap never elevates the remaining execution lifetime."""
    now = [95.0]
    client = MapClient()
    policy = RpcTimeoutPolicy(absolute_deadline=100.0, clock=lambda: now[0])

    def mutate() -> FakeWorld:
        world = client.replace()
        now[0] = 97.0
        return world

    assert call_map_rpc(client, policy, mutate).id == REPLACED_WORLD_ID
    assert (client.mutation_timeouts, client.timeouts[-1]) == ([5.0], 3.0)


def test_map_failure_after_deadline_keeps_original_error_and_stops_polling() -> None:
    """A finally block cannot replace the map error with an exhausted-budget error."""
    now = [95.0]
    client = MapClient()
    policy = RpcTimeoutPolicy(absolute_deadline=100.0, clock=lambda: now[0])

    def mutate() -> FakeWorld:
        client.replace()
        now[0] = 101.0
        message = "map timeout is original failure"
        raise RuntimeError(message)

    with pytest.raises(CarlaAdapterError, match="map timeout is original failure") as raised:
        call_map_rpc(client, policy, mutate)

    assert client.reads == 0
    assert "deadline expired" in str(raised.value.details["observed_world"])
    assert client.timeouts[-1] == NORMAL_RPC_SECONDS
    with pytest.raises(CarlaAdapterError, match="deadline expired"):
        policy.timeout_seconds()


def test_observation_failure_keeps_original_map_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The read-only diagnostic can fail without authorizing another map mutation."""
    client = MapClient(fail=True)

    def unavailable_world() -> FakeWorld:
        message = "server still loading"
        raise RuntimeError(message)

    monkeypatch.setattr(client, "get_world", unavailable_world)
    with pytest.raises(CarlaAdapterError, match="map RPC timed out") as raised:
        call_map_rpc(client, RpcTimeoutPolicy(), client.replace)

    assert raised.value.details["observed_world"] == {
        "world_id": None,
        "map_name": None,
        "error": "server still loading",
    }
    assert client.mutations == 1


def test_restore_timeout_failure_does_not_mask_map_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client configuration failure is secondary evidence, not a replacement of the lost reply."""
    client = MapClient(fail=True)
    original_set_timeout = client.set_timeout

    def set_timeout(seconds: float) -> None:
        if seconds == NORMAL_RPC_SECONDS:
            message = "timeout configuration failed"
            raise RuntimeError(message)
        original_set_timeout(seconds)

    monkeypatch.setattr(client, "set_timeout", set_timeout)
    with pytest.raises(CarlaAdapterError, match="map RPC timed out") as raised:
        call_map_rpc(client, RpcTimeoutPolicy(), client.replace)

    assert raised.value.details["rpc_timeout_restore_error"] == "timeout configuration failed"


def _install_opendrive(client: MapClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "carla_agentic_toolkit.experiment_environment.generate_opendrive_world",
        lambda *_args, **_kwargs: client.replace(),
    )


def _replace(adapter: PythonCarlaAdapter, operation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    client = cast("MapClient", adapter._connected_client)  # noqa: SLF001
    _install_opendrive(client, monkeypatch)
    _call_api(CarlaScriptApi(adapter, RunSnapshots()), operation)


def _call_api(api: CarlaScriptApi, operation: str) -> dict[str, object]:
    operations = {
        "load": lambda: api.load_world("Town02"),
        "reload": lambda: api.reload_world(reset_settings=False),
        "opendrive": lambda: api.generate_opendrive_world("<OpenDRIVE/>"),
    }
    return operations[operation]()
