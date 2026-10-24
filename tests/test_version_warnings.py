"""Version diagnostics never erase acknowledged standalone simulator operations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import experiment_environment
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.carla_versions import read_version_info
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_sync_settings import FakeSettings
from tests.test_traffic_mode_policy import ACTOR_ID, TrafficWorld

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.models import JsonObject

MATCHING_VERSION = "0.9.16"
CLIENT_SOURCE_VERSION = "0.9.16-client-source-dirty"
SERVER_SOURCE_VERSION = "0.10.0-server-source"
STATE_OPERATIONS = (
    "get_world_state",
    "load",
    "reload",
    "opendrive",
    "set_sync",
    "restore",
    "populate",
    "autopilot",
)
MUTATIONS = STATE_OPERATIONS[1:]
type VersionFailure = tuple[str, str, object, tuple[str | None, str | None]]
VERSION_FAILURES = (
    ("missing", "client", None, (None, MATCHING_VERSION)),
    ("missing", "server", None, (MATCHING_VERSION, None)),
    ("raising", "client", RuntimeError("client version unavailable"), (None, MATCHING_VERSION)),
    ("raising", "server", RuntimeError("server version unavailable"), (MATCHING_VERSION, None)),
    ("malformed", "client", "unknown-build", ("unknown-build", MATCHING_VERSION)),
    ("malformed", "server", "0.9", (MATCHING_VERSION, "0.9")),
    ("nonstring", "client", 16, (None, MATCHING_VERSION)),
)


@dataclass
class VersionClient:
    """Native acknowledgements and diagnostic reads have independent counters."""

    world: TrafficWorld = field(default_factory=lambda: TrafficWorld(settings=FakeSettings()))
    client_version: object = MATCHING_VERSION
    server_version: object = MATCHING_VERSION
    manager: Mock = field(default_factory=Mock)
    mutations: list[str] = field(default_factory=list)
    version_reads: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Expose the same endpoint used by the actual Traffic Manager helper."""
        self.manager.get_port.return_value = 8000

    def set_timeout(self, _seconds: float) -> None:
        """Keep native client plumbing without connecting to a simulator."""

    def get_world(self) -> TrafficWorld:
        """Return the currently observed episode."""
        return self.world

    def get_client_version(self) -> str:
        """Model a malformed fake return as an unavailable native diagnostic."""
        return self._version("client", self.client_version)

    def get_server_version(self) -> str:
        """Read independently even if the client diagnostic failed."""
        return self._version("server", self.server_version)

    def _version(self, source: str, value: object) -> str:
        self.version_reads.append(source)
        if isinstance(value, Exception):
            raise value
        return cast("str", value)

    def get_trafficmanager(self, _port: int) -> Mock:
        """Allow supported asynchronous mutations to reach their actual runtime."""
        return self.manager

    def load_world(self, _map_name: str, *, reset_settings: bool) -> TrafficWorld:
        """Acknowledge one explicit map mutation."""
        return self._replace("load", reset_settings=reset_settings)

    def reload_world(self, reset_settings: object) -> TrafficWorld:
        """Accept the actual positional native reload argument."""
        assert isinstance(reset_settings, bool)
        return self._replace("reload", reset_settings=reset_settings)

    def generate_opendrive_world(self, *, reset_settings: bool) -> TrafficWorld:
        """Acknowledge generation without importing the optional CARLA wheel."""
        return self._replace("opendrive", reset_settings=reset_settings)

    def _replace(self, operation: str, *, reset_settings: bool) -> TrafficWorld:
        self.mutations.append(operation)
        settings = FakeSettings() if reset_settings else replace(self.world.settings)
        self.world = TrafficWorld(id=self.world.id + 1, settings=settings)
        return self.world


@dataclass
class VersionCase:
    """Use actual adapter/facade methods, not a future helper import."""

    client: VersionClient
    api: CarlaScriptApi


def build_case(monkeypatch: pytest.MonkeyPatch) -> VersionCase:
    """Replace only native OpenDRIVE parameter construction."""
    client = VersionClient()
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    monkeypatch.setattr(
        experiment_environment,
        "generate_opendrive_world",
        lambda native, **kwargs: native.generate_opendrive_world(
            reset_settings=kwargs["reset_settings"]
        ),
    )
    return VersionCase(client, CarlaScriptApi(adapter, RunSnapshots()))


def invoke(case: VersionCase, operation: str) -> JsonObject:
    """Reach all adapter and mixin paths that return a WorldState."""
    operations = {
        "health_check": case.api.health_check,
        "get_world_state": case.api.get_world_state,
        "load": lambda: case.api.load_world("Town02", reset_settings=False),
        "reload": lambda: case.api.reload_world(reset_settings=False),
        "opendrive": lambda: case.api.generate_opendrive_world(
            "<OpenDRIVE/>", reset_settings=False
        ),
        "set_sync": lambda: case.api.set_sync_mode(enabled=False),
        "restore": lambda: case.api.restore_world_settings(asdict(case.client.world.settings)),
        "populate": lambda: case.api.populate_traffic({"vehicle_count": 1, "advance_world": False}),
        "autopilot": lambda: case.api.set_autopilot(
            {"actor_ids": [ACTOR_ID], "advance_world": False}
        ),
    }
    return operations[operation]()


def state_payload(result: JsonObject, operation: str) -> JsonObject:
    """Traffic workflows expose their state in the existing nested field."""
    return (
        cast("JsonObject", result["world_state"])
        if operation in ("populate", "autopilot")
        else result
    )


def assert_acknowledged(case: VersionCase, result: JsonObject, operation: str) -> None:
    """Diagnostic failures cannot cause another native mutation or lose success."""
    assert result.get("ok") is not False
    assertions = {
        "load": lambda: _assert_map_once(case, "load"),
        "reload": lambda: _assert_map_once(case, "reload"),
        "opendrive": lambda: _assert_map_once(case, "opendrive"),
        "set_sync": lambda: _assert_settings_once(case),
        "restore": lambda: _assert_settings_once(case),
        "populate": lambda: _assert_traffic_once(case, spawned=1),
        "autopilot": lambda: _assert_traffic_once(case, spawned=0),
    }
    assertions[operation]()


def _assert_map_once(case: VersionCase, operation: str) -> None:
    assert case.client.mutations == [operation]


def _assert_settings_once(case: VersionCase) -> None:
    assert len(case.client.world.applied) == 1


def _assert_traffic_once(case: VersionCase, *, spawned: int) -> None:
    assert case.client.world.spawns == spawned
    assert case.client.world.actor.set_autopilot.call_count == 1
    assert case.client.world.actor.set_autopilot.call_args.args == (True, 8000)


@pytest.mark.parametrize("operation", ["health_check", "get_world_state"])
@pytest.mark.parametrize(
    "versions",
    [
        (MATCHING_VERSION, MATCHING_VERSION),
        (CLIENT_SOURCE_VERSION, MATCHING_VERSION),
        (CLIENT_SOURCE_VERSION, "0.9.16-other-source"),
    ],
)
def test_equal_release_diagnostics_are_warning_free(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    versions: tuple[str, str],
) -> None:
    """Full source suffix strings remain visible without becoming a false mismatch."""
    case = build_case(monkeypatch)
    case.client.client_version, case.client.server_version = versions

    result = invoke(case, operation)

    assert result["warnings"] == []
    if operation == "health_check":
        assert (result["client_version"], result["server_version"]) == versions


@pytest.mark.parametrize("operation", ["health_check", *STATE_OPERATIONS])
def test_release_mismatch_reaches_every_state_result(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    """One state-producing path must not silently omit the client's version warning."""
    case = build_case(monkeypatch)
    case.client.client_version = CLIENT_SOURCE_VERSION
    case.client.server_version = SERVER_SOURCE_VERSION

    result = invoke(case, operation)

    warning = " ".join(cast("list[str]", state_payload(result, operation)["warnings"]))
    assert "mismatch" in warning.lower()
    assert CLIENT_SOURCE_VERSION in warning
    assert SERVER_SOURCE_VERSION in warning


def configure_failure(
    case: VersionCase,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    source: str,
    value: object,
) -> None:
    """Use absent getters and genuine raised exceptions, not helper-import failures."""
    if kind == "missing":
        monkeypatch.setattr(case.client, f"get_{source}_version", None)
    else:
        setattr(case.client, f"{source}_version", value)


@pytest.mark.parametrize("failure", VERSION_FAILURES)
@pytest.mark.parametrize("operation", MUTATIONS)
def test_unknown_versions_preserve_acknowledged_mutation(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure: VersionFailure,
) -> None:
    """Already acknowledged map, settings and traffic results survive diagnostic failure."""
    kind, source, value, available = failure
    case = build_case(monkeypatch)
    configure_failure(case, monkeypatch, kind, source, value)

    result = invoke(case, operation)

    assert_acknowledged(case, result, operation)
    assert_unknown_warning(state_payload(result, operation), available)
    other = "server" if source == "client" else "client"
    assert case.client.version_reads.count(other) == 1


@pytest.mark.parametrize("failure", VERSION_FAILURES)
def test_health_preserves_available_versions_without_unverified_world_observations(
    monkeypatch: pytest.MonkeyPatch,
    failure: VersionFailure,
) -> None:
    """Unknown compatibility preserves raw diagnostics without claiming world connectivity."""
    kind, source, value, available = failure
    case = build_case(monkeypatch)
    configure_failure(case, monkeypatch, kind, source, value)

    result = case.api.health_check()

    assert result["connected"] is False
    assert (result["current_map"], result["settings"], result["actor_counts"]) == (None, None, None)
    assert (result["client_version"], result["server_version"]) == available
    assert_unknown_warning(result, available)


def assert_unknown_warning(payload: JsonObject, available: tuple[str | None, str | None]) -> None:
    """Only known strings may be named; an unknown pair is not a mismatch."""
    warning = " ".join(cast("list[str]", payload["warnings"]))
    assert "unknown" in warning.lower()
    assert "mismatch" not in warning.lower()
    for version in available:
        _assert_available_version(warning, version)


def _assert_available_version(warning: str, version: str | None) -> None:
    """Require each independently available native string in the warning."""
    if version is not None:
        assert version in warning


def test_equal_malformed_versions_do_not_silently_assume_compatibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two identical opaque build strings do not establish a release prefix."""
    case = build_case(monkeypatch)
    case.client.client_version = case.client.server_version = "opaque-build"

    result = case.api.get_world_state()

    assert_unknown_warning(result, ("opaque-build", "opaque-build"))


def test_empty_native_version_string_remains_available_evidence() -> None:
    """An empty native string is unknown, but it is not a missing getter."""
    client = VersionClient(client_version="")

    info = read_version_info(client)

    assert info.client_version == ""
    assert info.server_version == MATCHING_VERSION
    assert "unknown" in info.warnings[0].lower()
    assert client.version_reads == ["client", "server"]


def test_version_diagnostics_do_not_swallow_process_interruptions() -> None:
    """Best-effort diagnostics catch ordinary failures, not process control."""
    client = Mock()
    client.get_client_version.side_effect = KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        read_version_info(client)

    client.get_server_version.assert_not_called()
