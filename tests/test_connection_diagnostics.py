"""Diagnose sandbox TM permissions and native client availability before RPCs."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter_connection as adapter_module
from carla_agentic_toolkit import traffic_runtime
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.sandbox_helpers import sandbox_runner_path

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient

PORT_POLICY = "CARLA_AGENTIC_TOOLKIT_TCP_CONNECT_PORTS"


@pytest.mark.skipif(sys.platform != "linux", reason="requires the Linux sandbox runner")
@pytest.mark.parametrize("ports", [[], [2000, 8500]])
def test_real_runner_exposes_actual_connect_rules_not_inherited_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ports: list[int],
) -> None:
    """Preflight evidence comes from enforced rules; unrelated parent env stays scrubbed."""
    monkeypatch.setenv(PORT_POLICY, "[65535]")
    monkeypatch.setenv("CARLA_DIAGNOSTIC_PARENT_SENTINEL", "not inherited")
    probe = tmp_path / "port_probe.py"
    probe.write_text(
        "#!/usr/bin/python3\n"
        "import json, os\n"
        f"ports = os.environ.get({PORT_POLICY!r})\n"
        "result = {'ports': json.loads(ports) if ports is not None else None, "
        "'parent': os.environ.get('CARLA_DIAGNOSTIC_PARENT_SENTINEL')}\n"
        "print(json.dumps({'ok': True, 'result': result}))\n",
        encoding="utf-8",
    )
    probe.chmod(0o755)
    command = [
        str(sandbox_runner_path()),
        "--python",
        str(probe),
        "--module",
        "ignored",
        "--script",
        str(probe),
        "--work-dir",
        str(tmp_path),
        "--output-dir",
        str(tmp_path),
        "--read-only",
        "/usr",
        "--read-only",
        "/lib",
        "--read-write",
        str(tmp_path),
    ]
    for port in ports:
        command.extend(("--tcp-connect", str(port)))
    completed = subprocess.run(  # noqa: S603 -- Fixed reviewed runner and local probe.
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = cast("dict[str, object]", json.loads(completed.stdout))
    assert payload["ok"] is True
    assert payload["result"] == {"ports": ports, "parent": None}
    assert payload["landlock"] == {
        "landlock": "available",
        "ruleset_enforced": True,
        "tcp_connect_ports": ports,
    }


TM_PORT = 8500


@pytest.mark.parametrize(
    "code",
    [
        'api.populate_traffic({"traffic_manager_port": 8500})',
        'api.set_autopilot({"actor_ids": [1], "traffic_manager_port": 8500})',
        'api.configure_traffic_manager({"traffic_manager_port": 8500})',
        'api.tune_traffic_vehicle(1, {"desired_speed_kmh": 20.0}, traffic_manager_port=8500)',
        'api.set_traffic_vehicle_path(1, {"route": ["Straight"], "traffic_manager_port": 8500})',
        'api.start_traffic_controller({"traffic_manager_port": 8500})',
        'api.set_traffic_density({"traffic_manager_port": 8500})',
        'api.set_vehicle_behavior({"actor_ids": [1], "profile": "cautious", '
        '"traffic_manager_port": 8500})',
    ],
)
def test_facade_rejects_unlisted_tm_port_before_any_native_connection(
    code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad TM port selection must not prepare actors or even connect to CARLA."""
    monkeypatch.setenv(PORT_POLICY, json.dumps([2000, 2001, 2002, 8000]))
    adapter = PythonCarlaAdapter()
    native = Mock(side_effect=AssertionError("native connection happened"))
    monkeypatch.setattr(adapter, "_client", native)
    api = CarlaScriptApi(adapter, RunSnapshots())
    result = eval(code, {"api": api})  # noqa: S307 -- Fixed parameterized trusted test expressions.
    assert {key: result[key] for key in ("ok", "error_type", "retryable")} == {
        "ok": False,
        "error_type": "traffic_manager_port_not_allowed",
        "retryable": False,
    }
    assert "8500" in result["error"]
    assert "traffic_manager_ports" in result["error"]
    native.assert_not_called()


@pytest.mark.parametrize(
    "message", ["failed to create because of bind error.", "BIND ERROR: denied"]
)
def test_native_tm_bind_failure_has_specific_actionable_error(message: str) -> None:
    """A native creation failure identifies the port and the pre-existing sidecar requirement."""
    client = Mock()
    client.get_trafficmanager.side_effect = RuntimeError(message)
    with pytest.raises(CarlaAdapterError) as caught:
        traffic_runtime.traffic_manager(cast("CarlaClient", client), TM_PORT)
    assert (type(caught.value).__name__, caught.value.details["error_type"]) == (
        "TrafficManagerUnavailableError",
        "traffic_manager_unavailable",
    )
    assert "8500" in str(caught.value)
    assert "sidecar" in str(caught.value)
    assert "bind error" in str(caught.value).lower()


@pytest.mark.parametrize("message", ["RPC timed out", "native API rejected request"])
def test_other_tm_failures_keep_the_original_diagnostic(message: str) -> None:
    """Unrelated RPC errors must not be falsely described as missing sidecars."""
    client = Mock()
    client.get_trafficmanager.side_effect = RuntimeError(message)
    with pytest.raises(CarlaAdapterError, match=message) as caught:
        traffic_runtime.traffic_manager(cast("CarlaClient", client), TM_PORT)
    assert type(caught.value) is CarlaAdapterError


@pytest.mark.parametrize(
    "error",
    [ImportError("libCarla.so: missing native symbol"), TypeError("unsupported Python ABI")],
)
def test_health_preserves_client_import_error_and_distinguishes_it_from_connection(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native load and interpreter incompatibility need their own diagnosis."""
    factory = Mock(side_effect=error)
    monkeypatch.setattr(adapter_module, "_carla_client_factory", factory)
    result = CarlaScriptApi(PythonCarlaAdapter(), RunSnapshots()).health_check()
    assert result["ok"] is False
    assert result["error_type"] == "carla_api_unavailable"
    assert str(error) in str(result["error"])
    assert result["retryable"] is False


@pytest.mark.parametrize(
    "policy", ["not json", "null", "{}", "[false]", "[0]", "[65536]", '["8500"]']
)
def test_malformed_kernel_port_evidence_fails_closed_before_tm_lookup(
    policy: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreadable permission evidence must not silently become unrestricted access."""
    monkeypatch.setenv(PORT_POLICY, policy)
    client = Mock()
    with pytest.raises(CarlaAdapterError) as caught:
        traffic_runtime.traffic_manager(cast("CarlaClient", client), TM_PORT)
    assert caught.value.details["error_type"] == "traffic_manager_network_policy_error"
    client.get_trafficmanager.assert_not_called()


@pytest.mark.parametrize("ports", [None, [8000, 8500]])
def test_trusted_native_or_explicitly_allowlisted_tm_lookup_remains_available(
    ports: list[int] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The absence of a sandbox policy does not restrict trusted native callers."""
    if ports is None:
        monkeypatch.delenv(PORT_POLICY, raising=False)
    else:
        monkeypatch.setenv(PORT_POLICY, json.dumps(ports))
    client = Mock()
    assert (
        traffic_runtime.traffic_manager(cast("CarlaClient", client), TM_PORT)
        is client.get_trafficmanager.return_value
    )
    client.get_trafficmanager.assert_called_once_with(TM_PORT)
