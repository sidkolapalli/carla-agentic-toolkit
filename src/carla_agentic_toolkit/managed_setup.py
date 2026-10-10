"""Durable managed reload provenance and published native traffic-light setup."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, cast

from carla_agentic_toolkit.managed_world import SessionInvariantError, world_identity

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaVector, CarlaWorld


class ManagedReload:
    """Distinguish a prepared settings change from an unresolved native reload call."""

    def __init__(self, world_id: int, map_name: str, persist: Callable[[], None]) -> None:
        """Retain the original episode and a caller-owned atomic journal writer."""
        self.previous_world_id = world_id
        self.map_name = map_name
        self.persist = persist
        self.state: dict[str, object] = {}
        self.failure: str | None = None

    def fields(self) -> dict[str, object]:
        """Add evidence only for sessions using the managed reload contract."""
        return {"reload": self.state.copy()} if self.state else {}

    def prepare(self) -> None:
        """Persist intent before the first settings mutation, but not a native call claim."""
        self.state = {
            "phase": "prepared",
            "previous_world_id": self.previous_world_id,
            "previous_map_name": self.map_name,
        }
        self._save()

    def pending(self) -> None:
        """Quarantine an outcome that becomes uncertain as soon as native reload starts."""
        self.state["phase"] = "pending"
        self._save()

    def acknowledge(self, world_id: int) -> None:
        """Persist the native returned identity before any map or setup RPC."""
        self.state.update(phase="acknowledged", world_id=world_id)
        self._save()

    def bind_map(self, map_name: str) -> None:
        """Record the verified same-map binding without discarding raw-ID evidence."""
        if map_name != self.map_name:
            message = "Managed reload returned a different map; setup refused."
            raise SessionInvariantError(message)
        self.state["map_name"] = map_name
        self._save()

    def _save(self) -> None:
        try:
            self.persist()
        except (OSError, RuntimeError, ValueError) as exc:
            self.failure = f"Managed reload journal failure: {exc}"
            raise

    def failures(self) -> list[str]:
        """Read-only settings equality cannot resolve a lost native reload outcome."""
        if self.failure is not None:
            return [self.failure]
        if self.state.get("phase") == "pending":
            return ["Managed reload outcome is unresolved; explicit recovery review is required."]
        return []

    def load(self, value: object, world_id: int) -> None:
        """Validate durable provenance without inferring a missing acknowledgement."""
        if value is None:
            return
        if not isinstance(value, Mapping):
            self.failure = "Managed reload recovery evidence is invalid."
            return
        self.state = dict(value)
        previous = self.state.get("previous_world_id")
        phase = self.state.get("phase")
        if not _valid_reload_binding(phase, previous, self.state.get("world_id"), world_id):
            self.failure = "Managed reload recovery identity or phase is invalid."


def _valid_reload_binding(phase: object, previous: object, returned: object, bound: int) -> bool:
    if type(previous) is not int or previous <= 0 or type(phase) is not str:
        return False
    if phase in {"prepared", "pending"}:
        return previous == bound
    return all(
        (
            phase == "acknowledged",
            type(returned) is int,
            returned == bound,
            previous != bound,
        )
    )


def require_setup_episode(client: CarlaClient, world_id: int) -> None:
    """Refuse setup authority over an externally replaced episode."""
    if world_identity(client.get_world()) != world_id:
        message = "The world was replaced during managed setup; further setup refused."
        raise SessionInvariantError(message)


def returned_world_identity(world: CarlaWorld, previous_world_id: int) -> int:
    """Only an exact native integer for a new episode can authorize post-reload setup."""
    raw_id = getattr(world, "id", None)
    if type(raw_id) is not int or raw_id <= 0 or raw_id == previous_world_id:
        message = "Managed reload returned an invalid or unchanged episode identity."
        raise SessionInvariantError(message)
    return raw_id


class _TrafficLight(Protocol):
    id: int

    def get_state(self) -> object: ...

    def get_opendrive_id(self) -> str: ...

    def get_pole_index(self) -> int: ...

    def get_location(self) -> CarlaVector: ...


def reset_traffic_lights(
    world: CarlaWorld, client: CarlaClient, world_id: int
) -> dict[str, object]:
    """Reset native cycles and explicitly publish one separately accounted setup frame."""
    require_setup_episode(client, world_id)
    before = world.get_snapshot().frame
    require_setup_episode(client, world_id)
    try:
        world.reset_all_traffic_lights()
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        message = f"Native traffic-light reset is unavailable: {exc}"
        raise SessionInvariantError(message) from exc
    require_setup_episode(client, world_id)
    frame = world.tick()
    snapshot = world.get_snapshot()
    require_setup_episode(client, world_id)
    if frame != before + 1 or snapshot.frame != frame:
        message = "Traffic-light reset setup publication did not advance exactly one frame."
        raise SessionInvariantError(message)
    lights = _light_states(world, client, world_id)
    snapshot = world.get_snapshot()
    require_setup_episode(client, world_id)
    if snapshot.frame != frame:
        message = "Traffic-light metadata frame changed after reset publication."
        raise SessionInvariantError(message)
    return {"frame": frame, "lights": lights}


def _light_states(world: CarlaWorld, client: CarlaClient, world_id: int) -> list[dict[str, object]]:
    require_setup_episode(client, world_id)
    actors = world.get_actors().filter("traffic.traffic_light")
    require_setup_episode(client, world_id)
    try:
        values = [_light_state(cast("_TrafficLight", actor)) for actor in actors]
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        message = f"Native traffic-light state is unavailable: {exc}"
        raise SessionInvariantError(message) from exc
    require_setup_episode(client, world_id)
    return sorted(values, key=_light_order)


def _light_order(value: dict[str, object]) -> str:
    stable = {key: item for key, item in value.items() if key != "actor_id"}
    return json.dumps(stable, sort_keys=True, allow_nan=False)


def _light_state(light: _TrafficLight) -> dict[str, object]:
    location = light.get_location()
    coordinates = {axis: float(getattr(location, axis)) for axis in ("x", "y", "z")}
    if not all(math.isfinite(value) for value in coordinates.values()):
        message = "Non-finite native traffic-light location."
        raise ValueError(message)
    return {
        "actor_id": light.id,
        "opendrive_id": light.get_opendrive_id(),
        "pole_index": light.get_pole_index(),
        "location": coordinates,
        "state": _state_name(light.get_state()),
    }


def _state_name(value: object) -> str:
    state = str(value).split(".")[-1]
    if state not in {"Red", "Yellow", "Green", "Off", "Unknown"}:
        message = "Native traffic-light getter did not return a recognized state."
        raise ValueError(message)
    return state
