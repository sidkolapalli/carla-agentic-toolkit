"""Validate and translate dynamic CARLA objects at the Python adapter boundary."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.actor_runtime import actor_by_id
from carla_agentic_toolkit.carla_protocols import (
    CarlaBlueprint,
    CarlaBlueprintAttribute,
    CarlaClient,
    CarlaClientFactory,
    CarlaImage,
    CarlaSensor,
    CarlaWorld,
)
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.experiment_common import carla_transform, require_sensor
from carla_agentic_toolkit.experiment_perception import collect_sensor_frames
from carla_agentic_toolkit.models import (
    BlueprintAttribute,
    BlueprintInfo,
    SpawnRequest,
    SpawnResult,
    Transform,
)

if TYPE_CHECKING:
    from pathlib import Path


def _carla_client_factory() -> CarlaClientFactory:
    """Import the optional CARLA Python module and return its Client factory."""
    module = import_module("carla")
    client_factory = getattr(module, "Client", None)
    if not callable(client_factory):
        msg = "Imported carla module does not expose Client."
        raise TypeError(msg)
    return cast("CarlaClientFactory", client_factory)


def _require_carla_client(candidate: object) -> CarlaClient:
    """Validate that a dynamic CARLA client exposes the expected API."""
    if isinstance(candidate, CarlaClient):
        return candidate
    msg = "CARLA Client object does not expose the expected API."
    raise CarlaAdapterError(msg)


def _safe_call(target: object, method_name: str) -> str | None:
    """Return a method result as text, or None when unavailable."""
    method = getattr(target, method_name, None)
    if not callable(method):
        return None
    try:
        value = method()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None
    return str(value)


def _blueprint_info(blueprint: CarlaBlueprint) -> BlueprintInfo:
    """Convert a CARLA blueprint into a stable summary."""
    return BlueprintInfo(
        blueprint_id=str(blueprint.id),
        tags=tuple(str(tag) for tag in blueprint.tags),
        attributes=tuple(_blueprint_attribute(attribute) for attribute in blueprint),
    )


def _blueprint_attribute(attribute: object) -> BlueprintAttribute:
    """Convert a CARLA blueprint attribute into a stable summary."""
    typed_attribute = _require_blueprint_attribute(attribute)
    return BlueprintAttribute(
        attribute_id=str(typed_attribute.id),
        is_modifiable=bool(typed_attribute.is_modifiable),
        recommended_values=tuple(str(value) for value in typed_attribute.recommended_values),
    )


def _require_blueprint_attribute(candidate: object) -> CarlaBlueprintAttribute:
    """Validate a dynamic CARLA blueprint attribute."""
    missing_api = not all(
        hasattr(candidate, attribute_name)
        for attribute_name in ("id", "is_modifiable", "recommended_values")
    )
    if missing_api:
        msg = "CARLA blueprint attribute does not expose the expected API."
        raise CarlaAdapterError(msg)
    return cast("CarlaBlueprintAttribute", candidate)


def _spawn_actor(world: CarlaWorld, index: int, request: SpawnRequest) -> SpawnResult:
    """Spawn one actor and convert CARLA failures into a structured result."""
    try:
        blueprint = _configured_blueprint(world, request)
        actor = world.spawn_actor(blueprint, carla_transform(request.transform))
    except (AttributeError, RuntimeError, TypeError, ValueError, CarlaAdapterError) as exc:
        return SpawnResult(request_index=index, actor_id=None, error=str(exc))
    return SpawnResult(request_index=index, actor_id=int(actor.id), error=None)


def _parent_actor(world: CarlaWorld, actor_id: int | None) -> object | None:
    """Return an attach parent actor when requested."""
    if actor_id is None:
        return None
    actor = actor_by_id(world, actor_id)
    if actor is None:
        msg = f"Parent actor {actor_id} was not found."
        raise CarlaAdapterError(msg)
    return actor


def _spawn_sensor(
    world: CarlaWorld,
    blueprint: CarlaBlueprint,
    transform: Transform,
    parent: object | None,
) -> CarlaSensor:
    """Spawn a sensor actor."""
    try:
        actor = world.spawn_actor(blueprint, carla_transform(transform), parent)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc
    return require_sensor(actor)


def _capture_image(sensor: CarlaSensor) -> CarlaImage:
    """Capture one asynchronous image using the shared bounded listener."""
    return _require_image(collect_sensor_frames(sensor, 1)[0])


def _require_image(candidate: object) -> CarlaImage:
    """Validate a dynamic CARLA image/frame object."""
    missing_api = not all(hasattr(candidate, name) for name in ("frame", "save_to_disk"))
    if missing_api:
        msg = "CARLA sensor frame does not expose the expected image API."
        raise CarlaAdapterError(msg)
    return cast("CarlaImage", candidate)


def _mime_type(path: Path) -> str:
    """Infer a capture MIME type from the output path."""
    if path.suffix.lower() == ".jpg" or path.suffix.lower() == ".jpeg":
        return "image/jpeg"
    return "image/png"


def _configured_blueprint(world: CarlaWorld, request: SpawnRequest) -> CarlaBlueprint:
    """Find and configure a CARLA blueprint for a spawn request."""
    blueprint = world.get_blueprint_library().find(request.blueprint_id)
    for attribute_id, value in request.attributes.items():
        blueprint.set_attribute(attribute_id, value)
    return blueprint
