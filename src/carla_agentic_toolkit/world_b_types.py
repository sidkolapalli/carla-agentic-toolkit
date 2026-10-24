"""CARLA runtime protocols for experiment geometry, vehicle, and recorder helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import (
        CarlaRotation,
        CarlaTransformObject,
        CarlaVector,
    )


class CarlaBoundingBox(Protocol):
    """Native carla.BoundingBox geometry fields."""

    location: CarlaVector
    extent: CarlaVector
    rotation: CarlaRotation


class CarlaEnvironmentObject(Protocol):
    """Fields of a carla.EnvironmentObject returned by get_environment_objects."""

    id: int
    name: str
    type: object
    transform: CarlaTransformObject
    bounding_box: CarlaBoundingBox


class CarlaBoundedActor(Protocol):
    """A live actor handle exposing its native local bounding box."""

    bounding_box: CarlaBoundingBox


class CarlaAttributedActor(Protocol):
    """A live actor handle exposing its blueprint type and attributes."""

    type_id: str
    attributes: dict[str, str]


class CarlaRawImage(Protocol):
    """Measured carla.Image dimensions and its unvalidated pixel buffer."""

    width: int
    height: int
    raw_data: object


class CarlaMovableActor(Protocol):
    """Teleport and target-velocity methods every carla.Actor exposes."""

    def set_transform(self, transform: object, /) -> None:
        """Teleport the actor to a carla.Transform."""

    def set_target_velocity(self, velocity: object, /) -> None:
        """Set the actor's target carla.Vector3D velocity."""


class CarlaDrivableVehicle(CarlaMovableActor, Protocol):
    """Direct control and light methods of a carla.Vehicle."""

    def apply_control(self, control: object, /) -> None:
        """Apply a carla.VehicleControl."""

    def set_light_state(self, light_state: object, /) -> None:
        """Set a carla.VehicleLightState mask."""


class CarlaRecorderClient(Protocol):
    """Recorder and batch methods of carla.Client."""

    def replay_file(
        self,
        name: str,
        time_start: float,
        duration: float,
        follow_id: int,
        replay_sensors: bool,  # noqa: FBT001 - CARLA takes replay_sensors positionally.
        /,
    ) -> str:
        """Replay a recorder file and return CARLA's report text."""

    def show_recorder_collisions(self, name: str, type1: str, type2: str, /) -> str:
        """Return recorder collision report text."""

    def show_recorder_actors_blocked(
        self, name: str, min_time: float, min_distance: float, /
    ) -> str:
        """Return recorder blocked-actor report text."""

    def apply_batch_sync(self, commands: list[object], /, *, do_tick: bool = False) -> list[object]:
        """Apply commands and return one carla.command.Response per command."""


class CarlaCommandNamespace(Protocol):
    """The carla.command constructors used by reviewed batches."""

    DestroyActor: Callable[[int], object]


class CarlaCommandModule(Protocol):
    """The carla module's command namespace."""

    command: CarlaCommandNamespace
