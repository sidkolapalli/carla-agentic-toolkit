"""Health diagnoses unsafe version pairs without entering the native episode handshake."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_sync_settings import FakeSettings
from tests.test_version_warnings import MATCHING_VERSION, VERSION_FAILURES, VersionClient

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.models import JsonObject
    from tests.test_traffic_mode_policy import TrafficWorld
    from tests.test_version_warnings import VersionFailure


class NativeHandshakeAbort(BaseException):
    """Represent the native incompatible-world process exit without killing pytest."""


@dataclass
class HealthClient(VersionClient):
    """Make an unsafe native world handshake unmistakable and observable."""

    world_error: BaseException | None = field(
        default_factory=lambda: NativeHandshakeAbort("unsafe native world handshake")
    )
    operations: list[str] = field(default_factory=list)

    def get_world(self) -> TrafficWorld:
        """Only compatible diagnostics are allowed to reach native episode attachment."""
        self.operations.append("world")
        if self.world_error is not None:
            raise self.world_error
        return self.world

    def _version(self, source: str, value: object) -> str:
        self.operations.append(source)
        return super()._version(source, value)


@dataclass
class HealthCase:
    """Keep the actual cached adapter, facade, and health snapshot together."""

    client: HealthClient
    adapter: PythonCarlaAdapter
    api: CarlaScriptApi
    snapshots: RunSnapshots
    reconnect: Mock


def _health_case(monkeypatch: pytest.MonkeyPatch, client: HealthClient) -> HealthCase:
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    reconnect = Mock(side_effect=AssertionError("health must retain its native client"))
    monkeypatch.setattr(adapter, "_connect", reconnect)
    snapshots = RunSnapshots()
    return HealthCase(client, adapter, CarlaScriptApi(adapter, snapshots), snapshots, reconnect)


def _assert_partial(result: JsonObject, *, compatibility: str) -> None:
    assert result["connected"] is False
    assert (result["current_map"], result["settings"], result["actor_counts"]) == (None, None, None)
    warnings = cast("list[str]", result["warnings"])
    assert compatibility in warnings[0].lower()
    _assert_world_inspection_skipped(warnings)


def _assert_world_inspection_skipped(warnings: list[str]) -> None:
    assert any(
        "world inspection" in warning.lower() and "skipped" in warning.lower()
        for warning in warnings
    )


def _assert_cached_snapshot(case: HealthCase, result: JsonObject) -> None:
    case.reconnect.assert_not_called()
    assert case.snapshots.read_snapshot("carla-snapshot://session/status") == result


@pytest.mark.parametrize(
    ("client_version", "server_version", "compatibility"),
    [
        ("0.10.0", "0.9.16", "mismatch"),
        ("0.9.16-client-dirty", "0.10.0-server-source", "mismatch"),
        ("opaque", "opaque", "unknown"),
        ("", MATCHING_VERSION, "unknown"),
    ],
)
def test_unverified_health_skips_native_world_handshake(
    monkeypatch: pytest.MonkeyPatch,
    client_version: str,
    server_version: str,
    compatibility: str,
) -> None:
    """Available version RPCs must suffice when episode attachment could abort the process."""
    case = _health_case(
        monkeypatch, HealthClient(client_version=client_version, server_version=server_version)
    )

    result = case.api.health_check()

    _assert_partial(result, compatibility=compatibility)
    assert (result["client_version"], result["server_version"]) == (client_version, server_version)
    assert case.client.operations == ["client", "server"]
    _assert_cached_snapshot(case, result)


@pytest.mark.parametrize("failure", VERSION_FAILURES)
def test_unknown_health_preserves_independent_available_versions(
    monkeypatch: pytest.MonkeyPatch, failure: VersionFailure
) -> None:
    """A missing or failed getter does not authorize a native world handshake."""
    kind, source, value, available = failure
    case = _health_case(monkeypatch, HealthClient())
    _configure_failure(case.client, monkeypatch, kind, source, value)

    result = case.api.health_check()

    _assert_partial(result, compatibility="unknown")
    assert (result["client_version"], result["server_version"]) == available
    expected = (
        ["server" if source == "client" else "client"]
        if kind == "missing"
        else ["client", "server"]
    )
    assert case.client.operations == expected
    _assert_cached_snapshot(case, result)


def _configure_failure(
    client: HealthClient,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    source: str,
    value: object,
) -> None:
    if kind == "missing":
        monkeypatch.setattr(client, f"get_{source}_version", None)
    else:
        setattr(client, f"{source}_version", value)


@pytest.mark.parametrize(
    ("client_version", "server_version"),
    [
        (MATCHING_VERSION, MATCHING_VERSION),
        ("0.9.16-client-dirty", MATCHING_VERSION),
        ("0.9.16-client-dirty", "0.9.16-server-source"),
    ],
)
def test_matching_health_retains_full_world_observations(
    monkeypatch: pytest.MonkeyPatch, client_version: str, server_version: str
) -> None:
    """Matching release prefixes still inspect the real world and preserve full source strings."""
    client = HealthClient(
        client_version=client_version, server_version=server_version, world_error=None
    )
    client.world.settings = FakeSettings(
        synchronous_mode=True, fixed_delta_seconds=0.05, no_rendering_mode=True
    )
    case = _health_case(monkeypatch, client)

    result = case.api.health_check()

    _assert_full_health(result)
    assert (result["client_version"], result["server_version"]) == (client_version, server_version)
    assert client.operations == ["client", "server", "world"]
    _assert_cached_snapshot(case, result)


def _assert_full_health(result: JsonObject) -> None:
    assert result["connected"] is True
    assert result["warnings"] == []
    _assert_world_observations(result)


def _assert_world_observations(result: JsonObject) -> None:
    assert result["current_map"] == "Town01"
    assert result["settings"] == {
        "synchronous_mode": True,
        "fixed_delta_seconds": 0.05,
        "no_rendering_mode": True,
        "substepping": True,
        "max_substeps": 10,
        "max_substep_delta_time": 0.01,
    }
    assert result["actor_counts"] == {"vehicles": 1, "walkers": 0, "sensors": 0, "traffic": 0}


def test_matching_health_world_failure_remains_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Version compatibility cannot fabricate a successful native world connection."""
    case = _health_case(monkeypatch, HealthClient(world_error=RuntimeError("world unavailable")))

    result = case.api.health_check()

    assert result["ok"] is False
    assert result["error_type"] == "carla_connection_error"
    assert "world unavailable" in str(result["message"])
    assert case.client.operations == ["client", "server", "world"]
    case.reconnect.assert_not_called()
    with pytest.raises(KeyError):
        case.snapshots.read_snapshot("carla-snapshot://session/status")


def test_adapter_health_returns_partial_without_facade_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native safety boundary belongs to the adapter, not exception recovery."""
    case = _health_case(monkeypatch, HealthClient(server_version="0.10.0"))

    result = case.adapter.health_check().to_dict()

    _assert_partial(result, compatibility="mismatch")
    assert case.client.operations == ["client", "server"]
    case.reconnect.assert_not_called()
