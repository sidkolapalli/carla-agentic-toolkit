"""Built-in Traffic Manager behavior profiles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BehaviorProfile:
    """Traffic Manager behavior settings for one profile."""

    speed_difference: float
    distance_to_leading_vehicle: float
    auto_lane_change: bool
    ignore_lights_percentage: float
    ignore_signs_percentage: float
    ignore_vehicles_percentage: float

    def to_settings(self) -> dict[str, float | bool]:
        """Return public profile settings."""
        return {
            "speed_difference": self.speed_difference,
            "distance_to_leading_vehicle": self.distance_to_leading_vehicle,
            "auto_lane_change": self.auto_lane_change,
            "ignore_lights_percentage": self.ignore_lights_percentage,
            "ignore_signs_percentage": self.ignore_signs_percentage,
            "ignore_vehicles_percentage": self.ignore_vehicles_percentage,
        }


_PROFILES = {
    "normal": BehaviorProfile(
        speed_difference=0.0,
        distance_to_leading_vehicle=4.0,
        auto_lane_change=True,
        ignore_lights_percentage=0.0,
        ignore_signs_percentage=0.0,
        ignore_vehicles_percentage=0.0,
    ),
    "cautious": BehaviorProfile(
        speed_difference=25.0,
        distance_to_leading_vehicle=8.0,
        auto_lane_change=False,
        ignore_lights_percentage=0.0,
        ignore_signs_percentage=0.0,
        ignore_vehicles_percentage=0.0,
    ),
    "aggressive": BehaviorProfile(
        speed_difference=-20.0,
        distance_to_leading_vehicle=2.0,
        auto_lane_change=True,
        ignore_lights_percentage=0.0,
        ignore_signs_percentage=0.0,
        ignore_vehicles_percentage=10.0,
    ),
    "impatient": BehaviorProfile(
        speed_difference=-10.0,
        distance_to_leading_vehicle=2.0,
        auto_lane_change=True,
        ignore_lights_percentage=20.0,
        ignore_signs_percentage=20.0,
        ignore_vehicles_percentage=5.0,
    ),
    "stalled": BehaviorProfile(
        speed_difference=100.0,
        distance_to_leading_vehicle=1.0,
        auto_lane_change=False,
        ignore_lights_percentage=0.0,
        ignore_signs_percentage=0.0,
        ignore_vehicles_percentage=0.0,
    ),
}


def behavior_profile(name: str) -> BehaviorProfile | None:
    """Return a built-in vehicle behavior profile."""
    return _PROFILES.get(name)
