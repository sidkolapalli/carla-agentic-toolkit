"""Restore managed settings only after reload, actor, and local-host authority checks."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from carla_agentic_toolkit.managed_replacement import check_replaced_world
from carla_agentic_toolkit.managed_setup import require_setup_episode
from carla_agentic_toolkit.managed_world import (
    apply_world_settings,
    world_identity,
    world_settings,
)

if TYPE_CHECKING:
    from carla_agentic_toolkit.managed_session import ManagedSession


class ManagedCleanupMixin:
    """Keep cleanup ordering separate from runtime frame ownership."""

    def _restore_world(self: ManagedSession, failures: list[str]) -> dict[str, object]:
        if self.reload.failures():
            return {
                "world_replaced": None,
                "world_identity_checked": False,
                "settings_restored": False,
                "reload_unresolved": True,
            }
        current = self.client.get_world()
        if self._recovering and self.background.failures():
            return {
                "world_replaced": world_identity(current) != self.world_id,
                "settings_restored": False,
            }
        if world_identity(current) != self.world_id:
            self.background.close_host(failures)
            return check_replaced_world(self.client, current, self._original_settings, failures)
        return self._restore_current_world(failures)

    def _restore_current_world(self: ManagedSession, failures: list[str]) -> dict[str, object]:
        if self._recovering:
            self._refresh_recovery_snapshot()
        self._destroy_owned(failures)
        if world_identity(self.client.get_world()) != self.world_id:
            message = "The world was replaced during actor cleanup; settings restore refused."
            failures.append(message)
            return {"world_replaced": True, "settings_restored": False}
        if not self.background.close_host(failures):
            return {
                "world_replaced": None,
                "world_identity_checked": False,
                "settings_restored": False,
            }
        return self._restore_settings(failures)

    def _restore_settings(self: ManagedSession, failures: list[str]) -> dict[str, object]:
        """Recheck episode authority after every cleanup RPC and before settings writes."""
        if world_identity(self.client.get_world()) != self.world_id:
            failures.append(
                "The world was replaced during host shutdown; settings restore refused."
            )
            return {"world_replaced": True, "settings_restored": False}
        apply_world_settings(
            self.world,
            self._original_settings,
            before_apply=partial(require_setup_episode, self.client, self.world_id),
        )
        restored = world_settings(self.world) == self._original_settings
        if not restored:
            failures.append("World settings restoration could not be verified.")
        return {
            "world_replaced": False,
            "settings_restored": restored,
            "recovery_frame": self._recovery_frame,
        }
