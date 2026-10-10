"""Traffic Manager recovery records attempted globals, not unknowable prior values."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import script_recovery, script_settings
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.models import TrafficManagerRequest
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_script_recovery import journal as ownership_journal
from tests.test_sync_settings import FakeWorld

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld

DISTANCE = "global_distance_to_leading_vehicle"
SPEED = "global_percentage_speed_difference"
SEED = "seed"
SYNC = "synchronous_mode"
PORT = 8000
TARGETS: dict[str, object] = {DISTANCE: 2.0, SPEED: 0.0, SEED: 0, SYNC: False}
ATTEMPTED: dict[str, object] = {DISTANCE: 9.0, SPEED: 25.0, SEED: 23, SYNC: False}


@dataclass
class RecordingManager:
    """Record only native setters, including mutation followed by a lost reply."""

    calls: list[tuple[str, object]] = field(default_factory=list)
    fail_setting: str | None = None
    after_setting: Callable[[], None] | None = None
    traffic_light_resets: int = 0

    def get_port(self) -> int:
        """Expose the port needed by real runtime serialization."""
        return PORT

    def _write(self, setting: str, value: object) -> None:
        self.calls.append((setting, value))
        if self.after_setting is not None:
            self.after_setting()
        if self.fail_setting == setting:
            message = "Traffic Manager rejected restore"
            raise RuntimeError(message)

    def set_global_distance_to_leading_vehicle(self, value: float) -> None:
        """Match the global distance setter."""
        self._write(DISTANCE, value)

    def global_percentage_speed_difference(self, value: float) -> None:
        """Match the global speed setter."""
        self._write(SPEED, value)

    def set_random_device_seed(self, value: int) -> None:
        """CARLA also resets all traffic lights when setting its random seed."""
        self.traffic_light_resets += 1
        self._write(SEED, value)

    def set_synchronous_mode(self, value: bool) -> None:  # noqa: FBT001
        """Match the native TM mode setter, without inventing a getter."""
        self._write(SYNC, value)


@dataclass
class JournalCase:
    """Keep journal, world and native calls independently observable."""

    path: Path
    world: FakeWorld
    manager: RecordingManager
    adapter: PythonCarlaAdapter

    def fresh(self) -> RunSettings:
        """Recovery always reads durable evidence through a new instance."""
        return RunSettings(self.path, require_existing=True)


def build_case(tmp_path: Path, managers: list[dict[str, object]]) -> JournalCase:
    """Write production-shaped evidence without using the new capture method."""
    world = FakeWorld()
    manager = RecordingManager()
    path = tmp_path / SETTINGS_FILENAME
    write_control(
        path,
        {
            "schema_version": 1,
            "world_id": world.id,
            "world_settings": asdict(world.settings),
            "traffic_managers": managers,
        },
    )
    client = SimpleNamespace(get_world=lambda: world, get_trafficmanager=lambda _port: manager)
    adapter = PythonCarlaAdapter(settings_journal=RunSettings(path, require_existing=True))
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    return JournalCase(path, world, manager, adapter)


def entry(
    targets: dict[str, object], attempted: dict[str, object], *, port: int = PORT
) -> dict[str, object]:
    """Construct the sparse per-port restoration contract."""
    return {"port": port, "restore_targets": targets, "attempted_settings": attempted}


def capture(
    settings: RunSettings, world: FakeWorld, setting: str, value: object, *, port: int = PORT
) -> None:
    """Call the public per-field hook with its keyword-only contract."""
    settings.capture_traffic_manager_setting(
        cast("CarlaWorld", world), port, setting=setting, value=value
    )


@pytest.mark.parametrize("setting", list(TARGETS))
def test_capture_records_only_the_attempted_field(tmp_path: Path, setting: str) -> None:
    """Unwritten settings must not receive restoration writes on this port."""
    world = FakeWorld()
    path = tmp_path / SETTINGS_FILENAME
    settings = RunSettings(path)
    settings.initialize()

    capture(settings, world, setting, ATTEMPTED[setting])

    assert read_control(path)["traffic_managers"] == [
        entry({setting: TARGETS[setting]}, {setting: ATTEMPTED[setting]})
    ]
    assert RunSettings(path, require_existing=True).pending() is True


def test_capture_unions_fields_and_ports_without_recapturing_world_baseline(tmp_path: Path) -> None:
    """Later attempts update values while retaining the first baseline and canonical targets."""
    case = build_case(tmp_path, [])
    original = asdict(case.world.settings)
    capture(case.fresh(), case.world, DISTANCE, 9.0)
    case.world.settings.no_rendering_mode = False
    capture(case.fresh(), case.world, SEED, 23)
    capture(case.fresh(), case.world, DISTANCE, 5.0)
    capture(case.fresh(), case.world, SPEED, 20.0, port=PORT + 1)

    state = read_control(case.path)

    assert state["world_settings"] == original
    assert state["traffic_managers"] == [
        entry({DISTANCE: 2.0, SEED: 0}, {DISTANCE: 5.0, SEED: 23}),
        entry({SPEED: 0.0}, {SPEED: 20.0}, port=PORT + 1),
    ]


def test_legacy_mode_entry_upgrades_without_losing_its_restore_target(tmp_path: Path) -> None:
    """Old mode-only evidence remains meaningful when another field is attempted."""
    case = build_case(tmp_path, [{"port": PORT, "original_mode": False, "applied_mode": True}])

    capture(case.fresh(), case.world, DISTANCE, 9.0)

    assert read_control(case.path)["traffic_managers"] == [
        entry({SYNC: False, DISTANCE: 2.0}, {SYNC: True, DISTANCE: 9.0})
    ]


def test_legacy_mode_wrapper_uses_the_sparse_capture_contract(tmp_path: Path) -> None:
    """Existing controller callers can keep their mode-capture API."""
    case = build_case(tmp_path, [])

    case.fresh().capture_traffic_manager(cast("CarlaWorld", case.world), PORT, enabled=False)

    assert read_control(case.path)["traffic_managers"] == [entry({SYNC: False}, {SYNC: False})]


def test_failed_native_setter_records_only_the_attempted_prefix(tmp_path: Path) -> None:
    """A lost reply captures that field but cannot authorize never-reached later setters."""
    case = build_case(tmp_path, [])
    case.manager.fail_setting = SPEED

    with pytest.raises(CarlaAdapterError, match="rejected restore"):
        case.adapter.configure_traffic_manager(
            request=TrafficManagerRequest(
                global_distance_to_leading_vehicle=9.0,
                global_percentage_speed_difference=25.0,
                seed=23,
                synchronous_mode=False,
            )
        )

    assert case.manager.calls == [(DISTANCE, 9.0), (SPEED, 25.0)]
    assert read_control(case.path)["traffic_managers"] == [
        entry({DISTANCE: 2.0, SPEED: 0.0}, {DISTANCE: 9.0, SPEED: 25.0})
    ]


def test_journal_write_failure_prevents_first_and_later_native_setters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An observer I/O failure cannot become a successful runtime configuration."""
    case = build_case(tmp_path, [])

    def fail_write(_path: Path, _value: dict[str, object]) -> None:
        message = "durable settings write failed"
        raise OSError(message)

    monkeypatch.setattr(script_settings, "write_control", fail_write)

    with pytest.raises(OSError, match="durable settings write failed"):
        case.adapter.configure_traffic_manager(
            request=TrafficManagerRequest(
                global_distance_to_leading_vehicle=9.0,
                global_percentage_speed_difference=25.0,
                seed=23,
            )
        )

    assert case.manager.calls == []
    assert read_control(case.path)["traffic_managers"] == []


@pytest.mark.parametrize("setting", list(TARGETS))
def test_fresh_reader_restores_only_the_recorded_field(tmp_path: Path, setting: str) -> None:
    """Acknowledged declared targets replace getter claims and unrelated global writes."""
    targets = {setting: TARGETS[setting]}
    case = build_case(tmp_path, [entry(targets, {setting: ATTEMPTED[setting]})])

    report = case.fresh().restore(case.adapter)

    _assert_restored_global_report(report, targets)
    assert case.manager.calls == [(setting, TARGETS[setting])]
    assert case.fresh().pending() is False


def _assert_restored_global_report(report: dict[str, object], targets: dict[str, object]) -> None:
    assert (report["settings_restored"], report["failures"]) == (True, [])
    assert report["traffic_manager_restore_targets"] == [{"port": PORT, "restore_targets": targets}]
    assert report["traffic_manager_async_ports"] == ([PORT] if SYNC in targets else [])


def test_seed_restore_has_its_real_traffic_light_reset_side_effect(tmp_path: Path) -> None:
    """Seed zero is a declared write, not preservation of the unknown original seed."""
    case = build_case(tmp_path, [entry({SEED: 0}, {SEED: 23})])

    report = case.fresh().restore(case.adapter)

    assert report["settings_restored"] is True
    assert case.manager.calls == [(SEED, 0)]
    assert case.manager.traffic_light_resets == 1


def test_legacy_mode_only_entry_restores_no_other_global(tmp_path: Path) -> None:
    """Historical True attempts restore async without inventing distance, speed or seed writes."""
    case = build_case(tmp_path, [{"port": PORT, "original_mode": False, "applied_mode": True}])

    report = case.fresh().restore(case.adapter)

    assert (report["settings_restored"], report["failures"]) == (True, [])
    assert case.manager.calls == [(SYNC, False)]
    assert case.fresh().pending() is False


def test_failed_setter_preserves_the_full_sparse_record_for_retry(tmp_path: Path) -> None:
    """A failed setter may already have applied; no partial restoration is claimed."""
    case = build_case(tmp_path, [entry(TARGETS.copy(), ATTEMPTED.copy())])
    original = read_control(case.path)
    case.manager.fail_setting = SPEED

    report = case.fresh().restore(case.adapter)

    assert report["settings_restored"] is False
    assert "rejected restore" in str(report["failures"])
    assert case.manager.calls == [(DISTANCE, 2.0), (SPEED, 0.0)]
    _assert_no_world_write_and_pending(case)
    assert read_control(case.path) == original


def test_world_replacement_between_restore_fields_stops_all_later_writes(tmp_path: Path) -> None:
    """The old episode cannot authorize a second global setter or world ApplySettings."""
    case = build_case(tmp_path, [entry(TARGETS.copy(), ATTEMPTED.copy())])
    case.manager.after_setting = lambda: setattr(case.world, "id", case.world.id + 1)

    report = case.fresh().restore(case.adapter)

    assert report["settings_restored"] is False
    assert "World changed" in str(report["failures"])
    assert case.manager.calls == [(DISTANCE, 2.0)]
    _assert_no_world_write_and_pending(case)


def _assert_no_world_write_and_pending(case: JournalCase) -> None:
    assert case.world.applied == []
    assert case.fresh().pending() is True


def test_legacy_last_tm_setter_replacement_prevents_world_settings_write(tmp_path: Path) -> None:
    """A replacement after the final TM setter still cannot authorize old-world settings."""
    case = build_case(tmp_path, [{"port": PORT, "original_mode": False, "applied_mode": True}])
    case.manager.after_setting = lambda: setattr(case.world, "id", case.world.id + 1)

    report = case.fresh().restore(case.adapter)

    assert report["settings_restored"] is False
    assert case.manager.calls == [(SYNC, False)]
    assert case.world.applied == []
    assert case.fresh().pending() is True


def test_world_replacement_during_tm_lookup_prevents_first_setter(tmp_path: Path) -> None:
    """A read-only TM lookup cannot make an obsolete episode safe to mutate."""
    case = build_case(tmp_path, [{"port": PORT, "original_mode": False, "applied_mode": True}])

    def get_manager(_port: int) -> RecordingManager:
        case.world.id += 1
        return case.manager

    case.adapter._connected_client = cast(  # noqa: SLF001
        "CarlaClient", SimpleNamespace(get_world=lambda: case.world, get_trafficmanager=get_manager)
    )

    report = case.fresh().restore(case.adapter)

    assert report["settings_restored"] is False
    assert case.manager.calls == []
    assert case.world.applied == []
    assert case.fresh().pending() is True


@pytest.mark.parametrize("legacy", [False, True])
def test_duplicate_ports_fail_closed_before_native_access(tmp_path: Path, *, legacy: bool) -> None:
    """Conflicting duplicate entries must not expand child-supplied mutation authority."""
    manager = (
        {"port": PORT, "original_mode": False, "applied_mode": False}
        if legacy
        else entry({SEED: 0}, {SEED: 23})
    )
    case = build_case(tmp_path, [manager.copy(), manager.copy()])

    report = case.fresh().restore(case.adapter)

    assert report["settings_restored"] is False
    assert case.manager.calls == []
    assert case.world.applied == []


@pytest.mark.parametrize(
    "manager",
    [
        entry({SEED: 99}, {SEED: 23}),
        entry({SYNC: True}, {SYNC: False}),
        entry({"unreviewed": 0}, {"unreviewed": 1}),
        entry({}, {}),
        entry({SEED: 0}, {DISTANCE: 9.0}),
        entry({SEED: 0}, {SEED: True}),
        entry({DISTANCE: 2.0}, {DISTANCE: "nan"}),
        entry({SYNC: False}, {SYNC: 0}),
    ],
)
def test_unreviewed_sparse_metadata_never_authorizes_setters(
    tmp_path: Path, manager: dict[str, object]
) -> None:
    """Only paired known fields, canonical targets and native-safe values are trusted."""
    case = build_case(tmp_path, [manager])

    report = case.fresh().restore(case.adapter)

    assert report["settings_restored"] is False
    assert case.manager.calls == []
    assert case.world.applied == []


@pytest.mark.skipif(sys.platform != "linux", reason="Trusted recovery requires Linux")
def test_tm_restore_failure_keeps_endpoint_dirty_with_no_owned_actors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings-only recovery cannot release quarantine after a global setter failure."""
    ownership = ownership_journal(tmp_path, monkeypatch, [])
    case = build_case(ownership.parent, [entry(TARGETS.copy(), ATTEMPTED.copy())])
    case.manager.fail_setting = SPEED
    state = tmp_path / "lease"
    with SimulatorLease("localhost", 3000, state_root=state) as lease:
        lease.mark_dirty(
            {"kind": "script", "ownership_path": str(ownership), "settings_path": str(case.path)}
        )
    _inline_recovery(monkeypatch, case.adapter)

    result = script_recovery.recover_script_ownership("localhost", 3000, state_root=state)

    assert result["ok"] is False
    assert case.manager.calls == [(DISTANCE, 2.0), (SPEED, 0.0)]
    assert case.fresh().pending() is True
    with SimulatorLease("localhost", 3000, state_root=state, recovering=True) as lease:
        assert lease.recovery_state


def _inline_recovery(monkeypatch: pytest.MonkeyPatch, adapter: PythonCarlaAdapter) -> None:
    from carla_agentic_toolkit import adapter as adapter_module  # noqa: PLC0415

    monkeypatch.setattr(adapter_module, "PythonCarlaAdapter", lambda **_kwargs: adapter)

    def spawn(job: Path, _descriptor: int) -> SimpleNamespace:
        report, _owner = script_recovery._worker_cleanup(read_control(job / "request.json"))  # noqa: SLF001
        write_control(job / "result.json", report)
        return SimpleNamespace(wait=lambda **_kwargs: 0)

    monkeypatch.setattr(script_recovery, "_spawn_cleanup", spawn)
    monkeypatch.setattr(script_recovery, "terminate_tree", lambda _process: None)
