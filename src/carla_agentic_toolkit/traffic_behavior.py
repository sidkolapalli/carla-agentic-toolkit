"""Apply named Traffic Manager behavior profiles to authoritative actor handles."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.actor_runtime import actor_by_id
from carla_agentic_toolkit.behavior_profiles import behavior_profile

if TYPE_CHECKING:
    from carla_agentic_toolkit.behavior_profiles import BehaviorProfile
    from carla_agentic_toolkit.carla_protocols import CarlaActor, CarlaTrafficManager, CarlaWorld
    from carla_agentic_toolkit.models import VehicleBehaviorRequest


def apply_profiles(
    world: CarlaWorld,
    traffic_manager_instance: CarlaTrafficManager,
    behaviors: dict[int, str],
) -> None:
    """Apply behavior profiles to registered actor IDs."""
    for actor_id, profile_name in behaviors.items():
        actor = actor_by_id(world, actor_id)
        profile = behavior_profile(profile_name)
        if actor is not None and profile is not None:
            _apply_profile(traffic_manager_instance, cast("CarlaActor", actor), profile)


def apply_behavior_to_world(
    world: CarlaWorld,
    traffic_manager_instance: CarlaTrafficManager,
    request: VehicleBehaviorRequest,
) -> dict[int, str]:
    """Apply a behavior request and return per-actor failures."""
    failures: dict[int, str] = {}
    profile = behavior_profile(request.profile)
    for actor_id in request.actor_ids:
        actor = actor_by_id(world, actor_id)
        if actor is None:
            failures[actor_id] = "Actor was not found."
        elif profile is not None:
            _apply_profile(traffic_manager_instance, cast("CarlaActor", actor), profile)
    return failures


def _apply_profile(
    traffic_manager_instance: CarlaTrafficManager,
    actor: CarlaActor,
    profile: BehaviorProfile,
) -> None:
    """Apply one behavior profile to one actor."""
    autopilot_enabled = True
    actor.set_autopilot(autopilot_enabled, traffic_manager_instance.get_port())
    _call_manager(
        traffic_manager_instance,
        "vehicle_percentage_speed_difference",
        actor,
        profile.speed_difference,
    )
    _call_manager(
        traffic_manager_instance,
        "distance_to_leading_vehicle",
        actor,
        profile.distance_to_leading_vehicle,
    )
    _call_manager(traffic_manager_instance, "auto_lane_change", actor, profile.auto_lane_change)
    _call_manager(
        traffic_manager_instance,
        "ignore_lights_percentage",
        actor,
        profile.ignore_lights_percentage,
    )
    _call_manager(
        traffic_manager_instance,
        "ignore_signs_percentage",
        actor,
        profile.ignore_signs_percentage,
    )
    _call_manager(
        traffic_manager_instance,
        "ignore_vehicles_percentage",
        actor,
        profile.ignore_vehicles_percentage,
    )


def _call_manager(manager: CarlaTrafficManager, method_name: str, *args: object) -> None:
    """Call an optional Traffic Manager method."""
    method = getattr(manager, method_name, None)
    if callable(method):
        method(*args)
