"""Script sync transitions validate timing and journal originals before applying them."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

import carla_agentic_toolkit.adapter as adapter_module
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError, OwnershipError
from carla_agentic_toolkit.models import TrafficManagerRequest
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.api_helpers import build_api

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.script_settings import RunSettings
    from carla_agentic_toolkit.traffic_controller_service import InProcessTrafficControllerService


@dataclass
class FakeSettings:
    """All timing and rendering values that must survive temporary script changes."""

    synchronous_mode: bool = False
    fixed_delta_seconds: float | None = None
    no_rendering_mode: bool = True
    substepping: bool = True
    max_substeps: int = 10
    max_substep_delta_time: float = 0.01


@dataclass
class FakeWorld:
    """Return copies of settings so failed validation cannot mutate the fake world."""

    settings: FakeSettings = field(default_factory=FakeSettings)
    applied: list[dict[str, object]] = field(default_factory=list)
    id: int = 17

    def get_settings(self) -> FakeSettings:
        """Return a copy of current server settings."""
        return replace(self.settings)

    def apply_settings(self, settings: FakeSettings) -> int:
        """Record settings applied by the adapter."""
        self.settings = settings
        self.applied.append(asdict(settings))
        return 42

    def get_map(self) -> SimpleNamespace:
        """Return a minimal map."""
        return SimpleNamespace(name="Town01")

    def get_actors(self) -> SimpleNamespace:
        """Return an empty actor list."""
        return SimpleNamespace(filter=lambda _: [])

    def get_snapshot(self) -> SimpleNamespace:
        """Return the latest frame without ticking."""
        return SimpleNamespace(frame=42)


@dataclass
class SettingsJournal:
    """Observe captures while the world still has its original settings."""

    captures: list[dict[str, object]] = field(default_factory=list)
    traffic_captures: list[tuple[int, int, bool]] = field(default_factory=list)
    traffic_setting_captures: list[tuple[int, int, str, object]] = field(default_factory=list)
    fail: bool = False

    def capture_world(self, world: FakeWorld) -> None:
        """Record the complete settings snapshot before mutation."""
        if self.fail:
            message = "settings journal write failed"
            raise OwnershipError(message)
        self.captures.append(asdict(world.get_settings()))

    def capture_traffic_manager(self, world: FakeWorld, port: int, enabled: bool) -> None:  # noqa: FBT001
        """Record Traffic Manager mode before the mutation."""
        self.traffic_captures.append((world.id, port, enabled))

    def capture_traffic_manager_setting(
        self, world: FakeWorld, port: int, *, setting: str, value: object
    ) -> None:
        """Observe each impending native global write independently."""
        if self.fail:
            message = "settings journal write failed"
            raise OwnershipError(message)
        self.traffic_setting_captures.append((world.id, port, setting, value))


def build_adapter(world: FakeWorld, journal: SettingsJournal | None = None) -> PythonCarlaAdapter:
    """Bind the real adapter to an in-memory world."""
    adapter = PythonCarlaAdapter(settings_journal=cast("RunSettings | None", journal))
    adapter._connected_client = cast("CarlaClient", SimpleNamespace(get_world=lambda: world))  # noqa: SLF001
    return adapter


@pytest.mark.parametrize("delta", [None, 0.0, -0.01, 0.101, float("nan"), float("inf"), True])
def test_sync_rejects_unsafe_delta_before_applying_settings(delta: float | None) -> None:
    """Sync requires an explicit finite fixed delta in the physics-safe range."""
    world = FakeWorld()
    journal = SettingsJournal()
    adapter = build_adapter(world, journal)

    with pytest.raises(CarlaAdapterError):
        adapter.set_sync_mode(enabled=True, fixed_delta_seconds=delta)

    assert world.applied == []
    assert journal.captures == []


def test_sync_rejects_delta_exceeding_physics_substep_budget() -> None:
    """A valid general delta can still exceed this world's physics budget."""
    world = FakeWorld(settings=FakeSettings(max_substeps=2))
    adapter = build_adapter(world)

    with pytest.raises(CarlaAdapterError, match="substep"):
        adapter.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)

    assert world.applied == []


def test_sync_captures_all_original_settings_before_mutating() -> None:
    """Run settings are durable before the first ApplySettings RPC."""
    world = FakeWorld()
    original = asdict(world.settings)
    journal = SettingsJournal()
    adapter = build_adapter(world, journal)

    adapter.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)

    assert journal.captures == [original]
    assert world.applied == [original | {"synchronous_mode": True, "fixed_delta_seconds": 0.05}]


def test_journal_failure_prevents_world_mutation() -> None:
    """A failed durable capture must leave server settings untouched."""
    world = FakeWorld()
    adapter = build_adapter(world, SettingsJournal(fail=True))

    with pytest.raises(OwnershipError, match="journal write failed"):
        adapter.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)

    assert world.applied == []


def test_script_sync_requires_explicit_delta() -> None:
    """The facade reports a recoverable validation failure for a missing delta."""
    world = FakeWorld()
    api = build_api(build_adapter(world), RunSnapshots())

    result = api.set_sync_mode(enabled=True)

    assert result["ok"] is False
    assert result["error_type"] == "set_sync_mode_failed"
    assert world.applied == []


def test_fixed_delta_accepts_upper_bound_without_substepping() -> None:
    """Disabling physics substeps removes that budget while retaining the global bound."""
    world = FakeWorld(settings=FakeSettings(substepping=False, max_substeps=1))
    delta = 0.1

    build_adapter(world).set_sync_mode(enabled=True, fixed_delta_seconds=delta)

    assert world.settings.fixed_delta_seconds == delta


def test_restore_captures_current_settings_then_restores_all_six_fields() -> None:
    """Explicit restore is also journaled before it updates the simulator."""
    world = FakeWorld()
    original = asdict(world.settings)
    journal = SettingsJournal()
    adapter = build_adapter(world, journal)
    adapter.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)
    changed = asdict(world.settings)

    adapter.restore_world_settings(original)

    assert asdict(world.settings) == original
    assert journal.captures == [original, changed]


def test_restore_rejects_incomplete_settings_before_mutation() -> None:
    """Restore accepts only a complete reviewed settings capture."""
    world = FakeWorld()
    adapter = build_adapter(world)

    with pytest.raises((CarlaAdapterError, OwnershipError)):
        adapter.restore_world_settings({"synchronous_mode": False})

    assert world.applied == []


def test_traffic_manager_fields_are_captured_immediately_before_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real runtime captures each field immediately before its async-only setter."""
    from tests.test_traffic_manager_settings_journal import (  # noqa: PLC0415
        ATTEMPTED,
        RecordingManager,
    )

    world = FakeWorld()
    journal = SettingsJournal()
    adapter = build_adapter(world, journal)
    manager = RecordingManager()

    def verify_capture() -> None:
        setting, value = manager.calls[-1]
        assert journal.traffic_setting_captures[-1] == (world.id, 8000, setting, value)
        assert len(journal.traffic_setting_captures) == len(manager.calls)

    manager.after_setting = verify_capture
    monkeypatch.setattr(adapter_module, "traffic_manager", lambda *_: manager)

    adapter.configure_traffic_manager(
        request=TrafficManagerRequest(
            global_distance_to_leading_vehicle=9.0,
            global_percentage_speed_difference=25.0,
            seed=23,
            synchronous_mode=False,
        )
    )

    assert journal.traffic_setting_captures == [
        (world.id, 8000, setting, value) for setting, value in ATTEMPTED.items()
    ]


def test_adapter_per_field_capture_delegates_without_mutation() -> None:
    """Controller workers and ordinary calls share the same journal entry point."""
    world = FakeWorld()
    journal = SettingsJournal()
    adapter = build_adapter(world, journal)

    adapter.capture_traffic_manager_setting(8001, setting="seed", value=23)

    assert journal.traffic_setting_captures == [(world.id, 8001, "seed", 23)]
    assert world.applied == []


def test_adapter_legacy_mode_hook_delegates_to_per_field_capture() -> None:
    """The compatibility hook must not bypass the new sparse journal boundary."""
    world = FakeWorld()
    journal = SettingsJournal()

    build_adapter(world, journal).capture_traffic_manager_sync(8001, enabled=False)

    assert journal.traffic_setting_captures == [(world.id, 8001, "synchronous_mode", False)]


@pytest.mark.parametrize("stopping", [False, True])
def test_api_close_waits_for_controller_to_stop(*, stopping: bool) -> None:
    """A worker that remains active after joining blocks settings restoration."""
    api = build_api(build_adapter(FakeWorld()), RunSnapshots())
    status = SimpleNamespace(active=stopping, stopping=stopping, to_dict=dict)
    api._traffic_controller = cast(  # noqa: SLF001
        "InProcessTrafficControllerService", SimpleNamespace(stop=lambda: status)
    )

    if stopping:
        with pytest.raises(CarlaAdapterError, match="restoration must wait"):
            api.close()
    else:
        assert api.close() == {}
