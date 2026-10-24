"""Managed repetition reloads have durable episode provenance and published light state."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import RecoveryRequiredError, SimulatorLease
from tests.test_managed_session import FakeWorld

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient

ORIGINAL_WORLD_ID = 7
RELOADED_WORLD_ID = 8
SETTLED_FRAME = 3
RESET_FRAME = 4


@dataclass
class Light:
    """Native getters expose the last published state rather than the pending reset."""

    id: int = 30
    type_id: str = "traffic.traffic_light"
    state: str = "Yellow"
    pending: str = "Yellow"
    on_state: Callable[[], None] | None = None

    def get_state(self) -> str:
        """Read the last delivered native light state."""
        if self.on_state is not None:
            self.on_state()
        return self.state

    def get_opendrive_id(self) -> str:
        """Return map-stable identity independent of actor ID."""
        return "signal-5"

    def get_pole_index(self) -> int:
        """Return the native group pole index."""
        return 2

    def get_location(self) -> SimpleNamespace:
        """Return native world coordinates."""
        return SimpleNamespace(x=1.25, y=2.5, z=3.0)


@dataclass
class ReloadWorld(FakeWorld):
    """Two different episode objects share settings only through explicit native reload."""

    events: list[tuple[str, int]] = field(default_factory=list)
    light: Light = field(default_factory=Light)
    on_map: Callable[[], None] | None = None
    on_tick: Callable[[], None] | None = None
    reset_error: str | None = None
    map_name: str = "Town10HD_Opt"

    def get_map(self) -> object:
        """Allow assertions at the first post-reload map RPC."""
        self.events.append(("map", self.id))
        if self.on_map is not None:
            self.on_map()
        return SimpleNamespace(name=self.map_name)

    def apply_settings(self, settings: SimpleNamespace) -> int:
        """Record settings writes separately from setup ticks."""
        self.events.append(("apply", self.id))
        return super().apply_settings(settings)

    def get_actors(self) -> object:
        """Report lights but no pre-existing vehicles or sensors."""
        self.events.append(("actors", self.id))
        return SimpleNamespace(
            filter=lambda pattern: [self.light] if pattern == "traffic.traffic_light" else [],
            find=lambda _actor_id: None,
        )

    def reset_all_traffic_lights(self) -> None:
        """Acknowledge reset without publishing changed getter values."""
        self.events.append(("reset", self.id))
        if self.reset_error is not None:
            raise RuntimeError(self.reset_error)
        self.light.pending = "Red"

    def tick(self) -> int:
        """Publish reset state, optionally replacing the episode during delivery."""
        self.events.append(("tick", self.id))
        self.light.state = self.light.pending
        if self.on_tick is not None:
            self.on_tick()
        return super().tick()


@dataclass
class ReloadClient:
    """CARLA reload returns a new world while retaining settings with False."""

    world: ReloadWorld
    replacement: ReloadWorld
    calls: list[bool] = field(default_factory=list)
    reload_error: str | None = None
    on_reload: Callable[[], None] | None = None

    def get_world(self) -> ReloadWorld:
        """Return the currently published episode."""
        return self.world

    def reload_world(self, *, reset_settings: bool = True) -> ReloadWorld:
        """Model settings preservation and a possible lost native response."""
        self.calls.append(reset_settings)
        self.world.events.append(("reload", self.world.id))
        self.replacement.settings = self.world.settings.copy()
        self.world = self.replacement
        if self.on_reload is not None:
            self.on_reload()
        if self.reload_error is not None:
            raise RuntimeError(self.reload_error)
        return self.world


def _case() -> tuple[ReloadWorld, ReloadWorld, ReloadClient]:
    events: list[tuple[str, int]] = []
    old = ReloadWorld(events=events)
    new = ReloadWorld(id=8, frame=3, events=events)
    return old, new, ReloadClient(old, new)


def _session(client: ReloadClient, lease: SimulatorLease) -> ManagedSession:
    return ManagedSession(ExperimentSpec(), cast("CarlaClient", client), lease, "reload-run")


def _assert_quarantined(root: Path) -> None:
    with (
        pytest.raises(RecoveryRequiredError),
        SimulatorLease("localhost", 3000, state_root=root),
    ):
        pytest.fail("An unresolved reload must not admit another mutator.")


def _reload_evidence(state: dict[str, object]) -> dict[str, object]:
    return cast("dict[str, object]", state["reload"])


def _first_light(evidence: dict[str, object]) -> dict[str, object]:
    return cast("list[dict[str, object]]", evidence["lights"])[0]


def _assert_setup_order(old: ReloadWorld, client: ReloadClient) -> None:
    assert client.calls == [False]
    assert old.events.index(("apply", 7)) < old.events.index(("reload", 7))
    assert old.settings["synchronous_mode"] is True


def _assert_reload_binding(
    session: ManagedSession, new: ReloadWorld, lease: SimulatorLease, original: dict[str, object]
) -> None:
    assert session.world is new
    assert session.world_id == RELOADED_WORLD_ID
    assert session.creation.world_id == RELOADED_WORLD_ID
    assert lease.recovery_state["settings"] == original


def _assert_original_restored(
    session: ManagedSession, new: ReloadWorld, original: dict[str, object]
) -> None:
    assert session.close()["ok"] is True
    assert new.settings == original


def _assert_reset_publication(session: ManagedSession, new: ReloadWorld) -> None:
    assert new.events.index(("reset", 8)) < new.events.index(("tick", 8))
    assert session.setup_frame_barrier["settled_frame"] == SETTLED_FRAME
    assert session.setup_frame_barrier["traffic_light_reset_frame"] == RESET_FRAME
    assert session.expected_frame == RESET_FRAME


def _assert_light_metadata(session: ManagedSession) -> None:
    assert session.initial_traffic_lights == {
        "frame": 4,
        "lights": [
            {
                "actor_id": 30,
                "opendrive_id": "signal-5",
                "pole_index": 2,
                "location": {"x": 1.25, "y": 2.5, "z": 3.0},
                "state": "Red",
            }
        ],
    }


def _interrupt_reload(session: ManagedSession, window: str) -> None:
    if window == "lost_reply":
        with pytest.raises(RuntimeError, match="reload reply lost"):
            session.open()
    else:
        session.open()


def _assert_reload_cleanup(
    report: dict[str, object],
    new: ReloadWorld,
    lease: SimulatorLease,
    original: dict[str, object],
    window: str,
) -> None:
    assert report["ok"] is (window == "acknowledged")
    if window == "lost_reply":
        _assert_unknown_cleanup(new, lease)
    else:
        assert new.settings == original
        assert not lease.recovery_state


def _assert_unknown_cleanup(new: ReloadWorld, lease: SimulatorLease) -> None:
    assert ("apply", 8) not in new.events
    assert ("tick", 8) not in new.events
    assert lease.recovery_state


def _assert_no_reload_setup(new: ReloadWorld) -> None:
    assert ("reset", 8) not in new.events
    assert ("map", 8) not in new.events


def _assert_journal_call_counts(old: ReloadWorld, client: ReloadClient, phase: str) -> None:
    assert client.calls == ([False] if phase == "acknowledged" else [])
    if phase == "prepared":
        assert ("apply", 7) not in old.events


def _assert_repetition_closed(session: ManagedSession) -> None:
    assert session.close()["ok"] is True


def _assert_stable_light(evidence: dict[str, object]) -> None:
    assert _first_light(evidence)["state"] == "Red"
    assert _first_light(evidence)["opendrive_id"] == "signal-5"
