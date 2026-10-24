"""Bound native CARLA calls by operation type and parent-issued execution deadlines."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.managed_world import world_identity
from carla_agentic_toolkit.session_protocol import read_message

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient

NORMAL_RPC_SECONDS = 10.0
MAP_RPC_SECONDS = 120.0
MAP_OBSERVATION_SECONDS = 2.0
RUN_DEADLINE_FILENAME = "run-deadline.json"
MAP_FAILURE_HINT = (
    "Do not retry the map mutation automatically: the server may already have replaced the world. "
    "Inspect observed_world and recover the execution before starting another mutation."
)
_NATIVE_ERRORS = (AttributeError, RuntimeError, TypeError, ValueError)


def deadline_value(value: object) -> float:
    """Accept only finite monotonic deadlines from trusted launcher protocol metadata."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        message = "RPC deadline must be a finite monotonic timestamp."
        raise TypeError(message)
    if not math.isfinite(value) or value <= 0:
        message = "RPC deadline must be a positive finite monotonic timestamp."
        raise ValueError(message)
    return float(value)


def run_deadline(path: Path | None, seconds: float, *, required: bool) -> float:
    """Read the parent deadline; direct legacy callers may explicitly use a local budget."""
    local_deadline = time.monotonic() + seconds
    if not required and (path is None or not path.exists()):
        return local_deadline
    if path is None:
        message = "Parent execution deadline is required."
        raise ValueError(message)
    return min(
        local_deadline, deadline_value(read_message(path).get("absolute_deadline_monotonic"))
    )


@dataclass
class RpcTimeoutPolicy:
    """A request may narrow the absolute lifetime, but can never reset or extend it."""

    normal_timeout_seconds: float = NORMAL_RPC_SECONDS
    map_timeout_seconds: float = MAP_RPC_SECONDS
    absolute_deadline: float | None = None
    request_deadline: float | None = None
    clock: Callable[[], float] = field(default_factory=lambda: time.monotonic, repr=False)

    def __post_init__(self) -> None:
        """Keep invalid transport or native caps from silently disabling deadlines."""
        for seconds in (self.normal_timeout_seconds, self.map_timeout_seconds):
            deadline_value(seconds)
        for deadline in (self.absolute_deadline, self.request_deadline):
            if deadline is not None:
                deadline_value(deadline)

    def timeout_seconds(self, *, map_operation: bool = False) -> float:
        """Recompute the remaining lifetime before each native operation."""
        cap = self.map_timeout_seconds if map_operation else self.normal_timeout_seconds
        now = self.clock()
        for deadline in (self.absolute_deadline, self.request_deadline):
            if deadline is not None:
                cap = min(cap, deadline - now)
        if cap <= 0:
            message = "Execution deadline expired before a native CARLA request."
            raise CarlaAdapterError(message, details={"retryable": False})
        return cap

    def start_request(self, seconds: float, *, deadline: float | None = None) -> None:
        """Use the parent's publish-time deadline rather than a fresh pickup-time budget."""
        local_deadline = self.clock() + seconds
        published = deadline_value(deadline) if deadline is not None else local_deadline
        self.request_deadline = min(local_deadline, published)

    def end_request(self) -> None:
        """Remove only the request window; the absolute execution deadline remains intact."""
        self.request_deadline = None


def configure_timeout(client: object, seconds: float) -> None:
    """Set the real native client, while allowing existing minimal in-memory client doubles."""
    setter = getattr(client, "set_timeout", None)
    if setter is not None:
        try:
            setter(seconds)
        except _NATIVE_ERRORS as exc:
            raise CarlaAdapterError(str(exc)) from exc


def call_map_rpc[Result](
    client: object, policy: RpcTimeoutPolicy, operation: Callable[[], Result]
) -> Result:
    """Elevate exactly one map RPC and observe a lost reply without retrying the mutation."""
    configure_timeout(client, policy.timeout_seconds(map_operation=True))
    failure: BaseException | None = None
    try:
        return _map_result(client, policy, operation)
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _restore_normal_timeout(client, policy, failure)


def _map_result[Result](
    client: object, policy: RpcTimeoutPolicy, operation: Callable[[], Result]
) -> Result:
    try:
        return operation()
    except UnsupportedFeatureError:
        raise
    except _NATIVE_ERRORS as exc:
        raise CarlaAdapterError(
            str(exc),
            details={
                "retryable": False,
                "hint": MAP_FAILURE_HINT,
                "observed_world": _observe_world(client, policy),
            },
        ) from exc


def _observe_world(client: object, policy: RpcTimeoutPolicy) -> dict[str, object]:
    observed: dict[str, object] = {"world_id": None, "map_name": None, "error": None}
    observation_deadline = policy.clock() + MAP_OBSERVATION_SECONDS
    try:
        _observation_timeout(client, policy, observation_deadline)
        world = cast("CarlaClient", client).get_world()
        observed["world_id"] = world_identity(world)
        _observation_timeout(client, policy, observation_deadline)
        observed["map_name"] = str(world.get_map().name)
    except _NATIVE_ERRORS as exc:
        observed["error"] = str(exc)
    return observed


def _observation_timeout(client: object, policy: RpcTimeoutPolicy, deadline: float) -> None:
    seconds = min(policy.timeout_seconds(), deadline - policy.clock())
    if seconds <= 0:
        message = "Map observation deadline expired."
        raise CarlaAdapterError(message)
    configure_timeout(client, seconds)


def _restore_normal_timeout(
    client: object, policy: RpcTimeoutPolicy, failure: BaseException | None
) -> None:
    try:
        seconds = policy.timeout_seconds()
    except CarlaAdapterError:
        # Configuration is local; the next RPC still refuses an exhausted deadline.
        seconds = policy.normal_timeout_seconds
    _restore_timeout(client, seconds, failure)


def _restore_timeout(client: object, seconds: float, failure: BaseException | None) -> None:
    try:
        configure_timeout(client, seconds)
    except CarlaAdapterError as exc:
        if failure is None:
            raise
        if isinstance(failure, CarlaAdapterError):
            failure.details["rpc_timeout_restore_error"] = str(exc)
