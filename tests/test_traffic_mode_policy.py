"""Toolkit Traffic Manager sidecars cannot be driven by a synchronous world owner."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import traffic_controller_service, traffic_runtime
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.models import TrafficManagerRequest
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_sync_settings import FakeSettings, FakeWorld

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaTrafficManager
    from carla_agentic_toolkit.models import JsonObject

ACTOR_ID = 11
TRAFFIC_OPERATIONS = ("configure", "populate", "autopilot", "behavior", "tune", "path")


@dataclass
class TrafficWorld(FakeWorld):
    """Keep native mutations observable while supporting legitimate asynchronous controls."""

    actor: Mock = field(default_factory=lambda: Mock(id=ACTOR_ID, attributes={}))
    spawns: int = 0

    def get_actors(self, _ids: object = None) -> SimpleNamespace:
        """Resolve explicit IDs and expose one existing vehicle for result counts."""
        return SimpleNamespace(
            filter=lambda pattern: [self.actor] if pattern == "vehicle.*" else [],
            find=lambda actor_id: self.actor if actor_id == ACTOR_ID else None,
        )

    def get_blueprint_library(self) -> Mock:
        """Avoid a live CARLA import during traffic population."""
        blueprint = Mock(id="vehicle.test")
        blueprint.has_attribute.return_value = False
        return Mock(filter=Mock(return_value=[blueprint]))

    def get_map(self) -> SimpleNamespace:
        """Provide one native spawn point."""
        return SimpleNamespace(name="Town01", get_spawn_points=lambda: [1])

    def try_spawn_actor(self, *_args: object) -> Mock:
        """Count unsafe native creation independently of a facade failure payload."""
        self.spawns += 1
        return self.actor


@dataclass
class TrafficCase:
    """The real facade and adapter share one server double and mutation counter."""

    world: TrafficWorld
    client: Mock
    manager: Mock
    adapter: PythonCarlaAdapter
    api: CarlaScriptApi


def build_traffic_case(monkeypatch: pytest.MonkeyPatch, *, mode: object) -> TrafficCase:
    """Patch native connection only, preserving runtime guards and conversions."""
    world = TrafficWorld(settings=FakeSettings(synchronous_mode=cast("bool", mode)))
    manager = Mock()
    manager.get_port.return_value = 8000
    client = Mock(get_world=Mock(return_value=world), get_trafficmanager=Mock(return_value=manager))
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    monkeypatch.setattr(traffic_controller_service, "_client", lambda _request: client)
    return TrafficCase(world, client, manager, adapter, CarlaScriptApi(adapter, RunSnapshots()))


def traffic_call(api: CarlaScriptApi, operation: str) -> JsonObject:
    """Exercise every public path that registers or controls Traffic Manager vehicles."""
    operations = {
        "configure": lambda: api.configure_traffic_manager({"synchronous_mode": False}),
        "populate": lambda: api.populate_traffic({"vehicle_count": 1, "advance_world": False}),
        "autopilot": lambda: api.set_autopilot({"actor_ids": [ACTOR_ID], "advance_world": False}),
        "behavior": lambda: api.set_vehicle_behavior(
            {"actor_ids": [ACTOR_ID], "profile": "normal"}
        ),
        "tune": lambda: api.tune_traffic_vehicle(ACTOR_ID, {"auto_lane_change": False}),
        "path": lambda: api.set_traffic_vehicle_path(ACTOR_ID, {"route": ["Straight"]}),
    }
    return operations[operation]()


@pytest.mark.parametrize("mode", [True, None, 0, 1, "False"])
@pytest.mark.parametrize("operation", TRAFFIC_OPERATIONS)
def test_tm_paths_refuse_non_async_mode_before_native_work(
    monkeypatch: pytest.MonkeyPatch, mode: object, operation: str
) -> None:
    """Unknown/truthy settings cannot silently become an asynchronous permission."""
    case = build_traffic_case(monkeypatch, mode=mode)

    result = traffic_call(case.api, operation)

    assert result["ok"] is False
    case.client.get_trafficmanager.assert_not_called()
    assert case.world.spawns == 0
    case.world.actor.set_autopilot.assert_not_called()


@pytest.mark.parametrize("operation", TRAFFIC_OPERATIONS)
def test_async_tm_paths_keep_supported_behavior(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """The dedicated asynchronous sidecar remains usable through all six APIs."""
    case = build_traffic_case(monkeypatch, mode=False)

    result = traffic_call(case.api, operation)

    assert result.get("ok") is not False
    case.client.get_trafficmanager.assert_called()


@pytest.mark.parametrize("mode", [False, True])
def test_sync_tm_request_is_rejected_before_all_global_setters(
    monkeypatch: pytest.MonkeyPatch, *, mode: bool
) -> None:
    """Reject the unsupported request before even applying its earlier global fields."""
    case = build_traffic_case(monkeypatch, mode=mode)

    result = case.api.configure_traffic_manager(
        {
            "synchronous_mode": True,
            "global_distance_to_leading_vehicle": 3.0,
            "global_percentage_speed_difference": -5.0,
            "seed": 7,
        }
    )

    assert result["ok"] is False
    assert "#26" in str(result["message"])
    case.client.get_trafficmanager.assert_not_called()
    assert case.manager.method_calls == []


def test_runtime_rejects_sync_before_earlier_global_setters() -> None:
    """Internal configure callers must not partially apply an unsupported sync request."""
    manager = Mock()

    with pytest.raises(UnsupportedFeatureError, match=r"asynchronous.*#26"):
        traffic_runtime.configure_traffic_manager(
            cast("CarlaTrafficManager", manager),
            TrafficManagerRequest(
                global_distance_to_leading_vehicle=3.0, seed=7, synchronous_mode=True
            ),
        )

    assert manager.method_calls == []


@pytest.mark.parametrize("mode", [1, 0, "False"])
def test_runtime_refuses_non_boolean_sync_request_before_setters(mode: object) -> None:
    """Native coercion cannot turn malformed direct requests into timing permission."""
    manager = Mock()
    request = TrafficManagerRequest(
        global_distance_to_leading_vehicle=3.0,
        synchronous_mode=cast("bool", mode),
    )

    with pytest.raises(CarlaAdapterError, match="boolean"):
        traffic_runtime.configure_traffic_manager(cast("CarlaTrafficManager", manager), request)

    assert manager.method_calls == []


@pytest.mark.parametrize("status", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize("operation", ["set_sync", "restore"])
def test_sync_settings_refuse_active_or_stopping_controller(
    monkeypatch: pytest.MonkeyPatch, status: tuple[bool, bool], operation: str
) -> None:
    """Restoration cannot bypass the same live-controller timing boundary as set_sync."""
    case = build_traffic_case(monkeypatch, mode=False)
    monkeypatch.setattr(
        case.api._traffic_controller,  # noqa: SLF001
        "get_status",
        lambda: SimpleNamespace(active=status[0], stopping=status[1]),
    )
    baseline = Mock(wraps=case.adapter.get_world_settings)
    monkeypatch.setattr(case.adapter, "get_world_settings", baseline)
    journal = Mock()
    case.adapter._settings_journal = journal  # noqa: SLF001

    result = timing_call(case, operation, enabled=True)

    assert result["ok"] is False
    assert "controller" in str(result["message"]).lower()
    assert case.world.applied == []
    baseline.assert_not_called()
    journal.capture_world.assert_not_called()


@pytest.mark.parametrize("operation", ["set_sync", "restore"])
def test_async_settings_remain_allowed_with_active_controller(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """The guard excludes synchronous ownership, not valid async settings restoration."""
    case = build_traffic_case(monkeypatch, mode=False)
    monkeypatch.setattr(
        case.api._traffic_controller,  # noqa: SLF001
        "get_status",
        lambda: SimpleNamespace(active=True, stopping=False),
    )

    result = timing_call(case, operation, enabled=False)

    assert result.get("ok") is not False
    assert case.world.settings.synchronous_mode is False


def timing_call(case: TrafficCase, operation: str, *, enabled: bool) -> JsonObject:
    """Use valid complete settings so tests reach the controller ownership guard."""
    if operation == "set_sync":
        return case.api.set_sync_mode(enabled=enabled, fixed_delta_seconds=0.05)
    settings = asdict(case.world.settings) | {
        "synchronous_mode": enabled,
        "fixed_delta_seconds": 0.05,
    }
    return case.api.restore_world_settings(settings)
