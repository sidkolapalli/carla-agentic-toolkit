"""Best-effort CARLA version diagnostics and managed release verification."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import CarlaAdapterError

if TYPE_CHECKING:
    from collections.abc import Callable

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


def read_version_info(
    client: object, *, on_error: Callable[[Exception], bool] | None = None
) -> VersionInfo:
    """Read both native version getters without reconnecting or retrying."""
    client_version, stopped = _read_version(client, "get_client_version", on_error)
    server_version = None if stopped else _read_version(client, "get_server_version", on_error)[0]
    return VersionInfo(
        client_version=client_version,
        server_version=server_version,
    )


def require_matching_release(info: VersionInfo) -> None:
    """Refuse managed startup unless both numeric release prefixes match."""
    if warnings := info.warnings:
        raise CarlaAdapterError(warnings[0])


def _read_version(
    client: object, getter_name: str, on_error: Callable[[Exception], bool] | None
) -> tuple[str | None, bool]:
    try:
        getter = getattr(client, getter_name, None)
        if not callable(getter):
            return None, False
        value = getter()
    except Exception as exc:  # noqa: BLE001 - diagnostics retain independent default reads.
        return None, on_error(exc) if on_error is not None else False
    return (value if isinstance(value, str) else None), False


def _release_prefix(version: str | None) -> str | None:
    if version is None:
        return None
    match = _RELEASE_PREFIX.match(version)
    return match.group(1) if match is not None else None


def _display_version(version: str | None) -> str:
    return repr(version) if version is not None else "unavailable"
