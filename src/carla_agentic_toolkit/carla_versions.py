"""Best-effort CARLA version diagnostics and managed release verification."""

from __future__ import annotations

import re
from dataclasses import dataclass

from carla_agentic_toolkit.errors import CarlaAdapterError

_RELEASE_PREFIX = re.compile(r"^([0-9]+\.[0-9]+\.[0-9]+)")


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """Retain the native full version strings independently of compatibility."""

    client_version: str | None
    server_version: str | None

    @property
    def warnings(self) -> tuple[str, ...]:
        """Report incompatible or unverified releases without guessing a version."""
        client_release = _release_prefix(self.client_version)
        server_release = _release_prefix(self.server_version)
        versions = (
            f"client version: {_display_version(self.client_version)}; "
            f"server version: {_display_version(self.server_version)}."
        )
        if client_release is None or server_release is None:
            return (f"CARLA client/server compatibility is unknown; {versions}",)
        if client_release != server_release:
            return (f"CARLA client/server release mismatch; {versions}",)
        return ()


def read_version_info(client: object) -> VersionInfo:
    """Read both native version getters without reconnecting or retrying."""
    return VersionInfo(
        client_version=_read_version(client, "get_client_version"),
        server_version=_read_version(client, "get_server_version"),
    )


def require_matching_release(info: VersionInfo) -> None:
    """Refuse managed startup unless both numeric release prefixes match."""
    if warnings := info.warnings:
        raise CarlaAdapterError(warnings[0])


def _read_version(client: object, getter_name: str) -> str | None:
    try:
        getter = getattr(client, getter_name, None)
        if not callable(getter):
            return None
        value = getter()
    except Exception:  # noqa: BLE001 - diagnostics cannot erase an acknowledged native result.
        return None
    return value if isinstance(value, str) else None


def _release_prefix(version: str | None) -> str | None:
    if version is None:
        return None
    match = _RELEASE_PREFIX.match(version)
    return match.group(1) if match is not None else None


def _display_version(version: str | None) -> str:
    return repr(version) if version is not None else "unavailable"
