"""Restrict synchronous managed traffic to a durably proven local TM host."""

from __future__ import annotations

import os
from contextlib import suppress
from copy import deepcopy
from typing import TYPE_CHECKING, Protocol, cast

from carla_agentic_toolkit.managed_liveness import process_record
from carla_agentic_toolkit.managed_tm_proof import (
    EVIDENCE_KEY,
    MAX_EPISODE,
    MAX_PORT,
    failures_from_state,
    listening_inodes,
    port_lock,
    process_socket_inodes,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaTrafficManager

_MAX_FAILURES = 8
_MAX_MESSAGE = 512
__all__ = ["ManagedTrafficManager", "failures_from_state"]


class _OwnedManager(Protocol):
    def get_port(self) -> int: ...

    def set_synchronous_mode(self, enabled: bool) -> None: ...  # noqa: FBT001

    def shut_down(self) -> None: ...


class ManagedTrafficManager:
    """Never adopt a remote/existing TM, replay mutations, or infer crash shutdown."""

    def __init__(  # noqa: PLR0913 -- explicit trusted host provenance and persistence boundary.
        self,
        client: CarlaClient,
        *,
        port: int,
        world_id: int,
        run_id: str,
        state_root: Path,
        persist: Callable[[], None],
    ) -> None:
        """Retain configuration without constructing or configuring a native TM."""
        self._client = client
        self._root = state_root
        self._persist_callback = persist
        self._manager: _OwnedManager | None = None
        self._lock_fd: int | None = None
        self._evidence_failed = False
        self._failure_messages: list[str] = []
        self._identity: dict[str, object] = {}
        self._state: dict[str, object] = {
            "schema_version": 1,
            "run_id": run_id,
            "world_id": world_id,
            "host_pid": os.getpid(),
            "host_start_time": None,
            "host_boot_id": None,
            "port": port,
            "phase": "not_started",
            "listener_inodes": [],
            "sync_attempted": False,
            "async_restored": False,
            "shutdown_acknowledged": False,
            "listener_closed": False,
        }

    @property
    def manager(self) -> CarlaTrafficManager:
        """Expose only the retained, proven handle, never construct one on demand."""
        self._require_available()
        return cast("CarlaTrafficManager", self._native_manager())

    def fields(self) -> dict[str, object]:
        """Detach mutable evidence before the enclosing lease takes its snapshot."""
        return {EVIDENCE_KEY: {**deepcopy(self._state), "failures": self.failures()}}

    def failures(self) -> list[str]:
        """Return sticky uncertainty without reading or changing native state."""
        return self._failure_messages.copy()

    def open(self) -> None:
        """Persist intent, prove a newly hosted local server, then acknowledge async."""
        self._run(self._open)

    def _open(self) -> None:
        self._require_available()
        port = self._port()
        self._lock_fd = port_lock(self._root, port)
        self._identity = process_record(os.getpid())
        self._state.update(
            host_start_time=self._identity["start_ticks"], host_boot_id=self._identity["boot_id"]
        )
        if listening_inodes(port):
            message = "A preexisting listener prevents exclusive managed TM ownership."
            raise RuntimeError(message)
        self._require_world(self._current_world_id)
        self._state["phase"] = "construction_intent"
        self._persist()
        self._require_world(self._current_world_id)
        handle = self._client.get_trafficmanager(port)
        self._manager = _manager_protocol(handle, port)
        listeners = listening_inodes(port)
        if len(listeners) != 1 or listeners != listeners & process_socket_inodes():
            message = "Traffic Manager did not create an exclusively current-PID listener."
            raise RuntimeError(message)
        self._state.update(listener_inodes=sorted(listeners), phase="owned")
        self._persist()
        self._guard(self._current_world_id)
        self._manager.set_synchronous_mode(False)
        self._guard(self._current_world_id)

    def enable_sync(self, current_world_id: Callable[[], int]) -> None:
        """Only the owner can enable sync, after fixture construction has finished."""
        self._run(lambda: self._enable_sync(current_world_id))

    def assert_owned(self, current_world_id: Callable[[], int]) -> None:
        """Recheck authority before each later native mutation without changing the TM."""
        self._run(lambda: self._guard(current_world_id))

    def _enable_sync(self, current_world_id: Callable[[], int]) -> None:
        self._guard(current_world_id)
        self._state["sync_attempted"] = True
        self._persist()
        self._guard(current_world_id)
        self._native_manager().set_synchronous_mode(True)
        self._guard(current_world_id)

    def rebind_world(self, acknowledged_world_id: int, current_world_id: Callable[[], int]) -> None:
        """Rebind only an explicit successful toolkit replacement, retaining host proof."""
        self._run(lambda: self._rebind_world(acknowledged_world_id, current_world_id))

    def _rebind_world(
        self, acknowledged_world_id: int, current_world_id: Callable[[], int]
    ) -> None:
        self._require_available()
        _require_episode(acknowledged_world_id)
        self._require_host()
        if _require_episode(current_world_id()) != acknowledged_world_id:
            message = "Acknowledged TM replacement episode is no longer current."
            raise RuntimeError(message)
        self._state["world_id"] = acknowledged_world_id
        self._persist()
        self._guard(current_world_id)

    def close(self, current_world_id: Callable[[], int]) -> dict[str, object]:
        """Clean only the original host; uncertainty forbids any further native writes."""
        with suppress(RuntimeError):
            self._run(lambda: self._close(current_world_id))
        return {
            "ok": self._state["phase"] == "closed" and not self._failure_messages,
            "failures": self.failures(),
            **self.fields(),
        }

    def _close(self, current_world_id: Callable[[], int]) -> None:
        self._require_available()
        if self._state["phase"] == "closed":
            return
        self._guard(current_world_id)
        self._state["phase"] = "closing"
        self._persist()
        self._guard(current_world_id)
        self._native_manager().set_synchronous_mode(False)
        self._guard(current_world_id)
        self._state.update(async_restored=True, phase="shutdown_intent")
        self._persist()
        self._guard(current_world_id)
        self._native_manager().shut_down()
        self._state["shutdown_acknowledged"] = True
        self._persist()
        self._require_process()
        self._require_world(current_world_id)
        if listening_inodes(self._port()):
            message = "The original Traffic Manager listener remains open after shutdown."
            raise RuntimeError(message)
        self._state.update(listener_closed=True, phase="closed")
        self._persist()
        self._release_port_lock()

    def _run(self, action: Callable[[], None]) -> None:
        try:
            action()
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as error:
            self._record_failure(error)
            message = f"Managed Traffic Manager ownership failed: {error}"
            raise RuntimeError(message) from error

    def _record_failure(self, error: Exception) -> None:
        message = str(error)[:_MAX_MESSAGE] or type(error).__name__
        if message not in self._failure_messages and len(self._failure_messages) < _MAX_FAILURES:
            self._failure_messages.append(message)
        if not self._evidence_failed:
            with suppress(RuntimeError):
                self._persist()

    def _persist(self) -> None:
        try:
            self._persist_callback()
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as error:
            self._evidence_failed = True
            message = f"Managed Traffic Manager evidence persistence failed: {error}"[:_MAX_MESSAGE]
            if message not in self._failure_messages:
                self._failure_messages.append(message)
            raise RuntimeError(message) from error

    def _require_available(self) -> None:
        if self._failure_messages or self._evidence_failed:
            message = "Managed Traffic Manager evidence is uncertain; manual recovery is required."
            raise RuntimeError(message)

    def _port(self) -> int:
        value = self._state["port"]
        if type(value) is not int or not 1 <= value <= MAX_PORT:
            message = "Managed Traffic Manager port must be an exact valid integer."
            raise RuntimeError(message)
        return value

    def _native_manager(self) -> _OwnedManager:
        if self._manager is None:
            message = "Managed Traffic Manager has not acquired local ownership."
            raise RuntimeError(message)
        return self._manager

    def _current_world_id(self) -> int:
        return _require_episode(getattr(self._client.get_world(), "id", None))

    def _require_world(self, current_world_id: Callable[[], int]) -> None:
        value = _require_episode(current_world_id())
        if value != self._state["world_id"]:
            message = "Managed Traffic Manager episode changed; manual recovery is required."
            raise RuntimeError(message)

    def _require_process(self) -> None:
        if process_record(os.getpid()) != self._identity:
            message = "Managed Traffic Manager process provenance changed."
            raise RuntimeError(message)

    def _require_host(self) -> None:
        self._require_process()
        listeners = listening_inodes(self._port())
        if not listeners or sorted(listeners) != self._state["listener_inodes"]:
            message = "Managed Traffic Manager listener ownership changed."
            raise RuntimeError(message)
        if listeners != listeners & process_socket_inodes():
            message = "Managed Traffic Manager listener is not owned by the current PID."
            raise RuntimeError(message)
        _manager_protocol(self._manager, self._port())

    def _guard(self, current_world_id: Callable[[], int]) -> None:
        self._require_available()
        self._require_world(current_world_id)
        self._require_host()
        self._require_world(current_world_id)

    def _release_port_lock(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None


def _require_episode(value: object) -> int:
    if type(value) is not int or not 0 <= value <= MAX_EPISODE:
        message = "Managed Traffic Manager requires an exact episode identity."
        raise RuntimeError(message)
    return value


def _manager_protocol(value: object, port: int) -> _OwnedManager:
    if not all(
        callable(getattr(value, name, None))
        for name in ("get_port", "set_synchronous_mode", "shut_down")
    ):
        message = "Managed Traffic Manager lacks the required native host capabilities."
        raise RuntimeError(message)
    manager = cast("_OwnedManager", value)
    actual = manager.get_port()
    if type(actual) is not int or actual != port:
        message = "Managed Traffic Manager returned an uncertain or incorrect port."
        raise RuntimeError(message)
    return manager
