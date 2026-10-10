"""Attempted global TM settings are observed immediately before each native setter."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import traffic_controller_step, traffic_runtime
from carla_agentic_toolkit.errors import CarlaAdapterError, OwnershipError
from carla_agentic_toolkit.models import (
    TrafficControllerStartRequest,
    TrafficDensityRequest,
    TrafficManagerRequest,
)
from tests.test_traffic_mode_policy import build_traffic_case

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaTrafficManager
    from carla_agentic_toolkit.script_settings import RunSettings

FIELD_VALUES = (
    ("global_distance_to_leading_vehicle", 3.0),
    ("global_percentage_speed_difference", -5.0),
    ("seed", 7),
    ("synchronous_mode", False),
)


@dataclass
class SettingsManager:
    """Keep acknowledgements independent of journal writes and attempted values."""

    events: list[tuple[str, str, object]] = field(default_factory=list)
    fail_on: str | None = None

    def get_port(self) -> int:
        """Return the configured TM endpoint."""
        return 8000

    def set_global_distance_to_leading_vehicle(self, value: object) -> None:
        """Record the first global setter."""
        self._record("global_distance_to_leading_vehicle", value)

    def global_percentage_speed_difference(self, value: object) -> None:
        """Record the speed-difference setter."""
        self._record("global_percentage_speed_difference", value)

    def set_random_device_seed(self, value: object) -> None:
        """Record the seed setter without resetting a live world's lights."""
        self._record("seed", value)

    def set_synchronous_mode(self, value: object) -> None:
        """Record the async-only timing setter."""
        self._record("synchronous_mode", value)

    def _record(self, setting: str, value: object) -> None:
        self.events.append(("native", setting, value))
        if setting == self.fail_on:
            message = "native setting refused"
            raise RuntimeError(message)


def full_request() -> TrafficManagerRequest:
    """Use four actual optional writes to expose premature bulk request journaling."""
    return TrafficManagerRequest(
        global_distance_to_leading_vehicle=3.0,
        global_percentage_speed_difference=-5.0,
        seed=7,
        synchronous_mode=False,
    )


def observe_setting(events: list[tuple[str, str, object]], *, setting: str, value: object) -> None:
    """Capture the phase so every native setter has preceding durable evidence."""
    events.append(("journal", setting, value))


def test_runtime_observes_each_setting_before_its_native_setter() -> None:
    """The callback runs four times in exact native write order."""
    manager = SettingsManager()

    traffic_runtime.configure_traffic_manager(
        cast("CarlaTrafficManager", manager),
        full_request(),
        before_setting=lambda **kwargs: observe_setting(manager.events, **kwargs),
    )

    assert manager.events == expected_events(FIELD_VALUES)


@pytest.mark.parametrize("failed_field", ["global_distance_to_leading_vehicle", "seed"])
def test_later_unattempted_settings_are_not_observed(failed_field: str) -> None:
    """A failed setter cannot invent evidence that later fields were attempted."""
    manager = SettingsManager(fail_on=failed_field)

    with pytest.raises(CarlaAdapterError, match="native setting refused"):
        traffic_runtime.configure_traffic_manager(
            cast("CarlaTrafficManager", manager),
            full_request(),
            before_setting=lambda **kwargs: observe_setting(manager.events, **kwargs),
        )

    stop = [name for name, _value in FIELD_VALUES].index(failed_field) + 1
    assert manager.events == expected_events(FIELD_VALUES[:stop])


def test_observer_ownership_failure_escapes_without_native_write() -> None:
    """Do not normalize a fatal journal error as an ordinary retryable native failure."""
    manager = SettingsManager()
    failure = OwnershipError("journal refused")

    def refuse(**_kwargs: object) -> None:
        raise failure

    with pytest.raises(OwnershipError) as raised:
        traffic_runtime.configure_traffic_manager(
            cast("CarlaTrafficManager", manager),
            full_request(),
            before_setting=refuse,
        )

    assert raised.value is failure
    assert manager.events == []


def test_sparse_request_only_observes_actual_fields() -> None:
    """Unset request fields must not gain a restoration target."""
    manager = SettingsManager()

    traffic_runtime.configure_traffic_manager(
        cast("CarlaTrafficManager", manager),
        TrafficManagerRequest(seed=7),
        before_setting=lambda **kwargs: observe_setting(manager.events, **kwargs),
    )

    assert manager.events == expected_events((("seed", 7),))


def expected_events(fields: tuple[tuple[str, object], ...]) -> list[tuple[str, str, object]]:
    """Pair each journal entry with the native call it precedes."""
    return [(phase, name, value) for name, value in fields for phase in ("journal", "native")]


@dataclass
class SettingsJournal:
    """Record originating episode, current port, and only attempted native fields."""

    captured: list[tuple[int, int, str, object]] = field(default_factory=list)

    def capture_traffic_manager(self, _world: object, _port: int, _enabled: object) -> None:
        """Retain the old sync-only interface so RED exposes absent per-field integration."""

    def capture_traffic_manager_setting(
        self, world: object, port: int, *, setting: str, value: object
    ) -> None:
        """Record each imminent setter with its actual originating world and port."""
        self.captured.append((cast("SimpleNamespace", world).id, port, setting, value))


def test_adapter_routes_actual_fields_to_episode_bound_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native adapter wires the observer rather than capturing only sync mode."""
    case = build_traffic_case(monkeypatch, mode=False)
    journal = SettingsJournal()
    case.adapter._settings_journal = cast("RunSettings", journal)  # noqa: SLF001

    case.adapter.configure_traffic_manager(request=full_request())

    assert journal.captured == [(case.world.id, 8000, name, value) for name, value in FIELD_VALUES]


def test_controller_captures_latest_step_fields_and_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A revised density request must not reuse start-time fields or endpoint binding."""
    case = build_traffic_case(monkeypatch, mode=False)
    journal = SettingsJournal()
    case.adapter._settings_journal = cast("RunSettings", journal)  # noqa: SLF001
    patch_controller_phases(monkeypatch)
    service = case.api._traffic_controller  # noqa: SLF001
    first = TrafficDensityRequest(vehicle_count=0, traffic_manager_port=8000, seed=7)
    latest = replace(
        first, traffic_manager_port=9000, seed=12, global_distance_to_leading_vehicle=8.0
    )
    for density in (first, latest):
        service._state = replace(  # noqa: SLF001
            service._state,  # noqa: SLF001
            request=TrafficControllerStartRequest(density=density),
        )
        service._control_once(case.client, service._state.generation)  # noqa: SLF001

    assert journal.captured == density_captures(case.world.id, first) + density_captures(
        case.world.id, latest
    )


def test_controller_journal_failure_stops_before_setters_or_another_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Global capture failure is fatal, not an ordinary retryable controller error."""
    case = build_traffic_case(monkeypatch, mode=False)
    failure = OwnershipError("settings journal refused")
    journal = Mock(capture_traffic_manager_setting=Mock(side_effect=failure))
    case.adapter._settings_journal = cast("RunSettings", journal)  # noqa: SLF001
    convergence = Mock()
    monkeypatch.setattr(traffic_controller_step, "converge_density", convergence)
    stopped = threading.Event()
    service = case.api._traffic_controller  # noqa: SLF001

    service._run(stopped, service._state.generation)  # noqa: SLF001

    assert stopped.is_set()
    assert service.get_status().error_type == "actor_ownership_failed"
    case.client.get_trafficmanager.assert_called_once_with(8000)
    case.manager.set_global_distance_to_leading_vehicle.assert_not_called()
    convergence.assert_not_called()


def patch_controller_phases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retain actual TM configuration while skipping unrelated convergence and pacing."""
    population = SimpleNamespace(
        registered=frozenset(), owned=frozenset(), spawned=(), destroyed=(), conflict=None
    )
    monkeypatch.setattr(
        traffic_controller_step, "converge_density", lambda *_args, **_kwargs: population
    )
    monkeypatch.setattr(traffic_controller_step, "apply_profiles", Mock())
    monkeypatch.setattr(traffic_controller_step, "vehicle_counts", lambda _world: (0, 0))
    monkeypatch.setattr(traffic_controller_step, "wait_for_tick", Mock())


def density_captures(
    world_id: int, request: TrafficDensityRequest
) -> list[tuple[int, int, str, object]]:
    """Describe exactly the global writes from this pass's density request."""
    fields = (
        ("global_distance_to_leading_vehicle", request.global_distance_to_leading_vehicle),
        ("global_percentage_speed_difference", request.global_percentage_speed_difference),
        ("seed", request.seed),
        ("synchronous_mode", False),
    )
    return [(world_id, request.traffic_manager_port, name, value) for name, value in fields]
