"""Protocol definitions for CARLA's dynamic Python API objects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator


class CarlaClientFactory(Protocol):
    """Factory for CARLA clients."""

    def __call__(self, host: str, port: int) -> object:
        """Create a CARLA client object."""


class ObjectFactory(Protocol):
    """Factory for dynamic CARLA objects."""

    def __call__(self, *args: object, **kwargs: object) -> object:
        """Create a CARLA runtime object."""


@runtime_checkable
class CarlaClient(Protocol):
    """Subset of CARLA client methods needed by the MCP server."""

    def set_timeout(self, seconds: float) -> None:
        """Set the client network timeout."""

    def get_world(self) -> CarlaWorld:
        """Return the current world."""

    def get_available_maps(self) -> list[str]:
        """Return available map names."""

    def get_client_version(self) -> object:
        """Return CARLA client version."""

    def get_server_version(self) -> object:
        """Return CARLA server version."""

    def load_world(self, map_name: str) -> CarlaWorld:
        """Load a world by map name."""

    def get_trafficmanager(self, port: int = 8000) -> CarlaTrafficManager:
        """Return a Traffic Manager on the requested port."""

    def start_recorder(self, path: str) -> str:
        """Start the recorder and return the simulator-accepted path."""

    def stop_recorder(self) -> None:
        """Stop the CARLA recorder."""


class CarlaWorld(Protocol):
    """Subset of CARLA world methods needed by the MCP server."""

    def get_map(self) -> CarlaMap:
        """Return the current CARLA map."""

    def get_settings(self) -> CarlaWorldSettings:
        """Return world settings."""

    def get_actors(self, actor_ids: list[int] | None = None, /) -> CarlaActorList:
        """Return cached actors, or directly resolve the supplied actor IDs."""

    def get_snapshot(self) -> CarlaSnapshot:
        """Return the latest world snapshot."""

    def apply_settings(self, settings: CarlaWorldSettings) -> int:
        """Apply updated world settings and return a frame ID."""

    def tick(self) -> int:
        """Advance one frame."""

    def wait_for_tick(self, seconds: float) -> CarlaSnapshot:
        """Wait for the next asynchronous simulation tick."""

    def get_blueprint_library(self) -> CarlaBlueprintLibrary:
        """Return the blueprint library."""

    def spawn_actor(
        self,
        blueprint: CarlaBlueprint,
        transform: object,
        attach_to: object | None = None,
    ) -> CarlaActor:
        """Spawn one actor."""

    def try_spawn_actor(
        self,
        blueprint: CarlaBlueprint,
        transform: object,
        attach_to: object | None = None,
    ) -> CarlaActor | None:
        """Try to spawn one actor."""


class CarlaMap(Protocol):
    """Subset of a CARLA map object."""

    name: str

    def get_spawn_points(self) -> list[object]:
        """Return recommended vehicle spawn points."""


class CarlaWorldSettings(Protocol):
    """Subset of CARLA world settings."""

    synchronous_mode: bool
    fixed_delta_seconds: float | None
    no_rendering_mode: bool


class CarlaVector(Protocol):
    """Subset of CARLA vector/location objects."""

    x: float
    y: float
    z: float


class CarlaRotation(Protocol):
    """Subset of CARLA rotation objects."""

    pitch: float
    yaw: float
    roll: float


class CarlaTransformObject(Protocol):
    """Subset of CARLA transform objects."""

    location: CarlaVector
    rotation: CarlaRotation


class CarlaActorList(Protocol):
    """Subset of a CARLA actor list."""

    def filter(self, wildcard_pattern: str) -> list[object]:
        """Filter actors by CARLA wildcard pattern."""

    def find(self, actor_id: int) -> object | None:
        """Find an actor by ID."""


class CarlaBlueprintLibrary(Protocol):
    """Subset of CARLA blueprint library methods."""

    def filter(self, wildcard_pattern: str) -> list[CarlaBlueprint]:
        """Filter blueprints by CARLA wildcard pattern."""

    def find(self, blueprint_id: str) -> CarlaBlueprint:
        """Find one blueprint by ID."""


class CarlaBlueprint(Protocol):
    """Subset of CARLA actor blueprint methods and attributes."""

    id: str
    tags: list[str]

    def __iter__(self) -> Iterator[object]:
        """Iterate blueprint attributes."""

    def set_attribute(self, attribute_id: str, value: str) -> None:
        """Set a blueprint attribute."""

    def has_attribute(self, attribute_id: str) -> bool:
        """Return whether a blueprint attribute exists."""

    def get_attribute(self, attribute_id: str) -> object:
        """Return a blueprint attribute."""


class CarlaBlueprintAttribute(Protocol):
    """Subset of CARLA blueprint attribute fields."""

    id: str
    is_modifiable: bool
    recommended_values: list[str]


class CarlaActor(Protocol):
    """Subset of a spawned CARLA actor."""

    id: int
    type_id: str
    attributes: dict[str, str]

    def get_transform(self) -> CarlaTransformObject:
        """Return actor transform."""

    def get_velocity(self) -> CarlaVector:
        """Return actor velocity."""

    def destroy(self) -> bool:
        """Destroy the actor."""

    def set_autopilot(self, *args: object) -> None:
        """Register or unregister an actor with Traffic Manager."""


class CarlaTrafficManager(Protocol):
    """Subset of CARLA Traffic Manager methods used by the MCP server."""

    def get_port(self) -> int:
        """Return the Traffic Manager port."""

    def set_global_distance_to_leading_vehicle(self, distance: float) -> None:
        """Set global following distance."""

    def global_percentage_speed_difference(self, percentage: float) -> None:
        """Set global speed difference from speed limits."""

    def set_random_device_seed(self, seed: int) -> None:
        """Set Traffic Manager random seed."""

    def set_synchronous_mode(self, *args: object) -> None:
        """Configure Traffic Manager synchronous mode."""


class CarlaSensor(CarlaActor, Protocol):
    """Subset of a CARLA sensor actor."""

    def listen(self, callback: object) -> None:
        """Subscribe to sensor frames."""

    def stop(self) -> None:
        """Stop sensor callbacks."""


class CarlaImage(Protocol):
    """Subset of a CARLA image/frame object."""

    frame: int

    def save_to_disk(self, path: str) -> None:
        """Save the frame to disk."""


class CarlaSnapshot(Protocol):
    """Subset of a CARLA world snapshot."""

    frame: int
