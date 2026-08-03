"""Behavior specs for the one-tool CARLA script API surface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from carla_mcp.errors import CarlaAdapterError
from carla_mcp.models import Location, SensorInfo, Transform
from carla_mcp.script_api import CarlaScriptApi
from carla_mcp.snapshots import RunSnapshots

if TYPE_CHECKING:
    from pathlib import Path

    from carla_mcp.adapter import PythonCarlaAdapter


SENSOR_ID = 101
PARENT_ID = 22
ACTOR_ID = 44
ROUTE_STEP_METERS = 3.0
CONNECTION_REFUSED = "connection refused"


@dataclass
class ScriptAdapter:
    """Test double for script-only API methods."""

    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    @property
    def host(self) -> str:
        """Return a CARLA host."""
        return "127.0.0.1"

    @property
    def port(self) -> int:
        """Return a CARLA port."""
        return 2000

    @property
    def timeout(self) -> float:
        """Return a CARLA timeout."""
        return 10.0

    def attach_sensor(
        self,
        *,
        blueprint_id: str,
        transform: Transform,
        attributes: dict[str, str],
        parent_actor_id: int | None,
    ) -> SensorInfo:
        """Attach a fake sensor."""
        self.calls.append(
            (
                "attach_sensor",
                {
                    "blueprint_id": blueprint_id,
                    "transform": transform.to_dict(),
                    "attributes": attributes,
                    "parent_actor_id": parent_actor_id,
                },
            )
        )
        return SensorInfo(
            sensor_id=SENSOR_ID,
            blueprint_id=blueprint_id,
            parent_actor_id=parent_actor_id,
            attributes=attributes,
            transform=transform,
        )

    def generate_route(
        self,
        *,
        start: Location,
        end: Location,
        step_meters: float,
        max_steps: int,
    ) -> dict[str, object]:
        """Return a fake route."""
        self.calls.append(
            (
                "generate_route",
                {
                    "start": start.to_dict(),
                    "end": end.to_dict(),
                    "step_meters": step_meters,
                    "max_steps": max_steps,
                },
            )
        )
        return {"waypoint_count": 1, "route": [{"road_id": 1}]}

    def apply_vehicle_control(
        self,
        *,
        actor_id: int,
        control: dict[str, object],
    ) -> dict[str, object]:
        """Return applied direct control."""
        self.calls.append(("apply_vehicle_control", {"actor_id": actor_id, "control": control}))
        return {"actor_id": actor_id, "applied_control": control}

    def list_capabilities(self) -> dict[str, object]:
        """Return fake live capability probes."""
        return {"client_version": "0.9.16", "map": {"get_waypoint": True}}

    def set_spectator(self, transform: Transform) -> dict[str, object]:
        """Return the current and previous fake spectator transforms."""
        return {
            "spectator": transform.to_dict(),
            "previous_spectator": {
                "location": {"x": 1.0, "y": 2.0, "z": 3.0},
                "rotation": {"pitch": -15.0, "yaw": 90.0, "roll": 0.0},
            },
        }

    def save_screenshot(
        self,
        *,
        output_path: Path,
        attributes: dict[str, str],
    ) -> dict[str, object]:
        """Return a fake screenshot capture."""
        return {"path": str(output_path), "attributes": attributes}


class FailingControlAdapter(ScriptAdapter):
    """Adapter that fails a formerly direct facade operation."""

    def apply_vehicle_control(
        self,
        *,
        actor_id: int,
        control: dict[str, object],
    ) -> dict[str, object]:
        """Fail like a recoverable CARLA control operation."""
        del actor_id, control
        raise CarlaAdapterError(CONNECTION_REFUSED)


class FailingHealthAdapter(ScriptAdapter):
    """Adapter that reports a recoverable CARLA operation failure."""

    def health_check(self) -> None:
        """Fail like an unreachable CARLA server."""
        raise CarlaAdapterError(CONNECTION_REFUSED)


def test_describe_api_publishes_runtime_catalog() -> None:
    """The one-tool model should expose runtime API discovery."""
    snapshots = RunSnapshots()
    api = build_api(adapter=ScriptAdapter(), snapshots=snapshots)

    catalog = api.describe_api()
    methods = cast("dict[str, object]", catalog["methods"])

    assert_catalog_has_methods(
        methods,
        ("attach_sensor", "apply_vehicle_control", "list_capabilities"),
    )
    assert_catalog_hides_private_state(methods)
    assert snapshots.read_snapshot("carla-snapshot://api") == catalog


def test_set_spectator_returns_restorable_camera_state() -> None:
    """Visual workflows should receive the previous operator camera transform."""
    api = build_api(adapter=ScriptAdapter(), snapshots=RunSnapshots())

    result = api.set_spectator(
        {
            "location": {"x": 4.0, "y": 5.0, "z": 6.0},
            "rotation": {"pitch": -10.0, "yaw": 0.0, "roll": 0.0},
        }
    )

    previous = cast("dict[str, object]", result["previous_spectator"])
    assert previous["location"] == {"x": 1.0, "y": 2.0, "z": 3.0}


def test_attach_event_sensor_maps_kind_and_publishes_snapshot() -> None:
    """Event sensors should use official CARLA sensor blueprints."""
    snapshots = RunSnapshots()
    adapter = ScriptAdapter()
    api = build_api(adapter=adapter, snapshots=snapshots)

    sensor = api.attach_event_sensor("collision", parent_id=PARENT_ID)

    assert sensor["blueprint_id"] == "sensor.other.collision"
    assert adapter.calls[0][1]["parent_actor_id"] == PARENT_ID
    assert snapshots.read_snapshot(f"carla-snapshot://sensors/{SENSOR_ID}") == sensor


def test_generate_route_parses_locations_and_publishes_snapshot() -> None:
    """Routes should accept JSON locations and publish a route snapshot."""
    snapshots = RunSnapshots()
    adapter = ScriptAdapter()
    api = build_api(adapter=adapter, snapshots=snapshots)

    route = api.generate_route(
        start={"x": 1.0, "y": 2.0, "z": 0.0},
        end={"x": 8.0, "y": 2.0, "z": 0.0},
        step_meters=ROUTE_STEP_METERS,
        max_steps=20,
    )

    assert route["waypoint_count"] == 1
    assert adapter.calls[0][1]["step_meters"] == ROUTE_STEP_METERS
    assert snapshots.read_snapshot("carla-snapshot://route/latest") == route


def test_apply_vehicle_control_passes_direct_control_kwargs() -> None:
    """Direct control should keep the kwargs shape expected by CARLA VehicleControl."""
    adapter = ScriptAdapter()
    api = build_api(adapter=adapter, snapshots=RunSnapshots())

    result = api.apply_vehicle_control(ACTOR_ID, throttle=0.4, steer=-0.1, brake=0.0)

    assert result["applied_control"] == {"throttle": 0.4, "steer": -0.1, "brake": 0.0}
    assert adapter.calls[0][1]["actor_id"] == ACTOR_ID


def test_direct_adapter_failure_uses_recoverable_operation_shape() -> None:
    """Every CARLA facade path should expose the same recoverable error contract."""
    api = build_api(adapter=FailingControlAdapter(), snapshots=RunSnapshots())

    result = api.apply_vehicle_control(ACTOR_ID, throttle=0.4)

    assert result == {
        "ok": False,
        "error_type": "apply_vehicle_control_failed",
        "message": CONNECTION_REFUSED,
        "retryable": True,
        "error": CONNECTION_REFUSED,
    }


def test_recoverable_tool_failure_keeps_an_explicit_error_marker() -> None:
    """Scripts should be able to inspect and recover from CARLA operation failures."""
    api = build_api(adapter=FailingHealthAdapter(), snapshots=RunSnapshots())

    result = api.health_check()

    assert result == {
        "ok": False,
        "error_type": "carla_connection_error",
        "message": "connection refused",
        "host": "127.0.0.1",
        "port": 2000,
        "retryable": True,
        "error": CONNECTION_REFUSED,
    }


def build_api(adapter: ScriptAdapter, snapshots: RunSnapshots) -> CarlaScriptApi:
    """Build a script API with a partial fake adapter for behavior specs."""
    return CarlaScriptApi(
        adapter=cast("PythonCarlaAdapter", adapter),
        snapshots=snapshots,
    )


def assert_catalog_has_methods(methods: dict[str, object], names: tuple[str, ...]) -> None:
    """Assert that the catalog exposes expected public methods."""
    missing = [name for name in names if name not in methods]
    assert missing == []


def assert_catalog_hides_private_state(methods: dict[str, object]) -> None:
    """Assert that private implementation attributes are absent."""
    assert "_adapter" not in methods
