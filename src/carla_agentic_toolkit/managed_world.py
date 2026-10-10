"""World identity, fixed-step settings, and setup barriers for a dedicated CARLA world."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from carla_agentic_toolkit.errors import UnsupportedFeatureError
from carla_agentic_toolkit.persistent_connection import require_operation_episode

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld
    from carla_agentic_toolkit.managed_spec import ExperimentSpec

SETTINGS_FIELDS = (
    "synchronous_mode",
    "fixed_delta_seconds",
    "no_rendering_mode",
    "substepping",
    "max_substeps",
    "max_substep_delta_time",
)
ACTOR_PATTERNS = ("vehicle.*", "walker.*", "sensor.*", "controller.*")
SETUP_FRAME_TIMEOUT_SECONDS = 2.0
SETUP_POLL_SECONDS = 0.01


class SessionInvariantError(RuntimeError):
    """A world, frame, actor, or controller no longer belongs to this run."""


def world_identity(world: CarlaWorld) -> int:
    """Require episode identity, never substitute a map name or reusable actor ID."""
    identity = getattr(world, "id", None)
    if isinstance(identity, bool) or not isinstance(identity, int):
        message = "Managed sessions require a world.id episode identity capability."
        raise UnsupportedFeatureError(message)
    return identity


def world_settings(world: CarlaWorld) -> dict[str, object]:
    """Capture every setting the managed owner can change before its first mutation."""
    settings = world.get_settings()
    if not all(hasattr(settings, key) for key in SETTINGS_FIELDS):
        message = "Managed sessions require fixed-step and physics substep capabilities."
        raise UnsupportedFeatureError(message)
    return {key: getattr(settings, key) for key in SETTINGS_FIELDS}


def apply_world_settings(world: CarlaWorld, values: dict[str, object]) -> None:
    """Apply the complete reviewed settings contract without scheduling an owner tick."""
    settings = world.get_settings()
    for key in SETTINGS_FIELDS:
        setattr(settings, key, values[key])
    require_operation_episode(world)
    world.apply_settings(settings)


@dataclass
class _SetupFrames:
    initial: int
    latest: int
    changed_at: float

    def observe(self, frame: int, now: float) -> None:
        if frame < self.latest:
            message = "Setup snapshot frame moved backwards."
            raise SessionInvariantError(message)
        if frame != self.latest:
            self.latest, self.changed_at = frame, now


def settle_setup_frames(
    world: CarlaWorld, client: CarlaClient, spec: ExperimentSpec, world_id: int
) -> dict[str, object]:
    """Account for ApplySettings delivery before claiming exact runtime tick ownership."""
    initial = world.get_snapshot().frame
    sample = _SetupFrames(initial, initial, time.monotonic())
    quiet_seconds = max(0.05, 2 * spec.fixed_delta_seconds)
    deadline = sample.changed_at + SETUP_FRAME_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        now = time.monotonic()
        sample.observe(world.get_snapshot().frame, now)
        if now - sample.changed_at >= quiet_seconds:
            return _accepted_setup_frames(client, world_id, sample, quiet_seconds)
        time.sleep(SETUP_POLL_SECONDS)
    message = "World failed to establish a quiet setup frame boundary within two seconds."
    raise SessionInvariantError(message)


def _accepted_setup_frames(
    client: CarlaClient, world_id: int, sample: _SetupFrames, quiet_seconds: float
) -> dict[str, object]:
    if world_identity(client.get_world()) != world_id:
        message = "The world was replaced while waiting for the setup frame boundary."
        raise SessionInvariantError(message)
    return {
        "initial_frame": sample.initial,
        "settled_frame": sample.latest,
        "advanced_frames": sample.latest - sample.initial,
        "quiet_seconds": quiet_seconds,
    }


def require_dedicated_world(
    world: CarlaWorld, client: CarlaClient, spec: ExperimentSpec, world_id: int
) -> None:
    """Verify a fresh empty snapshot without ticking an unverified world."""
    _require_fresh_snapshot(world, spec.rpc_timeout_seconds)
    if world_identity(client.get_world()) != world_id:
        message = "The world was replaced during dedicated world preflight."
        raise SessionInvariantError(message)
    actors = world.get_actors()
    if any(actors.filter(pattern) for pattern in ACTOR_PATTERNS):
        message = "Managed experiments require a dedicated world without existing actors."
        raise SessionInvariantError(message)


def _require_fresh_snapshot(world: CarlaWorld, timeout_seconds: float) -> None:
    """Verify delivery and publication of a newer frame without scheduling one."""
    before = world.get_snapshot().frame
    delivered = world.wait_for_tick(timeout_seconds)
    if delivered.frame <= before or world.get_snapshot().frame < delivered.frame:
        message = "Dedicated world preflight did not receive a fresh actor snapshot."
        raise SessionInvariantError(message)
