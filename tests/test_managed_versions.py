"""Managed startup records version evidence before enforcing release compatibility."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import managed_engine
from carla_agentic_toolkit.experiment_trace import TraceStore, load_trace
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.test_managed_engine import FakeExperiment
from tests.test_managed_session import FakeWorld

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.experiment_trace import TraceRead

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux lease locks.")


@dataclass
class StartupWorld(FakeWorld):
    """Expose native writes and frame progression while retaining realistic settings."""

    mutations: list[str] = field(default_factory=list)

    def apply_settings(self, settings: SimpleNamespace) -> int:
        """Record settings writes, including later restoration."""
        self.mutations.append("settings")
        return super().apply_settings(settings)

    def tick(self) -> int:
        """Record client-requested scheduled frames."""
        self.mutations.append("tick")
        return super().tick()

    def wait_for_tick(self, _seconds: float) -> SimpleNamespace:
        """Record asynchronous preflight progression too."""
        self.mutations.append("wait")
        return super().wait_for_tick(_seconds)


@dataclass
class StartupBoundary:
    """Hold observable production startup dependencies without replacing the entrypoint."""

    world: StartupWorld
    client: SimpleNamespace
    session_factory: Mock
    policy_factory: Mock


def _boundary(
    monkeypatch: pytest.MonkeyPatch, client_value: object, server_value: object
) -> StartupBoundary:
    world = StartupWorld()
    client = SimpleNamespace(
        get_world=Mock(return_value=world),
        reload_world=world.reload_world,
        get_client_version=Mock(return_value=client_value),
        get_server_version=Mock(return_value=server_value),
    )
    session = Mock(wraps=managed_engine.ManagedSession)
    policy = Mock(return_value=managed_engine.RulesPolicy())
    monkeypatch.setattr(managed_engine, "connect_client", lambda _spec: client)
    monkeypatch.setattr(managed_engine, "ManagedSession", session)
    monkeypatch.setattr(managed_engine, "create_policy", policy)
    monkeypatch.setattr(managed_engine, "build_experiment", FakeExperiment)
    return StartupBoundary(world, client, session, policy)


def _run(tmp_path: Path) -> tuple[dict[str, object], TraceRead]:
    result = managed_engine.run_experiment(
        ExperimentSpec(max_steps=1),
        "version-startup",
        state_root=tmp_path,
        cancelled=lambda: False,
        publish_status=lambda _status: None,
    )
    return result, load_trace(tmp_path / "runs/version-startup/events.jsonl")


def _assert_not_started(
    boundary: StartupBoundary, result: dict[str, object], tmp_path: Path
) -> None:
    _assert_cleanup_not_started(result)
    boundary.session_factory.assert_not_called()
    boundary.policy_factory.assert_not_called()
    boundary.client.get_world.assert_not_called()
    assert boundary.world.mutations == []
    with SimulatorLease("127.0.0.1", 2000, state_root=tmp_path) as lease:
        assert not lease.recovery_state


def _assert_cleanup_not_started(result: dict[str, object]) -> None:
    assert result["state"] == "failed"
    assert result["ok"] is False
    assert result["cleanup"] == {"ok": True, "not_started": True}


def _trace_event(read: TraceRead, kind: str) -> dict[str, object]:
    return next(event for event in read.events if event["kind"] == kind)


def _assert_versions(read: TraceRead, client: str | None, server: str | None) -> None:
    assert read.complete
    metadata = _trace_event(read, "metadata")
    environment = cast("dict[str, dict[str, object]]", metadata["data"])["environment"]
    assert (environment["carla_client"], environment["carla_server"]) == (client, server)
    failure = _trace_event(read, "infrastructure_error")
    assert cast("int", metadata["sequence"]) < cast("int", failure["sequence"])
    assert metadata["world_generation"] == "unconnected"


@pytest.mark.parametrize(
    ("client", "server"),
    [
        ("0.9.16", "0.10.0"),
        ("0.9.16-dirty", "0.9.15-source-build"),
        (None, "0.9.16"),
        ("0.9.16", None),
        ("", "0.9.16"),
        ("unknown", "0.9.16"),
        ("fake", "fake"),
        ("0.9", "0.9"),
        (False, "0.9.16"),
    ],
)
def test_incompatible_or_unknown_versions_never_start_managed_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: object, server: object
) -> None:
    """Unknown compatibility must not bypass the same pre-mutation gate as mismatches."""
    boundary = _boundary(monkeypatch, client, server)
    result, read = _run(tmp_path)
    _assert_not_started(boundary, result, tmp_path)
    _assert_versions(read, client if isinstance(client, str) else None, cast("str | None", server))
    boundary.client.get_client_version.assert_called_once_with()
    boundary.client.get_server_version.assert_called_once_with()


@pytest.mark.parametrize("getter", ["get_client_version", "get_server_version"])
def test_missing_version_getter_preserves_the_available_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, getter: str
) -> None:
    """One unavailable getter must not prevent independent evidence from its peer."""
    boundary = _boundary(monkeypatch, "0.9.16-client-source", "0.9.16-server-source")
    delattr(boundary.client, getter)
    result, read = _run(tmp_path)
    _assert_not_started(boundary, result, tmp_path)
    client = None if getter == "get_client_version" else "0.9.16-client-source"
    server = None if getter == "get_server_version" else "0.9.16-server-source"
    _assert_versions(read, client, server)


@pytest.mark.parametrize("getter", ["get_client_version", "get_server_version"])
@pytest.mark.parametrize("error", [AttributeError, OSError, RuntimeError, TypeError, ValueError])
def test_failed_version_getter_preserves_the_available_peer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, getter: str, error: type[Exception]
) -> None:
    """Native read failures remain traceable and cannot begin trusted managed startup."""
    boundary = _boundary(monkeypatch, "0.9.16-client-source", "0.9.16-server-source")
    getattr(boundary.client, getter).side_effect = error("version read failed")
    result, read = _run(tmp_path)
    _assert_not_started(boundary, result, tmp_path)
    client = None if getter == "get_client_version" else "0.9.16-client-source"
    server = None if getter == "get_server_version" else "0.9.16-server-source"
    _assert_versions(read, client, server)
    boundary.client.get_client_version.assert_called_once_with()
    boundary.client.get_server_version.assert_called_once_with()


@pytest.mark.parametrize(
    ("client", "server"),
    [("0.9.16", "0.9.16"), ("0.9.16-client-source", "0.9.16-server-dirty")],
)
def test_matching_release_proceeds_through_the_real_entrypoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str, server: str
) -> None:
    """Known equal releases remain supported independently of startup event ordering."""
    boundary = _boundary(monkeypatch, client, server)
    result, read = _run(tmp_path)
    assert result["state"] == "completed"
    assert cast("dict[str, object]", result["cleanup"])["ok"] is True
    boundary.policy_factory.assert_called_once()
    boundary.session_factory.assert_called_once()
    assert "settings" in boundary.world.mutations
    assert read.complete


@pytest.mark.parametrize(
    ("client", "server"),
    [("0.9.16", "0.9.16"), ("0.9.16-client-source", "0.9.16-server-dirty")],
)
def test_matching_release_records_metadata_before_constructing_managed_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: str, server: str
) -> None:
    """Source suffixes keep their full evidence while sharing release compatibility."""
    boundary = _boundary(monkeypatch, client, server)

    def create_policy(_spec: ExperimentSpec, _root: Path) -> managed_engine.RulesPolicy:
        read = load_trace(tmp_path / "runs/version-startup/events.jsonl")
        metadata = next(event for event in read.events if event["kind"] == "metadata")
        environment = metadata["data"]["environment"]
        assert (environment["carla_client"], environment["carla_server"]) == (client, server)
        return managed_engine.RulesPolicy()

    boundary.policy_factory.side_effect = create_policy
    result, read = _run(tmp_path)
    assert result["state"] == "completed"
    assert cast("dict[str, object]", result["cleanup"])["ok"] is True
    boundary.policy_factory.assert_called_once()
    boundary.session_factory.assert_called_once()
    assert "settings" in boundary.world.mutations
    assert read.complete


def test_metadata_write_failure_cannot_start_managed_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Durable startup evidence is required even when both release strings match."""
    boundary = _boundary(monkeypatch, "0.9.16", "0.9.16")
    monkeypatch.setattr(
        TraceStore, "_append_bytes", Mock(side_effect=OSError("metadata persistence failed"))
    )
    result, read = _run(tmp_path)
    _assert_not_started(boundary, result, tmp_path)
    assert read.complete
    assert not any(event["kind"] == "metadata" for event in read.events)
