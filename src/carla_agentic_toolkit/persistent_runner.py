"""Long-lived generated-script worker, always launched inside the Rust sandbox."""

from __future__ import annotations

import json
import sys
import time
from typing import TYPE_CHECKING, Protocol

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.connection_journal import CONNECTION_FILENAME
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.persistent_namespace import PersistentNamespace, SessionSnapshots
from carla_agentic_toolkit.rpc_timeouts import RpcTimeoutPolicy, deadline_value
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.script_runner import _error, _parse_args
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
from carla_agentic_toolkit.session_protocol import (
    MAX_REQUEST_SECONDS,
    POLL_SECONDS,
    PROTOCOL_VERSION,
    ProtocolError,
    bounded_seconds,
    read_message,
    validate_code,
    write_message,
)

if TYPE_CHECKING:
    from pathlib import Path

MIN_TELEMETRY_INTERVAL_SECONDS = 0.1
MAX_TELEMETRY_INTERVAL_SECONDS = 10.0


class TelemetryApi(Protocol):
    """Only the trusted, non-ticking vehicle telemetry method is periodically sampled."""

    def get_vehicle_telemetry(self, actor_id: int) -> dict[str, object]:
        """Return bounded current state for one owned vehicle."""
        ...


class SessionLoop:
    """One ordered request slot and one replacing telemetry slot; no hidden tick owner."""

    def __init__(  # noqa: PLR0913 - optional policy preserves direct loop callers and parent deadlines.
        self,
        directory: Path,
        session_id: str,
        api: TelemetryApi,
        ownership: RunOwnership,
        snapshots: SessionSnapshots | None = None,
        *,
        rpc_timeout_policy: RpcTimeoutPolicy | None = None,
        request_timeout_seconds: float = 30.0,
        require_deadlines: bool = False,
    ) -> None:
        """Retain the same API, ownership journal, and user namespace for this worker."""
        self.directory = directory
        self.session_id = session_id
        self.api = api
        self.ownership = ownership
        self.namespace = PersistentNamespace(api, snapshots)
        self.rpc_timeout_policy = rpc_timeout_policy
        self.request_timeout_seconds = bounded_seconds(
            request_timeout_seconds, "request_timeout_seconds", MAX_REQUEST_SECONDS
        )
        self.require_deadlines = require_deadlines
        self.sequence = 0
        self.telemetry_sequence = 0
        self.actor_id: int | None = None
        self.interval = 0.2
        self.next_sample = 0.0

    def run(self) -> None:
        """Announce readiness then serve until the external absolute/cancel watchdog stops us."""
        self._write("response.json", "ready", 0, {})
        while True:
            self.poll_once(time.monotonic())
            time.sleep(POLL_SECONDS)

    def poll_once(self, now: float) -> None:
        """Handle one request then sample telemetry only when its bounded interval is due."""
        request_path = self.directory / "request.json"
        if request_path.exists():
            request = read_message(request_path)
            request_path.unlink()
            self._request(request)
        if self.actor_id is not None and now >= self.next_sample:
            self._sample(now)

    def _request(self, request: dict[str, object]) -> None:
        self._validate_identity(request)
        action = request.get("action")
        if action == "execute":
            payload = self._execute_request(request)
        elif action == "telemetry":
            payload = self._configure_telemetry(request)
        else:
            message = "Unsupported session request action."
            raise ProtocolError(message)
        self._write("response.json", "result", self.sequence, payload)

    def _execute_request(self, request: dict[str, object]) -> dict[str, object]:
        policy = self.rpc_timeout_policy
        if policy is None:
            return self.namespace.execute(validate_code(request.get("code")))
        policy.start_request(self.request_timeout_seconds, deadline=self._request_deadline(request))
        try:
            policy.timeout_seconds()
            return self.namespace.execute(validate_code(request.get("code")))
        finally:
            policy.end_request()

    def _request_deadline(self, request: dict[str, object]) -> float | None:
        value = request.get("request_deadline_monotonic")
        if value is None and not self.require_deadlines:
            return None
        try:
            return deadline_value(value)
        except (ValueError, TypeError) as exc:
            raise ProtocolError(str(exc)) from exc

    def _validate_identity(self, request: dict[str, object]) -> None:
        if (
            request.get("version") != PROTOCOL_VERSION
            or request.get("session_id") != self.session_id
        ):
            message = "Session protocol identity mismatch."
            raise ProtocolError(message)
        sequence = request.get("sequence")
        if type(sequence) is not int or sequence != self.sequence + 1:
            message = "Session request sequence is invalid."
            raise ProtocolError(message)
        self.sequence = sequence

    def _configure_telemetry(self, request: dict[str, object]) -> dict[str, object]:
        actor_id = request.get("actor_id")
        if actor_id is not None and (
            type(actor_id) is not int or actor_id not in self.ownership.actor_ids()
        ):
            message = "Telemetry requires an actor owned by this session."
            raise ProtocolError(message)
        self.actor_id = actor_id
        self.interval = _telemetry_interval(request.get("interval_seconds", 0.2))
        self.next_sample = 0.0
        return {"ok": True, "actor_id": actor_id, "interval_seconds": self.interval}

    def _sample(self, now: float) -> None:
        if self.actor_id not in self.ownership.actor_ids():
            self.actor_id = None
            return
        payload = self.api.get_vehicle_telemetry(self.actor_id)
        self.telemetry_sequence += 1
        self.next_sample = now + self.interval
        self._write("telemetry.json", "telemetry", self.telemetry_sequence, payload)

    def _write(self, filename: str, kind: str, sequence: int, payload: dict[str, object]) -> None:
        write_message(
            self.directory / filename,
            {
                "version": PROTOCOL_VERSION,
                "session_id": self.session_id,
                "kind": kind,
                "sequence": sequence,
                "payload": payload,
            },
        )


def _telemetry_interval(interval: object) -> float:
    if (
        isinstance(interval, bool)
        or not isinstance(interval, int | float)
        or not MIN_TELEMETRY_INTERVAL_SECONDS <= interval <= MAX_TELEMETRY_INTERVAL_SECONDS
    ):
        message = "Telemetry interval must be in 0.1..10 seconds."
        raise ProtocolError(message)
    return float(interval)


def main() -> None:
    """Use only trusted launcher paths and the same curated script API as finite execution."""
    args = _parse_args()
    config = read_message(args.script)
    policy = RpcTimeoutPolicy(
        absolute_deadline=deadline_value(config.get("absolute_deadline_monotonic"))
    )
    request_timeout = bounded_seconds(
        config.get("request_timeout_seconds"), "request_timeout_seconds", MAX_REQUEST_SECONDS
    )
    adapter = PythonCarlaAdapter(
        host=args.host,
        port=args.port,
        timeout=min(args.timeout_seconds, 10.0),
        rpc_timeout_policy=policy,
        settings_journal=RunSettings(
            args.ownership_file.with_name(SETTINGS_FILENAME), require_existing=True
        ),
        persistent_connection_path=args.ownership_file.with_name(CONNECTION_FILENAME),
    )
    snapshots = SessionSnapshots()
    ownership = RunOwnership(args.ownership_file)
    api = CarlaScriptApi(adapter=adapter, snapshots=snapshots, ownership=ownership)
    try:
        SessionLoop(
            args.script.parent,
            str(config["session_id"]),
            api,
            ownership,
            snapshots,
            rpc_timeout_policy=policy,
            request_timeout_seconds=request_timeout,
            require_deadlines=True,
        ).run()
    except Exception as exc:  # noqa: BLE001 - protocol/user integration failures terminate the worker.
        outcome = _error("session_protocol_error", str(exc), stdout="")
    finally:
        api.close()
        adapter.close_sensor_subscriptions()
    sys.stdout.write(json.dumps(outcome, allow_nan=False))


if __name__ == "__main__":
    main()
