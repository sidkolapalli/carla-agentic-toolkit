"""Settings recovery keeps durable evidence until simulator read-back is verified."""

from __future__ import annotations

import json
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.managed_control_io import read_control
from carla_agentic_toolkit.script_settings import RunSettings, validate_world_settings
from tests.test_sync_settings import FakeSettings, FakeWorld, build_adapter

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld


def _capture(journal: RunSettings, world: FakeWorld) -> None:
    journal.capture_world(cast("CarlaWorld", world))


def _record(world: FakeWorld) -> dict[str, object]:
    return {
        "schema_version": 1,
        "world_id": world.id,
        "world_settings": asdict(world.settings),
        "traffic_managers": [],
    }


def _assert_failure(result: dict[str, object]) -> None:
    assert result["settings_restored"] is False
    assert result["failures"]


def _malformed_record(world: FakeWorld, damage: str) -> dict[str, object]:
    record = _record(world)
    manager: dict[str, object] = {"port": 8000, "original_mode": False, "applied_mode": True}
    variants = {
        "missing": {key: value for key, value in record.items() if key != "world_settings"},
        "unknown": record | {"unreviewed_key": True},
        "schema_boolean": record | {"schema_version": True},
        "manager_unknown": record | {"traffic_managers": [manager | {"unreviewed_key": True}]},
        "manager_port_boolean": record | {"traffic_managers": [manager | {"port": True}]},
    }
    return variants[damage]


@pytest.mark.parametrize("durable", [False, True])
def test_repeated_capture_restores_first_baseline(tmp_path: Path, *, durable: bool) -> None:
    """Successive sync transitions cannot overwrite a run's original settings."""
    world = FakeWorld()
    original = asdict(world.settings)
    journal = RunSettings(tmp_path / "settings.json" if durable else None)
    _capture(journal, world)
    world.settings.synchronous_mode = True
    world.settings.fixed_delta_seconds = 0.05
    _capture(journal, world)
    world.settings.no_rendering_mode = False
    _capture(journal, world)

    result = journal.restore(build_adapter(world))

    assert result["settings_restored"] is True
    assert asdict(world.settings) == original
    assert journal.pending() is False


@dataclass
class InterruptedWorld(FakeWorld):
    """Accept a sync update but lose its RPC response after the server applied it."""

    def apply_settings(self, settings: FakeSettings) -> int:
        """Inject an ambiguous failure only while enabling sync."""
        frame = super().apply_settings(settings)
        if settings.synchronous_mode:
            message = "connection lost after applying settings"
            raise RuntimeError(message)
        return frame


def test_interrupted_apply_can_be_recovered_by_another_journal_instance(tmp_path: Path) -> None:
    """An RPC response failure cannot discard the saved pre-mutation baseline."""
    world = InterruptedWorld()
    original = asdict(world.settings)
    path = tmp_path / "settings.json"
    journal = RunSettings(path)
    adapter = build_adapter(world)
    adapter._settings_journal = journal  # noqa: SLF001

    with pytest.raises(CarlaAdapterError, match="connection lost"):
        adapter.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)

    assert world.settings.synchronous_mode is True
    reopened = RunSettings(path, require_existing=True)
    result = reopened.restore(adapter)
    assert result["settings_restored"] is True
    assert asdict(world.settings) == original
    assert reopened.pending() is False


@dataclass
class RejectingWorld(FakeWorld):
    """Reject restoration either explicitly or by ignoring the server update."""

    failure: str = "mismatch"

    def apply_settings(self, settings: FakeSettings) -> int:
        """Record an attempted write while preserving the changed world settings."""
        self.applied.append(asdict(settings))
        if self.failure == "raise":
            message = "restore RPC failed"
            raise RuntimeError(message)
        return 42


@pytest.mark.parametrize("failure", ["raise", "mismatch"])
def test_failed_restore_keeps_original_evidence(tmp_path: Path, failure: str) -> None:
    """A rejected RPC or mismatched read-back must retain a retryable journal."""
    world = RejectingWorld(failure=failure)
    path = tmp_path / "settings.json"
    journal = RunSettings(path)
    _capture(journal, world)
    original_record = read_control(path)
    world.settings.synchronous_mode = True
    world.settings.fixed_delta_seconds = 0.05

    result = journal.restore(build_adapter(world))

    _assert_failure(result)
    assert world.settings.synchronous_mode is True
    assert read_control(path) == original_record
    assert journal.pending() is True


def test_unexpected_episode_change_refuses_capture_and_restore(tmp_path: Path) -> None:
    """Map-name similarity cannot authorize settings writes against another episode."""
    original_world = FakeWorld()
    path = tmp_path / "settings.json"
    journal = RunSettings(path)
    _capture(journal, original_world)
    original_record = read_control(path)
    replacement = FakeWorld(id=original_world.id + 1)

    with pytest.raises(CarlaAdapterError, match="World changed"):
        _capture(journal, replacement)
    result = journal.restore(build_adapter(replacement))

    assert result["settings_restored"] is False
    assert replacement.applied == []
    assert read_control(path) == original_record


def test_known_map_replacement_rebinds_episode_and_preserves_baseline(tmp_path: Path) -> None:
    """An explicit completed load retains the baseline under the new episode identity."""
    original_world = FakeWorld()
    original = asdict(original_world.settings)
    journal = RunSettings(tmp_path / "settings.json")
    _capture(journal, original_world)
    replacement = FakeWorld(
        id=original_world.id + 1,
        settings=FakeSettings(synchronous_mode=True, fixed_delta_seconds=0.05),
    )

    journal.rebind_world(cast("CarlaWorld", replacement))
    _capture(journal, replacement)
    result = journal.restore(build_adapter(replacement))

    assert result["settings_restored"] is True
    assert asdict(replacement.settings) == original


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("synchronous_mode", 1),
        ("substepping", "true"),
        ("no_rendering_mode", None),
        ("max_substeps", True),
        ("max_substeps", 1.5),
        ("max_substeps", 0),
        ("max_substep_delta_time", float("nan")),
        ("max_substep_delta_time", float("inf")),
        ("fixed_delta_seconds", True),
        ("fixed_delta_seconds", -0.01),
        pytest.param("fixed_delta_seconds", 10**400, id="delta-overflow"),
        pytest.param("max_substep_delta_time", 10**400, id="substep-overflow"),
    ],
)
def test_settings_validation_rejects_invalid_types_and_numbers(name: str, value: object) -> None:
    """Malformed fields are rejected before restoration can touch CARLA."""
    values = asdict(FakeSettings()) | {name: value}

    with pytest.raises(CarlaAdapterError):
        validate_world_settings(values)


def test_settings_validation_rejects_unknown_fields() -> None:
    """The restore contract cannot inject additional CARLA setting attributes."""
    with pytest.raises(CarlaAdapterError, match="exactly the six"):
        validate_world_settings(asdict(FakeSettings()) | {"unreviewed_setting": True})


@pytest.mark.parametrize(
    "damage", ["missing", "unknown", "schema_boolean", "manager_unknown", "manager_port_boolean"]
)
def test_malformed_journal_fails_closed_without_applying_settings(
    tmp_path: Path, damage: str
) -> None:
    """An incomplete or ambiguous restoration record cannot become successful cleanup."""
    world = FakeWorld()
    record = _malformed_record(world, damage)
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    journal = RunSettings(path, require_existing=True)
    adapter = build_adapter(world)
    traffic_calls: list[bool] = []
    adapter._connected_client = cast(  # noqa: SLF001
        "CarlaClient",
        SimpleNamespace(
            get_world=lambda: world,
            get_trafficmanager=lambda _port: SimpleNamespace(
                set_synchronous_mode=traffic_calls.append
            ),
        ),
    )

    result = journal.restore(adapter)

    _assert_failure(result)
    assert world.applied == []
    assert traffic_calls == []
    assert read_control(path) == record


def test_missing_required_journal_is_not_empty_success(tmp_path: Path) -> None:
    """Lost evidence must remain a failure even for a settings-only run."""
    world = FakeWorld()
    journal = RunSettings(tmp_path / "absent.json", require_existing=True)

    with pytest.raises(FileNotFoundError):
        journal.pending()
    result = journal.restore(build_adapter(world))

    _assert_failure(result)
    assert world.applied == []


@pytest.mark.parametrize("link", ["hardlink", "symlink"])
def test_linked_journal_is_rejected_without_restoration(tmp_path: Path, link: str) -> None:
    """Linked evidence cannot redirect settings recovery outside its private file."""
    world = FakeWorld()
    target = tmp_path / "original.json"
    target.write_text(json.dumps(_record(world)), encoding="utf-8")
    path = tmp_path / "settings.json"
    try:
        if link == "hardlink":
            path.hardlink_to(target)
        else:
            path.symlink_to(target)
    except OSError:
        pytest.skip("Host does not grant link creation")
    journal = RunSettings(path, require_existing=True)

    result = journal.restore(build_adapter(world))

    _assert_failure(result)
    assert world.applied == []


@pytest.mark.parametrize("link", ["hardlink", "symlink"])
def test_link_metadata_is_rejected_before_reading_or_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, link: str
) -> None:
    """File metadata validation rejects links even on hosts that cannot create them."""
    world = FakeWorld()
    path = tmp_path / "settings.json"
    metadata = SimpleNamespace(
        st_mode=stat.S_IFLNK if link == "symlink" else stat.S_IFREG,
        st_nlink=2 if link == "hardlink" else 1,
    )
    lstat = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda self: metadata if self == path else lstat(self))

    result = RunSettings(path, require_existing=True).restore(build_adapter(world))

    _assert_failure(result)
    assert world.applied == []
