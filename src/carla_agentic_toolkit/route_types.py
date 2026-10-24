"""Structural types for the native CARLA objects the route fixture reads and actuates."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from carla_agentic_toolkit.carla_protocols import CarlaActor

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import (
        CarlaBlueprint,
        CarlaTransformObject,
        CarlaVector,
        CarlaWorld,
        ObjectFactory,
    )
    from carla_agentic_toolkit.managed_spec import ExperimentSpec


class CarlaRouteModule(Protocol):
    """The ``carla`` module constructors used to place and actuate route actors."""

    Location: ObjectFactory
    Rotation: ObjectFactory
    Transform: ObjectFactory
    Vector3D: ObjectFactory
    VehicleControl: ObjectFactory
    WalkerControl: ObjectFactory


class RouteWaypoint(Protocol):
    """A native ``carla.Waypoint`` as the reviewed route walk reads it."""

    lane_type: object
    lane_width: float
    lane_id: int
    road_id: int
    transform: CarlaTransformObject

    def next(self, distance: float) -> list[RouteWaypoint]:
        """Return the legal successors ``distance`` metres ahead."""


class RouteMap(Protocol):
    """A native ``carla.Map`` that projects locations onto driving lanes."""

    def get_waypoint(self, location: object) -> RouteWaypoint:
        """Return the driving-lane waypoint nearest ``location``."""


class RouteBoundingBox(Protocol):
    """A native actor bounding box; ``extent`` holds half-lengths in metres."""

    extent: CarlaVector


class RouteActorHandle(CarlaActor, Protocol):
    """A native vehicle or walker whose static bounds the route observation measures."""

    bounding_box: RouteBoundingBox


class RouteControlledActor(RouteActorHandle, Protocol):
    """An owned vehicle or walker that accepts its native control command."""

    def apply_control(self, control: object) -> None:
        """Apply one ``carla.VehicleControl`` or ``carla.WalkerControl``."""


class RouteVehicle(RouteControlledActor, Protocol):
    """An owned ``carla.Vehicle``; walkers have no traffic-light trigger reads."""

    def is_at_traffic_light(self) -> bool:
        """Return whether the vehicle is inside a traffic-light trigger volume."""

    def get_traffic_light_state(self) -> object:
        """Return the ``carla.TrafficLightState`` affecting the vehicle."""


class RouteSession(Protocol):
    """The managed-session duties route actors use; it alone ticks and cleans up."""

    spec: ExperimentSpec
    run_id: str
    world_generation: str
    initial_traffic_lights: dict[str, object]
    world: CarlaWorld
    map: RouteMap

    def spawn_actor(
        self,
        blueprint: CarlaBlueprint,
        transform: object,
        *,
        role_name: str,
        controller: str,
        attach_to: CarlaActor | None = None,
    ) -> CarlaActor:
        """Journal ownership before native creation and return the native actor."""

    def on_close(self, callback: Callable[[], object]) -> None:
        """Run ``callback`` before the session destroys owned actors."""
