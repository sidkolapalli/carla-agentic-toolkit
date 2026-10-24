"""Asynchronous-only Traffic Manager policy and global mutation observers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from carla_agentic_toolkit.actor_creation import SpawnObservers
from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.world_timing import world_synchronous_mode

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaWorld


class BeforeTrafficManagerSetting(Protocol):
    """Observe one attempted global setter on a bound Traffic Manager endpoint."""

    def __call__(self, *, setting: str, value: object) -> None:
        """Persist the field before its native setter."""
        ...


class TrafficManagerSettingObserver(Protocol):
    """Observe global setters across the controller's changing endpoint requests."""

    def __call__(self, port: int, *, setting: str, value: object) -> None:
        """Persist one attempted field for this pass's endpoint."""
        ...


class TrafficMaintenanceObservers(SpawnObservers, total=False):
    """Optional hooks for native creation and per-pass global settings attempts."""

    before_setting: BeforeTrafficManagerSetting | None


def require_async_density_mode(*, synchronous_mode: bool) -> None:
    """Reject TM mutations when the world has a synchronous tick owner."""
    if not isinstance(synchronous_mode, bool):
        message = "CARLA synchronous_mode must be a boolean."
        raise CarlaAdapterError(message)
    if synchronous_mode:
        message = (
            "Toolkit Traffic Manager operations require an asynchronous world; "
            "the session's single tick owner must retain world and Traffic Manager timing."
        )
        raise UnsupportedFeatureError(message)


def require_async_traffic_world(world: CarlaWorld) -> None:
    """Read authoritative settings without unrelated actor inventory."""
    require_async_density_mode(synchronous_mode=world_synchronous_mode(world))


def require_async_traffic_manager_request(*, synchronous_mode: bool | None) -> None:
    """Refuse a synchronous TM request before any of its earlier global setters."""
    if synchronous_mode is not None and not isinstance(synchronous_mode, bool):
        message = "Traffic Manager synchronous_mode must be a boolean or None."
        raise CarlaAdapterError(message)
    if synchronous_mode is True:
        message = (
            "Toolkit-owned Traffic Manager sidecars must remain asynchronous; "
            "synchronous Traffic Manager hosting is tracked in issue #26."
        )
        raise UnsupportedFeatureError(message)
