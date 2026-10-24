"""CARLA protocols for navigation, scene, and walker runtime helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaTransformObject, CarlaVector


class CarlaWaypoint(Protocol):
    """Subset of a CARLA map waypoint."""

    transform: CarlaTransformObject

    def next(self, distance: float, /) -> list[CarlaWaypoint]:
        """Return successor waypoints about distance meters ahead."""


class CarlaNavigationMap(Protocol):
    """Map queries used by navigation helpers."""

    def get_waypoint(
        self,
        location: object,
        /,
        *,
        project_to_road: bool = True,
        lane_type: object = ...,
    ) -> CarlaWaypoint:
        """Return the waypoint for a location (None when unprojected and off-road)."""

    def get_topology(self) -> list[tuple[CarlaWaypoint, CarlaWaypoint]]:
        """Return road topology as entry/exit waypoint pairs."""


class CarlaNavigationModule(Protocol):
    """The carla module enums used by navigation helpers."""

    LaneType: object


class CarlaSceneModule(Protocol):
    """The carla module enums used by scene helpers."""

    TrafficLightState: object


class CarlaSpectator(Protocol):
    """Subset of the CARLA spectator actor."""

    def get_transform(self) -> CarlaTransformObject:
        """Return the spectator transform."""

    def set_transform(self, transform: object, /) -> None:
        """Move the spectator."""


class CarlaSceneWorld(Protocol):
    """World methods used by scene helpers."""

    def freeze_all_traffic_lights(
        self,
        frozen: bool,  # noqa: FBT001 - Mirrors CARLA's native positional signature.
        /,
    ) -> None:
        """Freeze or unfreeze every traffic light."""

    def get_spectator(self) -> CarlaSpectator:
        """Return the spectator actor."""

    def set_weather(self, weather: object, /) -> None:
        """Apply WeatherParameters."""


class CarlaTrafficLight(Protocol):
    """Subset of a CARLA traffic light actor."""

    def set_state(self, state: object, /) -> None:
        """Set a TrafficLightState."""


class CarlaWalkerController(Protocol):
    """Subset of a CARLA AI walker controller."""

    def start(self) -> None:
        """Register the controller with native navigation."""

    def stop(self) -> None:
        """Remove the controller from native navigation."""

    def set_max_speed(self, speed: float, /) -> None:
        """Set the walker's maximum speed."""

    def go_to_location(self, destination: object, /) -> None:
        """Send the walker to a CARLA location."""


class CarlaWalker(Protocol):
    """Subset of a CARLA pedestrian actor."""

    def apply_control(self, control: object, /) -> None:
        """Apply a WalkerControl."""


class CarlaPedestrianWorld(Protocol):
    """World methods used by walker helpers."""

    def set_pedestrians_seed(self, seed: int, /) -> None:
        """Seed native pedestrian navigation."""

    def get_random_location_from_navigation(self) -> CarlaVector | None:
        """Return a random navigable location, or None."""

    def tick(self, seconds: float = 0.0, /) -> int:
        """Advance one synchronous frame within a timeout."""
