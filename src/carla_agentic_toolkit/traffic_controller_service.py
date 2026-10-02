"""Thread lifetime, desired revisions, and truthful status for traffic maintenance."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from importlib import import_module
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.behavior_profiles import behavior_profile
from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.models import (
    DestroyResult,
    TrafficControllerStartRequest,
    TrafficControllerStatus,
    TrafficDensityRequest,
    VehicleBehaviorRequest,
    VehicleBehaviorResult,
)
from carla_agentic_toolkit.traffic_behavior import apply_behavior_to_world
from carla_agentic_toolkit.traffic_controller_step import (
    TrafficControllerStep,
    maintain_traffic_once,
    require_async_density_mode,
)
from carla_agentic_toolkit.traffic_density import ControllerActors
from carla_agentic_toolkit.traffic_runtime import traffic_manager

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaClient
STOP_JOIN_TIMEOUT_SECONDS = 5.0
__all__ = [
    "ControllerActors",
    "InProcessTrafficControllerService",
    "TrafficControllerStep",
    "maintain_traffic_once",
    "require_async_density_mode",
]


@dataclass(frozen=True, slots=True)
class _RuntimeState:
    """One generation's desired configuration and last applied observations.

    Only a matching revision may consume its desired one-shot reset. Applied
    observations never replace a newer request, and stopping generations may
    not publish completed work. Registration never grants deletion ownership.
    """

    request: TrafficControllerStartRequest
    active: bool
    vehicle_count: int = 0
    moving_vehicle_count: int = 0
    last_error: str | None = None
    stopping: bool = False
    generation: int = 0
    revision: int = 0
    applied_revision: int | None = None
    applied_density: TrafficDensityRequest | None = None
    registered_actor_ids: frozenset[int] = frozenset()
    owned_actor_ids: frozenset[int] = frozenset()
    error_type: str | None = None
    conflict: dict[str, object] | None = None


class InProcessTrafficControllerService:
    """Persistent Traffic Manager controller held by the MCP server process."""

    def __init__(
        self,
        on_spawn: Callable[[tuple[int, ...]], None] | None = None,
    ) -> None:
        """Create an inactive controller service."""
        self._on_spawn = on_spawn
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state = _RuntimeState(request=_default_start_request(), active=False)
        self._behaviors: dict[int, str] = {}

    def start(self, request: TrafficControllerStartRequest) -> TrafficControllerStatus:
        """Start the persistent controller."""
        self.stop()
        with self._lock:
            self._require_stopped()
            stop_event = threading.Event()
            self._stop_event = stop_event
            self._state = _RuntimeState(
                request=request, active=True, generation=self._state.generation + 1, revision=1
            )
            self._behaviors.clear()
            self._thread = threading.Thread(
                target=self._run,
                args=(stop_event, self._state.generation),
                name="carla-agentic-toolkit-traffic-controller",
                daemon=True,
            )
            self._thread.start()
            return self._status_locked()

    def stop(self) -> TrafficControllerStatus:
        """Request cancellation, retaining a timed-out worker until it exits."""
        with self._lock:
            thread = self._thread
            self._stop_event.set()
            self._state = replace(self._state, stopping=self._worker_alive())
        if thread is not None:
            thread.join(timeout=STOP_JOIN_TIMEOUT_SECONDS)
        with self._lock:
            self._finish_stop(thread)
            return self._status_locked()

    def _finish_stop(self, thread: threading.Thread | None) -> None:
        """Commit a join result only while it still refers to the same worker."""
        if self._thread is not thread:
            return
        if self._worker_alive():
            self._state = replace(
                self._state,
                active=True,
                stopping=True,
                last_error="Traffic controller is still stopping after its join timeout.",
                error_type="controller_stopping",
            )
            return
        self._thread = None
        self._state = replace(self._state, active=False, stopping=False)

    def _worker_alive(self) -> bool:
        """Use actual thread lifetime rather than a cancellation request."""
        return self._thread is not None and self._thread.is_alive()

    def _require_stopped(self) -> None:
        if self._worker_alive():
            msg = "Traffic controller is still stopping; retry after its worker terminates."
            raise CarlaAdapterError(msg)

    def get_status(self) -> TrafficControllerStatus:
        """Return current controller status."""
        with self._lock:
            return self._status_locked()

    def set_density(self, request: TrafficDensityRequest) -> TrafficControllerStatus:
        """Converge traffic to a target density."""
        with self._lock:
            start_request = replace(self._state.request, density=request)
            if self._state.stopping:
                self._require_stopped()
            if self._worker_alive():
                self._state = replace(
                    self._state, request=start_request, revision=self._state.revision + 1
                )
                return self._status_locked()
        return self.start(start_request)

    def set_vehicle_behavior(self, request: VehicleBehaviorRequest) -> VehicleBehaviorResult:
        """Apply a named behavior profile to explicit actor IDs."""
        profile = behavior_profile(request.profile)
        if profile is None:
            msg = f"Unknown behavior profile: {request.profile}."
            raise CarlaAdapterError(msg)
        with self._lock:
            self._behaviors.update(dict.fromkeys(request.actor_ids, request.profile))
        failures = _apply_behavior_once(self._state.request, request)
        return VehicleBehaviorResult(
            actor_ids=tuple(actor_id for actor_id in request.actor_ids if actor_id not in failures),
            profile=request.profile,
            applied_settings=profile.to_settings(),
            failed_applications=tuple(
                DestroyResult(actor_id=actor_id, destroyed=False, error=error)
                for actor_id, error in failures.items()
            ),
        )

    def _run(self, stop_event: threading.Event, generation: int) -> None:
        """Run the controller loop until stopped."""
        client: CarlaClient | None = None
        while not stop_event.is_set():
            try:
                client = client or _client(self._state.request)
                self._control_once(client, generation)
            except UnsupportedFeatureError as exc:
                self._record_error(str(exc), generation, error_type="unsupported_feature")
                stop_event.set()
            except (CarlaAdapterError, RuntimeError, TypeError, ValueError) as exc:
                self._record_error(str(exc), generation)
                client = None
                stop_event.wait(1.0)

    def _control_once(self, client: CarlaClient, generation: int) -> None:
        """Run one controller maintenance step."""
        with self._lock:
            state = self._state
            if state.generation != generation or state.stopping:
                return
            behaviors = dict(self._behaviors)
        result = maintain_traffic_once(
            client,
            state.request,
            behaviors,
            ControllerActors(state.registered_actor_ids, state.owned_actor_ids),
            configure_manager=state.request.density != state.applied_density,
        )
        self._record_step(result, generation=generation, revision=state.revision)

    def _record_step(
        self, result: TrafficControllerStep, *, generation: int, revision: int
    ) -> None:
        """Record the completed controller step."""
        if result.spawned_actor_ids and self._on_spawn is not None:
            self._on_spawn(result.spawned_actor_ids)
        with self._lock:
            if self._state.generation != generation or self._state.stopping:
                return
            self._commit_step(result, revision)

    def _commit_step(self, result: TrafficControllerStep, revision: int) -> None:
        """Keep desired and applied state distinct under the controller lock."""
        request = result.request if revision == self._state.revision else self._state.request
        conflict = result.conflict
        self._state = replace(
            self._state,
            request=request,
            vehicle_count=result.vehicle_count,
            moving_vehicle_count=result.moving_vehicle_count,
            registered_actor_ids=result.registered_actor_ids,
            owned_actor_ids=result.owned_actor_ids,
            applied_density=result.request.density,
            applied_revision=revision,
            conflict=conflict,
            error_type="density_conflict" if conflict else None,
            last_error="Protected vehicles exceed the density target." if conflict else None,
        )

    def _record_error(
        self, message: str, generation: int, *, error_type: str = "controller_error"
    ) -> None:
        """Record errors only for the current generation that still accepts work."""
        with self._lock:
            if self._state.generation != generation or self._state.stopping:
                return
            self._state = replace(self._state, last_error=message, error_type=error_type)

    def _status_locked(self) -> TrafficControllerStatus:
        """Build a status from locked state."""
        request = self._state.request
        applied = self._state.applied_density
        active = self._worker_alive()
        return TrafficControllerStatus(
            active=active,
            host=request.host,
            port=request.port,
            traffic_manager_port=request.density.traffic_manager_port,
            target_vehicle_count=request.density.vehicle_count,
            vehicle_count=self._state.vehicle_count,
            moving_vehicle_count=self._state.moving_vehicle_count,
            last_error=self._state.last_error,
            stopping=active and self._state.stopping,
            generation=self._state.generation,
            desired_revision=self._state.revision,
            applied_revision=self._state.applied_revision,
            applied_target_vehicle_count=applied.vehicle_count if applied else None,
            owned_actor_ids=tuple(sorted(self._state.owned_actor_ids)),
            adopted_actor_ids=tuple(
                sorted(self._state.registered_actor_ids - self._state.owned_actor_ids)
            ),
            error_type=self._state.error_type,
            conflict=self._state.conflict,
        )


def _default_start_request() -> TrafficControllerStartRequest:
    """Return default controller start settings."""
    return TrafficControllerStartRequest(density=TrafficDensityRequest(vehicle_count=0))


def _client(request: TrafficControllerStartRequest) -> CarlaClient:
    """Create a CARLA client for the controller."""
    module = import_module("carla")
    factory = getattr(module, "Client", None)
    if not callable(factory):
        msg = "Imported carla module does not expose Client."
        raise CarlaAdapterError(msg)
    client = factory(request.host, request.port)
    client.set_timeout(request.timeout_seconds)
    return cast("CarlaClient", client)


def _apply_behavior_once(
    start_request: TrafficControllerStartRequest,
    request: VehicleBehaviorRequest,
) -> dict[int, str]:
    """Apply one behavior request immediately when CARLA is reachable."""
    client = _client(start_request)
    world = client.get_world()
    manager = traffic_manager(client, request.traffic_manager_port)
    return apply_behavior_to_world(world, manager, request)
