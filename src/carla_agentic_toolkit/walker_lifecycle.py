"""Creating-client navigation barriers and rollback for AI walker pairs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.authoritative_destroy import destroy_result_cleaned
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.managed_world import world_identity
from carla_agentic_toolkit.world_timing import world_synchronous_mode

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaWorld
    from carla_agentic_toolkit.models import DestroyResult

WALKER_FRAME_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class WalkerLifecycle:
    """Bind every navigation action to the original client and episode."""

    world: CarlaWorld
    require_episode: Callable[[int], None] | None = None
    timeout_seconds: Callable[[], float] | None = None
    delete_actor: Callable[[int, int], DestroyResult] | None = None
    identity: int = field(init=False)

    def __post_init__(self) -> None:
        """Capture identity before the first navigation mutation."""
        object.__setattr__(self, "identity", world_identity(self.world))

    def check(self) -> None:
        """Refresh remaining budget without writing another creation intent."""
        self.timeout()
        if world_identity(self.world) != self.identity:
            message = "CARLA world episode changed during walker lifecycle."
            raise CarlaAdapterError(message)
        if self.require_episode is not None:
            self.require_episode(self.identity)

    def timeout(self) -> float:
        """Refresh and bound the next frame RPC without resetting its deadline."""
        cap = (
            self.timeout_seconds()
            if self.timeout_seconds is not None
            else WALKER_FRAME_TIMEOUT_SECONDS
        )
        return min(WALKER_FRAME_TIMEOUT_SECONDS, cap)

    def seed(self, seed: int | None) -> None:
        """Seed native navigation before any location selection, including seed zero."""
        if seed is not None:
            self.check()
            try:
                cast("Any", self.world).set_pedestrians_seed(seed)
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                message = f"Native pedestrian seeding failed: {exc}"
                raise CarlaAdapterError(message) from exc
            self.check()

    def settle(self, walker_id: int) -> int:
        """Publish a fresh frame containing the walker before registering navigation."""
        self.check()
        before = self.world.get_snapshot().frame
        synchronous = world_synchronous_mode(self.world)
        self.check()
        seconds = self.timeout()
        frame = (
            cast("Any", self.world).tick(seconds)
            if synchronous
            else self.world.wait_for_tick(seconds).frame
        )
        self.check()
        snapshot = cast("Any", self.world.get_snapshot())
        if frame <= before or snapshot.frame < frame or snapshot.find(walker_id) is None:
            message = "Walker startup snapshot did not publish the created walker."
            raise CarlaAdapterError(message)
        self.check()
        return int(frame)

    def stop(self, controller: CarlaActor) -> None:
        """Acknowledge navigation removal only on a normal native Stop return."""
        self.check()
        cast("Any", controller).stop()
        self.check()

    def destroy(self, candidate: CarlaActor) -> bool:
        """Only already-known returned actors are eligible for rollback."""
        self.check()
        if self.delete_actor is not None:
            result = self.delete_actor(int(candidate.id), self.identity)
            self.check()
            return destroy_result_cleaned(result)
        destroyed = bool(candidate.destroy())
        self.check()
        return destroyed
