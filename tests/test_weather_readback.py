"""Weather mutation results must describe server readback, never the local request."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import experiment_scene
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

REQUESTED_CLOUDINESS = 70.0

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaWorld


@dataclass
class Weather:
    """Native-shaped weather constructor whose defaults are not a server observation."""

    cloudiness: float = 0.0
    precipitation: float = 0.0
    sun_altitude_angle: float = 0.0


@dataclass
class World:
    """A server may clamp or ignore weather, independent of the requested object."""

    applied: object = field(default_factory=Weather)
    read_error: RuntimeError | None = None
    write_error: RuntimeError | None = None
    events: list[str] = field(default_factory=list)
    requested: list[Weather] = field(default_factory=list)

    def set_weather(self, request: Weather) -> None:
        """Record a native write without pretending every parameter can change."""
        self.events.append("set_weather")
        self.requested.append(request)
        if self.write_error is not None:
            raise self.write_error

    def get_weather(self) -> object:
        """Return authoritative readback or preserve the original read failure."""
        self.events.append("get_weather")
        if self.read_error is not None:
            raise self.read_error
        return self.applied


@pytest.fixture
def native_weather(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the real runtime while replacing only its native value constructor."""
    monkeypatch.setattr(
        experiment_scene, "import_module", lambda _name: SimpleNamespace(WeatherParameters=Weather)
    )


def _api(world: object, monkeypatch: pytest.MonkeyPatch) -> CarlaScriptApi:
    adapter = PythonCarlaAdapter()
    client = SimpleNamespace(get_world=lambda: world)
    monkeypatch.setattr(adapter, "_client", lambda: client)
    return CarlaScriptApi(adapter, RunSnapshots())


def _apply(world: object, entry: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    request = {"cloudiness": REQUESTED_CLOUDINESS}
    if entry == "facade":
        return _api(world, monkeypatch).set_weather(request)
    return experiment_scene.set_weather(cast("CarlaWorld", world), request)


@pytest.mark.usefixtures("native_weather")
@pytest.mark.parametrize("entry", ["runtime", "facade"])
@pytest.mark.parametrize(
    "applied",
    [
        Weather(20.0, 0.0, 45.0),
        Weather(70.0, 12.0, 45.0),
        SimpleNamespace(
            cloudiness=0.0, precipitation=0.0, sun_altitude_angle=45.0, weatherEnabled=False
        ),
    ],
)
def test_weather_result_returns_actual_server_values(
    monkeypatch: pytest.MonkeyPatch, entry: str, applied: object
) -> None:
    """Clamped, unchanged and disabled weather all need truthful server evidence."""
    world = World(applied)

    result = _apply(world, entry, monkeypatch)

    assert result == {"weather": vars(applied)}
    assert world.events == ["set_weather", "get_weather"]
    assert world.requested[0].cloudiness == REQUESTED_CLOUDINESS


@pytest.mark.usefixtures("native_weather")
@pytest.mark.parametrize("entry", ["runtime", "facade"])
def test_failed_weather_read_never_returns_a_fake_applied_value(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """A post-write read timeout remains a failure with no invented confirmation."""
    failure = RuntimeError("weather read timed out")
    world = World(read_error=failure)

    with pytest.raises(RuntimeError, match="weather read timed out") as raised:
        _apply(world, entry, monkeypatch)

    assert raised.value is failure
    assert world.events == ["set_weather", "get_weather"]


@pytest.mark.usefixtures("native_weather")
@pytest.mark.parametrize("read_method", [None, "not callable"])
def test_unavailable_weather_read_reports_unavailable_not_requested_weather(
    monkeypatch: pytest.MonkeyPatch, read_method: object
) -> None:
    """A build without usable readback cannot report the request as observed weather."""
    requested = []
    world = SimpleNamespace(set_weather=requested.append, get_weather=read_method)

    result = _apply(world, "runtime", monkeypatch)

    assert result == {
        "available": False,
        "reason": "world.get_weather is not available in this CARLA build.",
    }
    assert len(requested) == 1


@pytest.mark.usefixtures("native_weather")
def test_weather_write_failure_remains_primary_without_read_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed mutation must not be masked by a later observation or retry."""
    failure = RuntimeError("weather write failed")
    world = World(write_error=failure)

    with pytest.raises(RuntimeError, match="weather write failed") as raised:
        _apply(world, "runtime", monkeypatch)

    assert raised.value is failure
    assert world.events == ["set_weather"]


@pytest.mark.usefixtures("native_weather")
def test_unavailable_weather_writer_keeps_existing_unavailable_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing setter does not import weather or read the world speculatively."""
    result = _apply(object(), "runtime", monkeypatch)
    assert result == {
        "available": False,
        "reason": "world.set_weather is not available in this CARLA build.",
    }
