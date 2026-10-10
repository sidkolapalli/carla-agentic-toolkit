"""Durable settings restoration shared by script runners and trusted recovery."""

from __future__ import annotations

import math
import os
import stat
import threading
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.managed_control_io import read_control, write_control
from carla_agentic_toolkit.managed_world import (
    SETTINGS_FIELDS,
    apply_world_settings,
    world_identity,
    world_settings,
)
from carla_agentic_toolkit.ownership import cleanup_report
from carla_agentic_toolkit.traffic_manager_settings import (
    record_manager_setting,
    restore_manager_settings,
    validate_manager_entries,
    validate_manager_setting,
)

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.carla_protocols import CarlaWorld

SETTINGS_FILENAME = "world-settings.json"


def validate_world_settings(values: dict[str, object]) -> None:
    """Validate the six-field contract without narrowing preexisting timing values."""
    if set(values) != set(SETTINGS_FIELDS):
        message = "World settings require exactly the six timing and rendering fields."
        raise CarlaAdapterError(message)
    _boolean_settings(values)
    _positive_number(values["max_substep_delta_time"], "max_substep_delta_time")
    _positive_integer(values["max_substeps"], "max_substeps")
    if values["fixed_delta_seconds"] is not None:
        _positive_number(values["fixed_delta_seconds"], "fixed_delta_seconds")


def _boolean_settings(values: dict[str, object]) -> None:
    for name in ("synchronous_mode", "no_rendering_mode", "substepping"):
        if type(values[name]) is not bool:
            message = f"World setting {name} must be a boolean."
            raise CarlaAdapterError(message)


def _positive_integer(value: object, name: str) -> None:
    if type(value) is not int or value <= 0:
        message = f"World setting {name} must be a positive integer."
        raise CarlaAdapterError(message)


def _positive_number(value: object, name: str) -> None:
    if not _finite_number(value) or cast("float", value) <= 0:
        message = f"World setting {name} must be a positive finite number."
        raise CarlaAdapterError(message)


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(cast("float", value))
    except OverflowError:
        return False


def _empty_state() -> dict[str, object]:
    return {"schema_version": 1, "world_id": None, "world_settings": None, "traffic_managers": []}


class RunSettings:
    """Retain the first baseline before mutation, including after child termination."""

    def __init__(self, path: Path | None, *, require_existing: bool = False) -> None:
        """Use an atomic durable file, or memory for a directly invoked finite runner."""
        self._path = path
        self._required = require_existing
        self._state = _empty_state()
        self._lock = threading.Lock()

    def initialize(self) -> None:
        """Publish intact empty evidence before the parent starts a sandbox."""
        with self._lock:
            self._save(_empty_state())

    def pending(self) -> bool:
        """Require readable evidence before claiming no settings remain to restore."""
        with self._lock:
            return self._load()["world_settings"] is not None

    def capture_world(self, world: CarlaWorld) -> None:
        """Flush the first six-field baseline before applying a world mutation."""
        with self._lock:
            state = self._load()
            _capture_world(state, world)
            self._save(state)

    def rebind_world(self, world: CarlaWorld) -> None:
        """Keep the baseline through an explicitly completed map replacement."""
        with self._lock:
            state = self._load()
            if state["world_settings"] is not None:
                state["world_id"] = world_identity(world)
                self._save(state)

    def capture_traffic_manager(
        self,
        world: CarlaWorld,
        port: int,
        enabled: bool,  # noqa: FBT001 - preserve the positional journal contract.
    ) -> None:
        """Keep the historical mode-only capture hook compatible."""
        self.capture_traffic_manager_setting(world, port, setting="synchronous_mode", value=enabled)

    def capture_traffic_manager_setting(
        self, world: CarlaWorld, port: int, *, setting: str, value: object
    ) -> None:
        """Flush one attempted global against its declared target before the setter."""
        validate_manager_setting(port, setting=setting, value=value)
        with self._lock:
            state = self._load()
            _capture_world(state, world)
            managers = cast("list[dict[str, object]]", state["traffic_managers"])
            record_manager_setting(managers, port, setting=setting, value=value)
            self._save(state)

    def restore(self, adapter: PythonCarlaAdapter) -> dict[str, object]:
        """Apply and reread the baseline; failed verification retains durable evidence."""
        try:
            with self._lock:
                return self._restore(adapter)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            failure: dict[str, object] = {"actor_id": None, "error": str(exc)}
            return cleanup_report(failures=(failure,)) | {"settings_restored": False}

    def _restore(self, adapter: PythonCarlaAdapter) -> dict[str, object]:
        state = self._load()
        original = state["world_settings"]
        if original is None:
            return cleanup_report() | {"settings_restored": True}
        client = adapter._client()  # noqa: SLF001 - trusted cleanup retains one native connection.
        world = client.get_world()
        _require_episode(state, world)
        managers = cast("list[dict[str, object]]", state["traffic_managers"])
        manager_report = restore_manager_settings(
            client, managers, require_episode=lambda: _require_episode(state, client.get_world())
        )
        values = cast("dict[str, object]", original)
        _require_episode(state, client.get_world())
        apply_world_settings(world, values)
        _require_episode(state, client.get_world())
        _verify_restored(world_settings(world), values)
        _require_episode(state, client.get_world())
        self._save(_empty_state())
        return cleanup_report() | {"settings_restored": True} | manager_report

    def _load(self) -> dict[str, object]:
        if self._path is None:
            return self._state.copy()
        try:
            _private_settings_file(self._path)
            value = read_control(self._path)
        except FileNotFoundError:
            if self._required:
                raise
            return _empty_state()
        _validate_state(value)
        return value

    def _save(self, state: dict[str, object]) -> None:
        if self._path is not None:
            write_control(self._path, state)
        self._state = state


def _capture_world(state: dict[str, object], world: CarlaWorld) -> None:
    if state["world_settings"] is None:
        values = world_settings(world)
        validate_world_settings(values)
        state.update(world_id=world_identity(world), world_settings=values)
    else:
        _require_episode(state, world)


def _require_episode(state: dict[str, object], world: CarlaWorld) -> None:
    if state["world_id"] != world_identity(world):
        message = "World changed before script settings restoration could be verified."
        raise CarlaAdapterError(message)


def _private_settings_file(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        message = "Settings journal must be a regular file with one link."
        raise ValueError(message)
    if os.name == "posix" and metadata.st_uid != os.getuid():
        message = "Settings journal must belong to this local user."
        raise ValueError(message)


def _validate_state(state: dict[str, object]) -> None:
    if set(state) != set(_empty_state()):
        message = "Settings journal requires exactly the reviewed restoration fields."
        raise ValueError(message)
    version = state["schema_version"]
    if type(version) is not int or version != 1:
        message = "Settings journal has an unsupported schema."
        raise ValueError(message)
    _validate_world_binding(state["world_id"], state["world_settings"])
    validate_manager_entries(state["traffic_managers"], state["world_settings"])


def _validate_world_binding(identity: object, values: object) -> None:
    if values is None:
        if identity is not None:
            message = "Empty settings journal cannot retain an unbound world identity."
            raise ValueError(message)
        return
    if type(identity) is not int or not isinstance(values, dict):
        message = "Settings journal requires settings bound to an integer world identity."
        raise ValueError(message)
    validate_world_settings(cast("dict[str, object]", values))


def _verify_restored(current: dict[str, object], original: dict[str, object]) -> None:
    if not all(_same_setting(current[name], original[name]) for name in SETTINGS_FIELDS):
        message = "Script world settings restoration could not be verified."
        raise CarlaAdapterError(message)


def _same_setting(current: object, original: object) -> bool:
    if _finite_number(current) and _finite_number(original):
        return math.isclose(cast("float", current), cast("float", original), rel_tol=1e-6)
    return type(current) is type(original) and current == original
