"""Runtime helpers for CARLA actor inspection and cleanup."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from carla_mcp.errors import CarlaAdapterError
from carla_mcp.models import ActorSnapshot, DestroyResult, Location, Rotation, Transform

if TYPE_CHECKING:
    from carla_mcp.carla_protocols import CarlaActor, CarlaVector, CarlaWorld


def actor_snapshot(candidate: object) -> ActorSnapshot:
    """Convert a CARLA actor into a stable snapshot."""
    actor = require_actor(candidate)
    return ActorSnapshot(
        actor_id=int(actor.id),
        type_id=str(actor.type_id),
        role_name=actor.attributes.get("role_name"),
        transform=actor_transform(actor),
        speed_mps=actor_speed_mps(actor),
        traffic_light_state=traffic_light_state(actor),
    )


def destroy_actor(world: CarlaWorld, actor_id: int) -> DestroyResult:
    """Destroy one actor by ID."""
    actor = world.get_actors().find(actor_id)
    if actor is None:
        return DestroyResult(actor_id=actor_id, destroyed=False, error="Actor was not found.")
    try:
        destroyed = bool(require_actor(actor).destroy())
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return DestroyResult(actor_id=actor_id, destroyed=False, error=str(exc))
    return DestroyResult(actor_id=actor_id, destroyed=destroyed, error=None)


def require_actor(candidate: object) -> CarlaActor:
    """Validate a dynamic CARLA actor."""
    missing_api = not all(
        hasattr(candidate, name)
        for name in ("id", "type_id", "attributes", "get_transform", "get_velocity", "destroy")
    )
    if missing_api:
        msg = "CARLA actor does not expose the expected API."
        raise CarlaAdapterError(msg)
    return cast("CarlaActor", candidate)


def actor_transform(actor: CarlaActor) -> Transform:
    """Return an actor transform model."""
    transform = actor.get_transform()
    location = transform.location
    rotation = transform.rotation
    return Transform(
        location=Location(x=float(location.x), y=float(location.y), z=float(location.z)),
        rotation=Rotation(
            pitch=float(rotation.pitch),
            yaw=float(rotation.yaw),
            roll=float(rotation.roll),
        ),
    )


def actor_speed_mps(actor: CarlaActor) -> float | None:
    """Return actor speed in metres per second when available."""
    try:
        return float(squared_vector_magnitude(actor.get_velocity()) ** 0.5)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def traffic_light_state(actor: CarlaActor) -> str | None:
    """Return traffic light state text when the actor exposes it."""
    method = getattr(actor, "get_traffic_light_state", None)
    if not callable(method):
        return None
    try:
        return str(method())
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def squared_vector_magnitude(vector: CarlaVector) -> float:
    """Return squared vector magnitude for a CARLA vector-like object."""
    x = float(vector.x)
    y = float(vector.y)
    z = float(vector.z)
    return x * x + y * y + z * z
