"""Protocols for the CARLA objects the managed merge fixture reads and drives."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from carla_agentic_toolkit.carla_protocols import (
        CarlaTransformObject,
        CarlaVector,
        ObjectFactory,
    )
    from carla_agentic_toolkit.managed_spec import ExperimentSpec


class MergeLaneMarking(Protocol):
    """Lane marking whose enum type is only rendered as text."""

    @property
    def type(self) -> object:
        """Return the native marking type enum."""


class MergeWaypoint(Protocol):
    """Waypoint fields and topology queries used to verify the corridor."""

    @property
    def road_id(self) -> int:
        """Return the OpenDRIVE road ID."""

    @property
    def section_id(self) -> int:
        """Return the OpenDRIVE section ID."""

    @property
    def lane_id(self) -> int:
        """Return the signed OpenDRIVE lane ID."""

    @property
    def s(self) -> float:
        """Return the distance along the road."""

    @property
    def lane_width(self) -> float:
        """Return the lane width in metres."""

    @property
    def lane_change(self) -> object:
        """Return the native lane-change permission enum."""

    @property
    def lane_type(self) -> object:
        """Return the native lane type enum."""

    @property
    def is_junction(self) -> bool:
        """Return whether the waypoint lies in a junction."""

    @property
    def transform(self) -> CarlaTransformObject:
        """Return the lane-centre pose."""

    @property
    def right_lane_marking(self) -> MergeLaneMarking:
        """Return the right boundary marking."""

    @property
    def left_lane_marking(self) -> MergeLaneMarking:
        """Return the left boundary marking."""

    def get_right_lane(self) -> MergeWaypoint | None:
        """Return the adjacent right lane, or None where CARLA has none."""

    def next(self, distance: float, /) -> Sequence[MergeWaypoint]:
        """Return successors at the given distance."""


class MergeMap(Protocol):
    """Map identity and waypoint sampling used to select the corridor."""

    @property
    def name(self) -> str:
        """Return the map asset path."""

    def generate_waypoints(self, distance: float, /) -> Sequence[MergeWaypoint]:
        """Return waypoints sampled at the given spacing."""


class MergeBoundingBox(Protocol):
    """Actor bounds; only the half-extents are read here."""

    @property
    def extent(self) -> CarlaVector:
        """Return the box half-extents in metres."""


class MergeActor(Protocol):
    """Live actor handle whose static bounds accompany snapshot motion."""

    @property
    def id(self) -> int:
        """Return the actor ID."""

    @property
    def bounding_box(self) -> MergeBoundingBox:
        """Return the actor's local bounding box."""


class MergeVehicle(MergeActor, Protocol):
    """Vehicle handle driven by the local tracker."""

    def apply_control(self, control: object, /) -> None:
        """Apply one native VehicleControl."""


class MergeSpawned(Protocol):
    """Any actor returned by a session spawn."""

    @property
    def id(self) -> int:
        """Return the actor ID."""


class MergeBlueprint(Protocol):
    """Blueprint attributes set before spawning."""

    def set_attribute(self, attribute_id: str, value: str, /) -> None:
        """Set one blueprint attribute."""

    def has_attribute(self, attribute_id: str, /) -> bool:
        """Return whether the blueprint exposes an attribute."""


class MergeBlueprintLibrary(Protocol):
    """Blueprint lookup by exact ID."""

    def find(self, blueprint_id: str, /) -> MergeBlueprint:
        """Return one blueprint, raising when the catalog lacks it."""


class MergeWorld(Protocol):
    """World access needed only for blueprint lookup."""

    def get_blueprint_library(self) -> MergeBlueprintLibrary:
        """Return the blueprint library."""


class MergeSession(Protocol):
    """Managed session contract the merge backend uses without owning timing."""

    @property
    def spec(self) -> ExperimentSpec:
        """Return the validated experiment spec."""

    @property
    def world(self) -> MergeWorld:
        """Return the owned world."""

    @property
    def map(self) -> MergeMap:
        """Return the loaded map."""

    @property
    def run_id(self) -> str:
        """Return the durable run ID."""

    @property
    def world_generation(self) -> str:
        """Return the world generation label."""

    @property
    def initial_traffic_lights(self) -> dict[str, object]:
        """Return the recorded initial traffic-light state."""

    def on_close(self, callback: Callable[[], object], /) -> None:
        """Register a callback that runs before session cleanup."""

    def spawn_actor(
        self,
        blueprint: MergeBlueprint,
        transform: object,
        *,
        role_name: str,
        controller: str,
        attach_to: MergeSpawned | None = None,
    ) -> MergeSpawned:
        """Journal and spawn one owned actor."""


class MergeCarlaModule(Protocol):
    """The carla module constructors the merge backend calls."""

    Location: ObjectFactory
    Rotation: ObjectFactory
    Transform: ObjectFactory
    VehicleControl: ObjectFactory


class MergeMeasurement(Protocol):
    """Sensor data carrying its simulator frame."""

    @property
    def frame(self) -> int:
        """Return the measurement frame."""


class MergeCollisionEvent(MergeMeasurement, Protocol):
    """Collision sensor event."""

    @property
    def normal_impulse(self) -> CarlaVector:
        """Return the collision impulse in N*s."""

    @property
    def other_actor(self) -> MergeSpawned:
        """Return the other actor in the collision."""


class MergeLaneInvasionEvent(MergeMeasurement, Protocol):
    """Lane invasion sensor event."""

    @property
    def crossed_lane_markings(self) -> Sequence[MergeLaneMarking]:
        """Return the markings crossed this frame."""


class MergeGnssMeasurement(MergeMeasurement, Protocol):
    """GNSS sensor measurement."""

    @property
    def latitude(self) -> float:
        """Return latitude in degrees."""

    @property
    def longitude(self) -> float:
        """Return longitude in degrees."""

    @property
    def altitude(self) -> float:
        """Return altitude in metres."""
