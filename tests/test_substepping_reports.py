"""Native timing evidence reaches health, world state, and inline facade snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.models import WorldSettings
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from collections.abc import Mapping

    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.models import JsonObject

PHYSICS_FIELDS = ("substepping", "max_substeps", "max_substep_delta_time")
REPORT_OPERATIONS = ("adapter_health", "adapter_world", "facade_health", "facade_world")
OBSERVED_PHYSICS = (
    {"substepping": True, "max_substeps": 10, "max_substep_delta_time": 0.01},
    {"substepping": False, "max_substeps": 7, "max_substep_delta_time": 0.004},
)
EXISTING_SETTINGS = {
    "synchronous_mode": False,
    "fixed_delta_seconds": 0.032,
    "no_rendering_mode": True,
}


@dataclass
class ReportingWorld:
    """Expose native observed settings independently of the reporting DTO."""

    settings: SimpleNamespace
    id: int = 17
    settings_reads: int = 0
    actor_reads: int = 0

    def get_settings(self) -> SimpleNamespace:
        """Return only settings explicitly present in this native-like client version."""
        self.settings_reads += 1
        return self.settings

    def get_map(self) -> SimpleNamespace:
        """Report a map without native mutation or unrelated dependencies."""
        return SimpleNamespace(name="Town07")

    def get_actors(self) -> SimpleNamespace:
        """Keep inventory reads visible when health is intentionally version-only."""
        self.actor_reads += 1
        return SimpleNamespace(filter=lambda _pattern: [])

    def get_snapshot(self) -> SimpleNamespace:
        """Provide the existing world-state frame observation."""
        return SimpleNamespace(frame=42)


@dataclass
class ReportingClient:
    """Retain native version and world observations without creating a simulator client."""

    world: ReportingWorld
    client_version: str | None = "0.9.16"
    server_version: str | None = "0.9.16-source"
    world_reads: int = 0

    def get_client_version(self) -> str | None:
        """Return the full client release used by the matching-health preflight."""
        return self.client_version

    def get_server_version(self) -> str | None:
        """Return the full independently available server version."""
        return self.server_version

    def get_world(self) -> ReportingWorld:
        """Count world access separately from version-only diagnostics."""
        self.world_reads += 1
        return self.world

    def set_timeout(self, _seconds: float) -> None:
        """Support retained native client timeout refresh without I/O."""


@dataclass
class ReportingCase:
    """Use actual adapter and facade entry points with their real snapshot store."""

    world: ReportingWorld
    client: ReportingClient
    adapter: PythonCarlaAdapter
    snapshots: RunSnapshots = field(default_factory=RunSnapshots)

    @property
    def api(self) -> CarlaScriptApi:
        """Construct the real facade, not a report-returning adapter double."""
        return CarlaScriptApi(self.adapter, self.snapshots)


def _reporting_case(physics: Mapping[str, object]) -> ReportingCase:
    world = ReportingWorld(SimpleNamespace(**EXISTING_SETTINGS, **physics))
    client = ReportingClient(world)
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    return ReportingCase(world, client, adapter)


def _report(case: ReportingCase, operation: str) -> JsonObject:
    operations = {
        "adapter_health": lambda: case.adapter.health_check().to_dict(),
        "adapter_world": lambda: case.adapter.get_world_state().to_dict(),
        "facade_health": case.api.health_check,
        "facade_world": case.api.get_world_state,
    }
    return operations[operation]()


def _assert_physics(payload: JsonObject, expected: Mapping[str, object]) -> None:
    settings = cast("JsonObject", payload["settings"])
    for name, value in expected.items():
        assert type(settings[name]) is type(value)
        assert settings[name] == value


def _assert_existing_observations(case: ReportingCase, payload: JsonObject) -> None:
    settings = cast("JsonObject", payload["settings"])
    assert {name: settings[name] for name in EXISTING_SETTINGS} == EXISTING_SETTINGS
    assert payload["current_map"] == "Town07"
    assert case.world.settings_reads == 1
    assert case.world.actor_reads == 1


def _assert_inline_snapshot(case: ReportingCase, operation: str, payload: JsonObject) -> None:
    snapshot_uris = {
        "facade_health": "carla-snapshot://session/status",
        "facade_world": "carla-snapshot://world/current",
    }
    if operation in snapshot_uris:
        assert case.snapshots.read_snapshot(snapshot_uris[operation]) == payload


@pytest.mark.parametrize("operation", REPORT_OPERATIONS)
@pytest.mark.parametrize("physics", OBSERVED_PHYSICS)
def test_native_physics_observations_reach_all_reports_and_snapshots(
    operation: str, physics: JsonObject
) -> None:
    """An observed False or a nondefault physics budget must not be dropped or invented."""
    case = _reporting_case(physics)

    payload = _report(case, operation)

    _assert_physics(payload, physics)
    _assert_existing_observations(case, payload)
    _assert_inline_snapshot(case, operation, payload)


@pytest.mark.parametrize("operation", REPORT_OPERATIONS)
@pytest.mark.parametrize("missing", [PHYSICS_FIELDS, *((name,) for name in PHYSICS_FIELDS)])
def test_missing_legacy_native_fields_are_reported_as_unknown(
    operation: str, missing: tuple[str, ...]
) -> None:
    """A partial native settings API preserves available evidence and nulls only unknowns."""
    physics = dict(OBSERVED_PHYSICS[1])
    available = {name: value for name, value in physics.items() if name not in missing}
    expected = {name: None if name in missing else value for name, value in physics.items()}
    case = _reporting_case(available)

    payload = _report(case, operation)

    _assert_physics(payload, expected)
    _assert_existing_observations(case, payload)
    _assert_inline_snapshot(case, operation, payload)


def test_legacy_world_settings_constructor_has_unknown_physics_defaults() -> None:
    """Existing three-field DTO callers remain valid without fabricating native defaults."""
    settings = WorldSettings(
        synchronous_mode=False, fixed_delta_seconds=None, no_rendering_mode=False
    )

    payload = settings.to_dict()

    assert {name: payload[name] for name in PHYSICS_FIELDS} == dict.fromkeys(PHYSICS_FIELDS)


@pytest.mark.parametrize("operation", ["facade_health", "facade_world"])
def test_inline_snapshots_follow_new_native_physics_observations(operation: str) -> None:
    """A later successful report replaces the snapshot with newly observed settings."""
    case = _reporting_case(OBSERVED_PHYSICS[0])
    _report(case, operation)
    case.world.settings = SimpleNamespace(**EXISTING_SETTINGS, **OBSERVED_PHYSICS[1])

    payload = _report(case, operation)

    _assert_physics(payload, OBSERVED_PHYSICS[1])
    _assert_inline_snapshot(case, operation, payload)


@pytest.mark.parametrize("operation", ["adapter_health", "facade_health"])
@pytest.mark.parametrize("client_version", ["0.10.0", None])
def test_unverified_health_remains_version_only_without_physics_defaults(
    operation: str, client_version: str | None
) -> None:
    """Mismatch or unknown compatibility never turns missing physics into fake evidence."""
    case = _reporting_case(OBSERVED_PHYSICS[0])
    case.client.client_version = client_version

    payload = _report(case, operation)

    assert payload["connected"] is False
    assert payload["settings"] is None
    assert payload["actor_counts"] is None
    assert case.client.world_reads == 0
    _assert_inline_snapshot(case, operation, payload)
