"""Native connection lifetime and persistent-only originating-episode checks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit.adapter_objects import _carla_client_factory, _require_carla_client
from carla_agentic_toolkit.carla_versions import read_version_info
from carla_agentic_toolkit.errors import CarlaAdapterError, CarlaApiUnavailableError
from carla_agentic_toolkit.managed_world import world_identity
from carla_agentic_toolkit.persistent_connection import PersistentConnection
from carla_agentic_toolkit.rpc_timeouts import configure_timeout

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld
    from carla_agentic_toolkit.carla_versions import VersionInfo
    from carla_agentic_toolkit.rpc_timeouts import RpcTimeoutPolicy


class PythonCarlaConnectionMixin:
    """Keep native stream reuse distinct from persistent reconnect authority."""

    _connected_client: CarlaClient | None
    _persistent_connection: PersistentConnection | None
    _persistent_connection_path: Path | None
    _rpc_timeout_policy: RpcTimeoutPolicy
    _host: str
    _port: int

    def _client(self, *, inspect_episode: bool = True) -> CarlaClient:
        """Retain one client stream so paused synchronous worlds keep observable state."""
        self._require_persistent_available()
        if self._connected_client is None:
            self._connected_client = self._connect()
        else:
            self.configure_rpc_timeout(self._connected_client)
        client = self._connected_client
        self._inspect_persistent_client(client, inspect_episode=inspect_episode)
        return client

    def _require_persistent_available(self) -> None:
        if self._persistent_connection is not None:
            self._persistent_connection.require_available()

    def _inspect_persistent_client(self, client: CarlaClient, *, inspect_episode: bool) -> None:
        state = self._persistent_connection
        if inspect_episode and state is not None and state.world_id is not None:
            self._world(client)

    def configure_rpc_timeout(self, client: object) -> None:
        """Refresh a native client against this execution's remaining RPC budget."""
        configure_timeout(client, self._rpc_timeout_policy.timeout_seconds())

    def _refresh_rpc_timeout(self) -> None:
        """Check the deadline immediately before another creation on a retained client."""
        self._client()

    def _connect(self) -> CarlaClient:
        """Create and configure a CARLA client."""
        try:
            client_factory = _carla_client_factory()
        except (ImportError, TypeError) as exc:
            raise CarlaApiUnavailableError(str(exc)) from exc
        try:
            client_candidate = client_factory(self._host, self._port)
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        client = _require_carla_client(client_candidate)
        self.configure_rpc_timeout(client)
        return client

    def _world(self, client: CarlaClient) -> CarlaWorld:
        """Fetch a world only after persistent version and originating-episode checks."""
        if self._persistent_connection is not None:
            self._persistent_connection.before_world(
                client, on_error=self._persistent_diagnostic_failure
            )
        try:
            world = client.get_world()
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            raise CarlaAdapterError(str(exc)) from exc
        if self._persistent_connection is not None:
            self._persistent_connection.observe(world_identity(world))
        return world

    def _version_info(self, client: CarlaClient) -> VersionInfo:
        callback = self._persistent_diagnostic_failure if self._persistent_connection else None
        return read_version_info(client, on_error=callback)

    def _enable_persistent_connection(self) -> None:
        if self._persistent_connection is None:
            self._persistent_connection = PersistentConnection(self._persistent_connection_path)

    def _begin_persistent_request(self) -> None:
        if self._persistent_connection is not None:
            self._persistent_connection.begin_request()

    def _record_persistent_failure(self, error: Exception) -> Exception:
        state = self._persistent_connection
        if state is not None and state.record_failure(error):
            self._connected_client = None
        return state.operation_error(error) if state is not None else error

    def _persistent_operation_guard(self) -> Callable[[CarlaWorld], None] | None:
        return self._check_persistent_world if self._persistent_connection is not None else None

    def _check_persistent_world(self, world: CarlaWorld) -> None:
        if self._persistent_connection is not None:
            self._persistent_connection.observe(world_identity(world))
            self._client()

    def _acknowledge_world_replacement(self, world: CarlaWorld) -> None:
        if self._persistent_connection is not None:
            self._persistent_connection.acknowledge_replacement(world_identity(world))

    def _finish_persistent_outcome(self, outcome: dict[str, object]) -> dict[str, object]:
        state = self._persistent_connection
        return state.finish_outcome(outcome) if state is not None else outcome

    def _persistent_diagnostic_failure(self, error: Exception) -> bool:
        state = self._persistent_connection
        if state is None or not state.record_failure(error):
            return False
        self._connected_client = None
        return True
