"""Runtime helpers for CARLA Traffic Manager operations."""

from __future__ import annotations

import contextlib
import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.actor_creation import (
    creation_identity,
    observe_created_actor,
    observe_no_actor,
    prepare_spawn,
)
from carla_agentic_toolkit.actor_runtime import actor_by_id
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import (
    AutopilotRequest,
    SpawnResult,
    TrafficManagerRequest,
    TrafficManagerSettings,
    TrafficPopulationRequest,
)
from carla_agentic_toolkit.tm_access import get_traffic_manager
from carla_agentic_toolkit.traffic_manager_policy import require_async_traffic_manager_request

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Unpack

    from carla_agentic_toolkit.actor_creation import (
        ActorCreationObserver,
        BeforeSpawn,
        NoActorObserver,
        SpawnObservers,
    )
    from carla_agentic_toolkit.carla_protocols import (
        CarlaActor,
        CarlaBlueprint,
        CarlaClient,
        CarlaTrafficManager,
        CarlaWorld,
    )
    from carla_agentic_toolkit.traffic_manager_policy import BeforeTrafficManagerSetting


@dataclass(frozen=True, slots=True)
class _SpawnTrafficContext:
    """Shared context for spawning Traffic Manager vehicles."""

    world: CarlaWorld
    traffic_manager_instance: CarlaTrafficManager
    seed: int
    on_spawn: ActorCreationObserver | None = None
    before_spawn: BeforeSpawn | None = None
    on_no_actor: NoActorObserver | None = None


def traffic_manager(client: CarlaClient, traffic_manager_port: int) -> CarlaTrafficManager:
    """Return a validated CARLA Traffic Manager."""
    candidate = get_traffic_manager(client, traffic_manager_port)
    if _missing_traffic_manager_api(candidate):
        msg = "CARLA Traffic Manager does not expose the expected API."
        raise CarlaAdapterError(msg)
    return candidate


def configure_traffic_manager(
    traffic_manager_instance: CarlaTrafficManager,
    request: TrafficManagerRequest,
    *,
    before_setting: BeforeTrafficManagerSetting | None = None,
) -> TrafficManagerSettings:
    """Configure dedicated asynchronous Traffic Manager sidecar globals."""
    require_async_traffic_manager_request(synchronous_mode=request.synchronous_mode)
    _apply_traffic_manager_settings(traffic_manager_instance, request, before_setting)
    return TrafficManagerSettings(
        traffic_manager_port=traffic_manager_instance.get_port(),
        global_distance_to_leading_vehicle=request.global_distance_to_leading_vehicle,
        global_percentage_speed_difference=request.global_percentage_speed_difference,
        seed=request.seed,
        safe_filter=None,
        synchronous_mode=request.synchronous_mode,
    )


def populate_traffic_actors(
    *,
    world: CarlaWorld,
    traffic_manager_instance: CarlaTrafficManager,
    request: TrafficPopulationRequest,
    **observers: Unpack[SpawnObservers],
) -> tuple[list[int], list[SpawnResult]]:
    """Spawn vehicles at map spawn points and register them with Traffic Manager."""
    blueprints = _stable_ordered(
        _traffic_vehicle_blueprints(world, safe_filter=request.safe_filter),
        seed=request.seed,
    )
    spawn_points = _stable_ordered(list(world.get_map().get_spawn_points()), seed=request.seed)
    request_count = min(request.vehicle_count, len(spawn_points))
    results = [
        _spawn_traffic_actor(
            context=_SpawnTrafficContext(
                world=world,
                traffic_manager_instance=traffic_manager_instance,
                seed=request.seed,
                **observers,
            ),
            blueprint=blueprints[index % len(blueprints)],
            spawn_point=spawn_point,
            index=index,
        )
        for index, spawn_point in enumerate(spawn_points[:request_count])
    ]
    results.extend(_missing_spawn_point_results(request.vehicle_count, request_count))
    return _successful_actor_ids(results), _failed_spawns(results)


def set_actor_autopilot(
    *,
    world: CarlaWorld,
    request: AutopilotRequest,
    traffic_manager_port: int,
) -> tuple[list[int], list[SpawnResult]]:
    """Set autopilot for explicit actor IDs."""
    results = [
        _set_one_actor_autopilot(
            world=world,
            actor_id=actor_id,
            enabled=request.enabled,
            traffic_manager_port=traffic_manager_port,
            index=index,
        )
        for index, actor_id in enumerate(request.actor_ids)
    ]
    return _successful_actor_ids(results), _failed_spawns(results)


def advance_world_once(world: CarlaWorld) -> int:
    """Require one successful frame advance/wait and return its observed identity."""
    try:
        if world.get_settings().synchronous_mode:
            return int(world.tick())
        return int(world.wait_for_tick(2.0).frame)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        message = f"World advancement failed: {exc}"
        raise CarlaAdapterError(message) from exc


def _missing_traffic_manager_api(candidate: object) -> bool:
    """Return whether a Traffic Manager candidate lacks required methods."""
    return not all(
        hasattr(candidate, name)
        for name in (
            "get_port",
            "set_global_distance_to_leading_vehicle",
            "global_percentage_speed_difference",
            "set_random_device_seed",
            "set_synchronous_mode",
        )
    )


def _apply_traffic_manager_settings(
    traffic_manager_instance: CarlaTrafficManager,
    request: TrafficManagerRequest,
    before_setting: BeforeTrafficManagerSetting | None,
) -> None:
    """Apply optional Traffic Manager settings."""
    _apply_setting(
        "global_distance_to_leading_vehicle",
        request.global_distance_to_leading_vehicle,
        traffic_manager_instance.set_global_distance_to_leading_vehicle,
        before_setting,
    )
    _apply_setting(
        "global_percentage_speed_difference",
        request.global_percentage_speed_difference,
        traffic_manager_instance.global_percentage_speed_difference,
        before_setting,
    )
    _apply_setting(
        "seed", request.seed, traffic_manager_instance.set_random_device_seed, before_setting
    )
    _apply_setting(
        "synchronous_mode",
        request.synchronous_mode,
        traffic_manager_instance.set_synchronous_mode,
        before_setting,
    )


def _apply_setting[T](
    name: str,
    value: T | None,
    setter: Callable[[T], None],
    before_setting: BeforeTrafficManagerSetting | None,
) -> None:
    """Keep fatal journal failures outside the recoverable native RPC boundary."""
    if value is None:
        return
    if before_setting is not None:
        before_setting(setting=name, value=value)
    try:
        setter(value)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        raise CarlaAdapterError(str(exc)) from exc


def _spawn_traffic_actor(
    *,
    context: _SpawnTrafficContext,
    blueprint: CarlaBlueprint,
    spawn_point: object,
    index: int,
) -> SpawnResult:
    """Spawn one traffic actor and enable autopilot.

    The actor is tracked before autopilot so a later failure does not leak it.
    """
    configured_blueprint = _configured_traffic_blueprint(
        blueprint=blueprint,
        seed=context.seed,
        index=index,
    )
    identity = creation_identity(context.world, context.on_spawn)
    prepare_spawn(context.before_spawn)
    try:
        actor = context.world.try_spawn_actor(configured_blueprint, spawn_point)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return _spawn_failure(index, str(exc))
    if actor is None:
        observe_no_actor(identity, context.on_no_actor)
        return _spawn_failure(index, "CARLA could not spawn actor at the selected spawn point.")
    observe_created_actor(actor, identity, context.on_spawn)
    try:
        autopilot_enabled = True
        actor.set_autopilot(autopilot_enabled, context.traffic_manager_instance.get_port())
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        _destroy_actor(actor)
        return _spawn_failure(index, str(exc))
    return SpawnResult(request_index=index, actor_id=int(actor.id), error=None)


def _set_one_actor_autopilot(
    *,
    world: CarlaWorld,
    actor_id: int,
    enabled: bool,
    traffic_manager_port: int,
    index: int,
) -> SpawnResult:
    """Set autopilot for one actor and return a structured result."""
    actor = actor_by_id(world, actor_id)
    if actor is None:
        return _spawn_failure(index, f"Actor {actor_id} was not found.")
    if not hasattr(actor, "set_autopilot"):
        return SpawnResult(
            request_index=index,
            actor_id=actor_id,
            error=f"Actor {actor_id} does not expose set_autopilot.",
        )
    try:
        cast("CarlaActor", actor).set_autopilot(enabled, traffic_manager_port)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return SpawnResult(request_index=index, actor_id=actor_id, error=str(exc))
    return SpawnResult(request_index=index, actor_id=actor_id, error=None)


def _traffic_vehicle_blueprints(
    world: CarlaWorld,
    *,
    safe_filter: bool,
) -> list[CarlaBlueprint]:
    """Return vehicle blueprints suitable for autonomous urban traffic."""
    blueprints = list(world.get_blueprint_library().filter("vehicle.*"))
    if safe_filter:
        blueprints = [
            blueprint for blueprint in blueprints if _is_safe_vehicle_blueprint(blueprint)
        ]
    if not blueprints:
        msg = "No vehicle blueprints are available for traffic population."
        raise CarlaAdapterError(msg)
    return sorted(blueprints, key=lambda blueprint: blueprint.id)


def _is_safe_vehicle_blueprint(blueprint: CarlaBlueprint) -> bool:
    """Keep native cars, or use legacy heuristics when classification is absent."""
    if _has_blueprint_attribute(blueprint, "base_type"):
        return _blueprint_string_attribute_text(blueprint, "base_type") == "car"
    unsafe_fragments = (
        "ambulance",
        "carlacola",
        "carlamotors",
        "cybertruck",
        "firetruck",
        "isetta",
        "microlino",
        "sprinter",
    )
    wheels = _blueprint_attribute_text(blueprint, "number_of_wheels")
    has_four_wheels = wheels in (None, "4")
    is_common_vehicle = not any(fragment in blueprint.id for fragment in unsafe_fragments)
    return has_four_wheels and is_common_vehicle


def _configured_traffic_blueprint(
    *,
    blueprint: CarlaBlueprint,
    seed: int,
    index: int,
) -> CarlaBlueprint:
    """Set normal traffic attributes before spawning a vehicle."""
    _set_blueprint_attribute(blueprint, "role_name", "autopilot")
    _set_recommended_attribute_choice(blueprint, "color", seed=seed, index=index)
    _set_recommended_attribute_choice(blueprint, "driver_id", seed=seed, index=index)
    return blueprint


def _set_recommended_attribute_choice(
    blueprint: CarlaBlueprint,
    attribute_id: str,
    *,
    seed: int,
    index: int,
) -> None:
    """Set a deterministic recommended attribute value when available."""
    value = _recommended_attribute_choice(blueprint, attribute_id, seed=seed, index=index)
    if value is not None:
        _set_blueprint_attribute(blueprint, attribute_id, value)


def _blueprint_attribute_text(blueprint: CarlaBlueprint, attribute_id: str) -> str | None:
    """Return a blueprint attribute as text when it exists."""
    if not _has_blueprint_attribute(blueprint, attribute_id):
        return None
    try:
        attribute = blueprint.get_attribute(attribute_id)
        as_int = getattr(attribute, "as_int", None)
        if callable(as_int):
            return str(as_int())
        return str(attribute)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _blueprint_string_attribute_text(blueprint: CarlaBlueprint, attribute_id: str) -> str | None:
    """Read string attributes without probing CARLA's incompatible integer cast."""
    try:
        attribute = blueprint.get_attribute(attribute_id)
        as_str = getattr(attribute, "as_str", None)
        if callable(as_str):
            return str(as_str())
        return str(attribute)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return None


def _recommended_attribute_choice(
    blueprint: CarlaBlueprint,
    attribute_id: str,
    *,
    seed: int,
    index: int,
) -> str | None:
    """Return a deterministic recommended blueprint attribute value."""
    values = _recommended_attribute_values(blueprint, attribute_id)
    if not values:
        return None
    choice_index = _stable_index(f"{blueprint.id}:{attribute_id}:{index}", seed, len(values))
    return values[choice_index]


def _recommended_attribute_values(
    blueprint: CarlaBlueprint,
    attribute_id: str,
) -> tuple[str, ...]:
    """Return recommended values for one blueprint attribute."""
    if not _has_blueprint_attribute(blueprint, attribute_id):
        return ()
    try:
        attribute = blueprint.get_attribute(attribute_id)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return ()
    values = getattr(attribute, "recommended_values", None)
    if not values:
        return ()
    return tuple(str(value) for value in values)


def _set_blueprint_attribute(blueprint: CarlaBlueprint, attribute_id: str, value: str) -> None:
    """Set a blueprint attribute only when CARLA exposes it."""
    if not _has_blueprint_attribute(blueprint, attribute_id):
        return
    try:
        blueprint.set_attribute(attribute_id, value)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return


def _has_blueprint_attribute(blueprint: CarlaBlueprint, attribute_id: str) -> bool:
    """Return whether a CARLA blueprint has an attribute."""
    try:
        return bool(blueprint.has_attribute(attribute_id))
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


def _missing_spawn_point_results(vehicle_count: int, request_count: int) -> list[SpawnResult]:
    """Return failures for requests that exceeded available spawn points."""
    return [
        _spawn_failure(index, "No map spawn point available for requested vehicle.")
        for index in range(request_count, vehicle_count)
    ]


def _spawn_failure(index: int, message: str) -> SpawnResult:
    """Create a failed spawn result."""
    return SpawnResult(request_index=index, actor_id=None, error=message)


def _destroy_actor(actor: object) -> None:
    """Destroy a CARLA actor, silently ignoring errors."""
    destroy = getattr(actor, "destroy", None)
    if callable(destroy):
        with contextlib.suppress(AttributeError, RuntimeError, TypeError, ValueError):
            destroy()


def _successful_actor_ids(results: list[SpawnResult]) -> list[int]:
    """Return successful actor IDs from structured spawn results."""
    return [int(result.actor_id) for result in results if result.actor_id is not None]


def _failed_spawns(results: list[SpawnResult]) -> list[SpawnResult]:
    """Return failed spawn results."""
    return [result for result in results if result.error is not None]


def _stable_ordered[T](items: list[T], *, seed: int) -> list[T]:
    """Return a deterministic hash-ordered copy of items."""
    return sorted(
        items,
        key=lambda item: _stable_digest(f"{seed}:{_stable_item_text(item)}"),
    )


def _stable_index(text: str, seed: int, count: int) -> int:
    """Map text and seed to a stable bounded index."""
    digest = _stable_digest(f"{seed}:{text}")
    return int(digest[:12], 16) % count


def _stable_digest(text: str) -> str:
    """Return a stable digest for deterministic simulation choices."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_item_text(item: object) -> str:
    """Return stable-ish identifying text for CARLA dynamic objects."""
    item_id = getattr(item, "id", None)
    if item_id is not None:
        return str(item_id)
    return _stable_transform_text(item)


def _stable_transform_text(item: object) -> str:
    """Return stable text for a CARLA transform-like object."""
    location = getattr(item, "location", None)
    rotation = getattr(item, "rotation", None)
    if location is None or rotation is None:
        return repr(item)
    return ":".join(
        (
            f"{float(location.x):.3f}",
            f"{float(location.y):.3f}",
            f"{float(location.z):.3f}",
            f"{float(rotation.yaw):.3f}",
        )
    )
