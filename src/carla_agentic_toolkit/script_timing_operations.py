"""Synchronous script stepping and controller shutdown before settings restoration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.script_operations import ScriptOperations
from carla_agentic_toolkit.script_operations import recover as _recover

if TYPE_CHECKING:
    from carla_agentic_toolkit.models import JsonObject
    from carla_agentic_toolkit.traffic_controller_service import InProcessTrafficControllerService


class ScriptTimingOperations(ScriptOperations):
    """Retain timing captures and stop background mutations before restoring them."""

    _traffic_controller: InProcessTrafficControllerService

    def _current_synchronous_mode(self) -> bool:
        """Read only settings when observing a lifecycle or batch mode transition."""
        return self._adapter.get_synchronous_mode()

    @staticmethod
    def _mode_change(payload: JsonObject, *, previous_mode: bool) -> JsonObject:
        """Expose the observed mode alongside its pre/post transition."""
        current_mode = _observed_mode(payload["settings"])
        return payload | {
            "synchronous_mode": current_mode,
            "synchronous_mode_changed": current_mode is not previous_mode,
        }

    def close(self) -> JsonObject:
        """Stop and join background traffic mutations before run settings are restored."""
        status = self._traffic_controller.stop()
        if status.active or status.stopping:
            message = "Traffic controller is still stopping; settings restoration must wait."
            raise CarlaAdapterError(message)
        return status.to_dict()

    @_recover("set_sync_mode_failed")
    def set_sync_mode(
        self,
        *,
        enabled: bool,
        fixed_delta_seconds: float | None = None,
    ) -> JsonObject:
        """Configure timing; synchronous settings require a fully stopped traffic controller."""
        self._require_stopped_controller_for_sync(synchronous_mode=enabled)
        previous_settings = self._adapter.get_world_settings()
        payload = self._adapter.set_sync_mode(
            enabled=enabled,
            fixed_delta_seconds=fixed_delta_seconds,
        ).to_dict()
        payload["previous_settings"] = previous_settings
        return self._snapshot("carla-snapshot://world/current", payload)

    @_recover("restore_world_settings_failed")
    def restore_world_settings(self, settings: dict[str, object]) -> JsonObject:
        """Restore all six settings; synchronous timing requires a fully stopped controller."""
        self._require_stopped_controller_for_sync(synchronous_mode=settings.get("synchronous_mode"))
        payload = self._adapter.restore_world_settings(settings).to_dict()
        return self._snapshot("carla-snapshot://world/current", payload)

    def _require_stopped_controller_for_sync(self, *, synchronous_mode: object) -> None:
        if synchronous_mode is not True:
            return
        status = self._traffic_controller.get_status()
        if status.active or status.stopping:
            message = (
                "Stop and fully join the traffic controller before enabling synchronous timing."
            )
            raise CarlaAdapterError(message)


def _observed_mode(settings: object) -> bool:
    """Reject an unknown mode rather than report a guessed transition."""
    if not isinstance(settings, dict):
        message = "A world-state result must include its observed settings."
        raise CarlaAdapterError(message)
    mode = settings.get("synchronous_mode")
    if not isinstance(mode, bool):
        message = "CARLA synchronous_mode must be a boolean."
        raise CarlaAdapterError(message)
    return mode
