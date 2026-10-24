"""Persistent TM connect permissions remain explicit, bounded and immutable."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import sandbox_session_process
from carla_agentic_toolkit.session_protocol import SessionConfig, read_message

if TYPE_CHECKING:
    import subprocess

    from carla_agentic_toolkit.sandbox import RunnerCommandRequest


@pytest.mark.parametrize("ports", [[], [8500], [1, 65535], [8500, 8500]])
def test_session_tm_ports_parse_as_independent_immutable_values(ports: list[int]) -> None:
    """JSON arrays cannot later change a running session's connect permissions."""
    config = SessionConfig.parse({"traffic_manager_ports": ports})
    expected = tuple(ports)
    ports.append(9000)
    assert config.traffic_manager_ports == expected


@pytest.mark.parametrize(
    "ports",
    [
        None,
        "8500",
        8500,
        {"port": 8500},
        [False],
        [True],
        [0],
        [-1],
        [65536],
        [8500.0],
        ["8500"],
        list(range(1, 18)),
    ],
)
def test_session_tm_ports_reject_malformed_or_unbounded_permissions(ports: object) -> None:
    """Each permission must satisfy the one-shot strict TCP port range."""
    with pytest.raises(ValueError, match="traffic_manager_ports"):
        SessionConfig.parse({"traffic_manager_ports": ports})


def test_session_default_does_not_add_tm_permissions() -> None:
    """Absent configuration retains the existing default connect policy."""
    assert SessionConfig.parse({}).traffic_manager_ports == ()


def test_session_accepts_the_bounded_port_count() -> None:
    """The last permitted list length is not accidentally rejected."""
    ports = list(range(1, 17))
    assert SessionConfig.parse({"traffic_manager_ports": ports}).traffic_manager_ports == tuple(
        ports
    )


def test_direct_session_config_rejects_mutable_permissions_before_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct trusted callers cannot retain a mutable list or acquire a lease first."""
    monkeypatch.setattr(
        sandbox_session_process, "SimulatorLease", lambda *_args: pytest.fail("lease acquired")
    )
    config = SessionConfig(traffic_manager_ports=cast("tuple[int, ...]", [8500]))
    with pytest.raises(TypeError, match="traffic_manager_ports"):
        sandbox_session_process.SessionProcess("invalid-tm-ports", config)


@pytest.mark.skipif(sys.platform != "linux", reason="Simulator leases require Linux")
def test_session_forwards_tm_ports_to_execution_runner_and_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The trusted session owner forwards the same frozen permission list at all boundaries."""
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(sandbox_session_process, "_sandbox_runner", lambda: Path("runner"))
    requests: list[RunnerCommandRequest] = []

    def command(request: RunnerCommandRequest) -> list[str]:
        requests.append(request)
        return ["runner", "--module", "carla_agentic_toolkit.script_runner"]

    monkeypatch.setattr(sandbox_session_process, "_runner_command", command)
    monkeypatch.setattr(
        sandbox_session_process.subprocess,
        "Popen",
        lambda args, **_kwargs: cast(
            "subprocess.Popen[str]", SimpleNamespace(args=args, returncode=0)
        ),
    )
    config = SessionConfig.parse({"port": 29873, "traffic_manager_ports": [8500, 8501]})
    owner = sandbox_session_process.SessionProcess("tm-port-test", config)
    try:
        assert owner.request.traffic_manager_ports == (8500, 8501)
        owner.start()
        assert requests[0].traffic_manager_ports == (8500, 8501)
        assert read_message(owner.work / "session.json")["traffic_manager_ports"] == [8500, 8501]
    finally:
        owner.lease.__exit__(None, None, None)
