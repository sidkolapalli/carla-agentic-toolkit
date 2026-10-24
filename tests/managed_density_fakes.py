"""Native-like managed-density doubles with a real, process-owned TCP listener."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.managed_spec import ExperimentSpec
from tests.test_managed_session import FakeWorld

if TYPE_CHECKING:
    from collections.abc import Callable


def transform(x: float = 0.0, y: float = 0.0) -> SimpleNamespace:
    """Use actual transform fields consumed by the managed spawn journal."""
    return SimpleNamespace(
        location=SimpleNamespace(x=x, y=y, z=0.0),
        rotation=SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0),
    )


class StringAttribute:
    """String CARLA attributes are not integer attributes or their debug repr."""

    def __init__(self, value: str) -> None:
        """Retain the exact native-like attribute text."""
        self.value = value

    def as_str(self) -> str:
        """Return the attribute value, not a debug representation."""
        return self.value

    def as_int(self) -> int:
        """Reject an integer conversion of a string attribute."""
        message = "This attribute is a string."
        raise ValueError(message)


@dataclass
class DensityBlueprint:
    """A configured car blueprint retains role identity for native creation."""

    id: str = "vehicle.test.car"
    attributes: dict[str, str] = field(default_factory=lambda: {"base_type": "car"})

    def has_attribute(self, name: str) -> bool:
        """Distinguish missing attributes from present empty values."""
        return name in self.attributes

    def get_attribute(self, name: str) -> StringAttribute:
        """Expose CARLA's string-attribute accessor shape."""
        return StringAttribute(self.attributes[name])

    def set_attribute(self, name: str, value: str) -> None:
        """Retain configured role data for raw actor creation."""
        self.attributes[name] = value


@dataclass
class DensityActor:
    """Live pose reads can fail while the immutable snapshot remains valid."""

    id: int
    world: DensityWorld
    type_id: str = "vehicle.test.car"
    attributes: dict[str, str] = field(default_factory=dict)
    pose: SimpleNamespace = field(default_factory=transform)
    autopilot_calls: list[tuple[bool, int]] = field(default_factory=list)
    autopilot_error: str = ""
    live_pose_error: bool = False
    bounding_box: object = field(
        default_factory=lambda: SimpleNamespace(extent=SimpleNamespace(x=2.0, y=1.0, z=0.8))
    )

    def set_autopilot(self, enabled: bool, port: int) -> None:  # noqa: FBT001
        """Log explicit registration without changing actor ownership."""
        self.world.calls.append(("autopilot", self.id, enabled, port))
        self.autopilot_calls.append((enabled, port))
        if self.autopilot_error:
            raise RuntimeError(self.autopilot_error)

    def get_transform(self) -> SimpleNamespace:
        """Optionally refuse live reads to enforce snapshot observations."""
        if self.live_pose_error:
            message = "Live pose reads are forbidden in frozen observations."
            raise AssertionError(message)
        return self.pose

    def get_location(self) -> object:
        """Match the native handle's live location accessor."""
        return self.get_transform().location

    def get_velocity(self) -> object:
        """Optionally refuse live velocity reads beside a frozen snapshot."""
        if self.live_pose_error:
            message = "Live velocity reads are forbidden in frozen observations."
            raise AssertionError(message)
        return SimpleNamespace(x=0.0, y=0.0, z=0.0)


@dataclass
class DensityWorld(FakeWorld):
    """Only callbacks/native operations advance the observable evidence log."""

    calls: list[tuple[object, ...]] = field(default_factory=list)
    spawn_results: list[object] = field(default_factory=list)
    before_spawn: Callable[[], None] | None = None
    tick_calls: int = 0
    wait_calls: int = 0
    next_id: int = 11
    spawn_points: list[object] = field(
        default_factory=lambda: [transform(float(index) * 20.0) for index in range(12)]
    )

    def apply_settings(self, settings: SimpleNamespace) -> int:
        """Log settings mutations alongside native TM operations."""
        self.calls.append(("settings", settings.synchronous_mode))
        return super().apply_settings(settings)

    def get_map(self) -> object:
        """Expose deterministic native-like map spawn points."""
        return SimpleNamespace(name="Town10HD_Opt", get_spawn_points=lambda: self.spawn_points)

    def get_blueprint_library(self) -> object:
        """Return safe car blueprints with actual string-attribute accessors."""
        return SimpleNamespace(
            filter=lambda _pattern: [DensityBlueprint()],
            find=lambda name: DensityBlueprint(id=name),
        )

    def get_actors(self) -> object:
        """Keep external actor inventory distinct from owned handle records."""
        return SimpleNamespace(
            filter=lambda pattern: [a for a in self.actors if a.type_id.startswith(pattern[:-1])],
            find=lambda actor_id: next((a for a in self.actors if a.id == actor_id), None),
        )

    def get_snapshot(self) -> SimpleNamespace:
        """Freeze actor poses at the current owner frame."""
        values = {a.id: _frozen_actor(cast("DensityActor", a)) for a in self.actors}
        return SimpleNamespace(
            frame=self.frame,
            timestamp=SimpleNamespace(elapsed_seconds=self.frame * 0.05),
            find=values.get,
        )

    def tick(self) -> int:
        """Record every explicit owner tick."""
        self.tick_calls += 1
        self.calls.append(("tick",))
        return super().tick()

    def wait_for_tick(self, _seconds: float) -> SimpleNamespace:
        """Record waits so hidden advancement is visible to negative controls."""
        self.wait_calls += 1
        self.calls.append(("wait", _seconds))
        return super().wait_for_tick(_seconds)

    def spawn_actor(self, blueprint: DensityBlueprint, pose: object, **_kwargs: object) -> object:
        """Use the same returned-ID boundary as optional native creation."""
        return self.try_spawn_actor(blueprint, pose)

    def try_spawn_actor(self, blueprint: DensityBlueprint, pose: object) -> object:
        """Return collisions or unknown replies without fabricating actor IDs."""
        self.calls.append(("spawn",))
        if self.before_spawn is not None:
            self.before_spawn()
        result = (
            self.spawn_results.pop(0) if self.spawn_results else self._new_actor(blueprint, pose)
        )
        if isinstance(result, BaseException):
            raise result
        return result

    def _new_actor(self, blueprint: DensityBlueprint, pose: object) -> DensityActor:
        actor = DensityActor(
            self.next_id,
            self,
            type_id=blueprint.id,
            attributes=blueprint.attributes.copy(),
            pose=cast("SimpleNamespace", pose),
        )
        self.next_id += 1
        self.actors.append(cast("SimpleNamespace", actor))
        return actor


def _frozen_actor(actor: DensityActor) -> SimpleNamespace:
    pose = transform(actor.pose.location.x, actor.pose.location.y)
    return SimpleNamespace(
        get_transform=lambda: pose,
        get_velocity=lambda: SimpleNamespace(x=0.0, y=0.0, z=0.0),
    )


class DensityManager:
    """No seed/global methods exist; a remote handle never owns a LISTEN socket."""

    def __init__(self, world: DensityWorld, port: int, *, local: bool = True) -> None:
        """Create only this test's local listener when requested."""
        self.world, self.port = world, port
        self.listener: socket.socket | None = None
        self.mode_error: str = ""
        self.shutdown_error: str = ""
        if local:
            self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.listener.bind(("127.0.0.1", port))
            self.listener.listen()

    def get_port(self) -> int:
        """Expose the native manager's advertised exact port."""
        return self.port

    def set_synchronous_mode(self, enabled: bool) -> None:  # noqa: FBT001
        """Log mode acknowledgement without offering an invented getter."""
        self.world.calls.append(("tm_mode", enabled))
        if self.mode_error:
            raise RuntimeError(self.mode_error)

    def shut_down(self) -> None:
        """Close only the fixture's explicitly created listener."""
        self.world.calls.append(("tm_shutdown",))
        if self.shutdown_error:
            raise RuntimeError(self.shutdown_error)
        self.release_listener()

    def release_listener(self) -> None:
        """Release an exact test-owned socket after assertions."""
        if self.listener is not None:
            self.listener.close()
            self.listener = None


class DensityClient:
    """Native manager construction is a separate logged operation."""

    def __init__(self, world: DensityWorld, *, local: bool = True) -> None:
        """Keep manager construction separate from client/world access."""
        self.world, self.local = world, local
        self.manager: DensityManager | None = None
        self.manager_error: str = ""

    def get_world(self) -> DensityWorld:
        """Return the actual current test episode."""
        return self.world

    def get_client_version(self) -> str:
        """Supply an explicit compatible fake native release."""
        return "0.9.16-test"

    def get_server_version(self) -> str:
        """Supply the matching fake server release."""
        return "0.9.16-test"

    def reload_world(self, *, reset_settings: bool) -> DensityWorld:
        """Return the actual acknowledged replacement episode after reload."""
        self.world.calls.append(("reload", reset_settings))
        self.world.reload_world(reset_settings)
        return self.world

    def get_trafficmanager(self, port: int) -> DensityManager:
        """Log construction and retain a local or remote-like handle."""
        self.world.calls.append(("tm_construct", port))
        if self.manager_error:
            raise RuntimeError(self.manager_error)
        if self.manager is None:
            self.manager = DensityManager(self.world, port, local=self.local)
        return self.manager

    def release_listener(self) -> None:
        """Release only the manager created by this fixture."""
        if self.manager is not None:
            self.manager.release_listener()


def unused_port() -> int:
    """Allocate a test-only ephemeral port without touching any simulator."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return cast("int", listener.getsockname()[1])


def density_spec(port: int, *, count: int = 2, interval: int = 1) -> ExperimentSpec:
    """Use the strict public config after tests reproduced its missing behavior."""
    return ExperimentSpec.model_validate(
        {
            "background_density": {
                "vehicle_count": count,
                "traffic_manager_port": port,
                "maintenance_interval_steps": interval,
            }
        }
    )
