"""Read-only settings verification for an externally replaced managed episode."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit.managed_world import (
    SETTINGS_FIELDS,
    SessionInvariantError,
    world_identity,
    world_settings,
)

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld


def check_replaced_world(
    client: CarlaClient,
    current: CarlaWorld,
    original: dict[str, object],
    failures: list[str],
) -> dict[str, object]:
    """Check an external episode without applying old-world mutation authority."""
    report = {"world_replaced": True, "settings_checked": False, "settings_restored": False}
    try:
        values = _replacement_settings(client, current)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        failures.append(f"Replacement world settings check failed: {exc}")
        return report
    restored = _settings_match(values, original)
    if not restored:
        failures.append("Replacement world settings differ; old-episode restoration refused.")
    return report | {"settings_checked": True, "settings_restored": restored}


def _replacement_settings(client: CarlaClient, current: CarlaWorld) -> dict[str, object]:
    """Bind settings reads to one stable actual replacement episode."""
    identity = world_identity(current)
    values = world_settings(current)
    if world_identity(current) != identity or world_identity(client.get_world()) != identity:
        message = "The replacement world changed while its settings were checked."
        raise SessionInvariantError(message)
    return values


def _settings_match(current: dict[str, object], original: dict[str, object]) -> bool:
    return all(
        name in original
        and type(current[name]) is type(original[name])
        and current[name] == original[name]
        for name in SETTINGS_FIELDS
    )
