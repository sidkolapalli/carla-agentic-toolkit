"""Journaled timing and rendering operations for the Python CARLA adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.carla_versions import read_version_info
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.experiment_common import world_state
from carla_agentic_toolkit.managed_world import apply_world_settings
from carla_agentic_toolkit.managed_world import world_settings as capture_world_settings
from carla_agentic_toolkit.script_settings import validate_world_settings
from carla_agentic_toolkit.world_timing import world_synchronous_mode

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld
    from carla_agentic_toolkit.models import WorldState
    from carla_agentic_toolkit.script_settings import RunSettings

MAX_SYNC_DELTA_SECONDS = 0.1


class PythonCarlaSettingsMixin:
    """Keep world-setting mutation and capture under one adapter boundary."""

    _settings_journal: RunSettings | None

    def _client(self) -> CarlaClient:
        """Return a configured CARLA client."""
        raise NotImplementedError

    @staticmethod
    def _world(client: CarlaClient) -> CarlaWorld:
        """Return the current CARLA world."""
        raise NotImplementedError

    def _world_state(self, world: CarlaWorld, *, client: CarlaClient) -> WorldState:
        """Add version diagnostics using the operation's retained native client."""
        return world_state(world, warnings=read_version_info(client).warnings)

    def get_world_settings(self) -> dict[str, object]:
        """Return all timing and rendering settings for a later explicit restore."""
        try:
            return capture_world_settings(self._world(self._client()))
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc

    def get_synchronous_mode(self) -> bool:
        """Observe only the world's current mode before a lifecycle or batch call."""
        return world_synchronous_mode(self._world(self._client()))

    def set_sync_mode(
        self, *, enabled: bool, fixed_delta_seconds: float | None = None
    ) -> WorldState:
        """Configure synchronous mode and fixed timestep."""
        client = self._client()
        world = self._world(client)
        try:
            values = capture_world_settings(world) | {
                "synchronous_mode": enabled,
                "fixed_delta_seconds": fixed_delta_seconds,
            }
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        validate_world_settings(values)
        _validate_sync_delta(values)
        if self._settings_journal is not None:
            self._settings_journal.capture_world(world)
        try:
            apply_world_settings(world, values)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        return self._world_state(world, client=client)

    def restore_world_settings(self, settings: dict[str, object]) -> WorldState:
        """Restore a complete settings snapshot after capturing the current baseline."""
        validate_world_settings(settings)
        client = self._client()
        world = self._world(client)
        if self._settings_journal is not None:
            self._settings_journal.capture_world(world)
        try:
            apply_world_settings(world, settings)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        return self._world_state(world, client=client)

    def capture_traffic_manager_sync(self, port: int, *, enabled: bool) -> None:
        """Keep the historical mode-only controller hook compatible."""
        self.capture_traffic_manager_setting(port, setting="synchronous_mode", value=enabled)

    def capture_traffic_manager_setting(self, port: int, *, setting: str, value: object) -> None:
        """Journal the exact global field immediately before its native setter."""
        if self._settings_journal is not None:
            self._settings_journal.capture_traffic_manager_setting(
                self._world(self._client()), port, setting=setting, value=value
            )


def _validate_sync_delta(settings: dict[str, object]) -> None:
    """Require validated new sync settings to respect the fixed-step limit."""
    if settings["synchronous_mode"] is not True:
        return
    delta = settings["fixed_delta_seconds"]
    if delta is None or cast("float", delta) > MAX_SYNC_DELTA_SECONDS:
        message = "Sync mode requires an explicit finite fixed_delta_seconds > 0 and <= 0.1."
        raise CarlaAdapterError(message)
    _validate_substep_budget(settings, cast("float", delta))


def _validate_substep_budget(settings: dict[str, object], delta: float) -> None:
    """Check the world's enabled substep budget after its field types are validated."""
    if settings["substepping"] is not True:
        return
    substep_delta = cast("float", settings["max_substep_delta_time"])
    substeps = cast("int", settings["max_substeps"])
    if delta > substep_delta * substeps:
        message = "fixed_delta_seconds exceeds the world's physical substep budget."
        raise CarlaAdapterError(message)
