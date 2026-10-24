"""CARLA-named lifecycle and batch calls make their settings and tick choices explicit."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import experiment_environment, experiment_replay
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from tests.test_sync_settings import FakeSettings, FakeWorld

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.models import JsonObject

INITIAL_FRAME = 42


@dataclass
class DefaultsWorld(FakeWorld):
    """Expose timing without allowing actor inventory to become mutation preflight."""

    current_frame: int = INITIAL_FRAME
    inventory_allowed: bool = False

    def get_actors(self) -> SimpleNamespace:
        """Only completed replacement results need an actor inventory."""
        if not self.inventory_allowed:
            message = "Actor inventory is not required before this operation."
            raise RuntimeError(message)
        return super().get_actors()

    def get_snapshot(self) -> SimpleNamespace:
        """Publish a frame changed only by an explicit batch tick."""
        return SimpleNamespace(frame=self.current_frame)


@dataclass
class DefaultsClient:
    """Record exact CARLA arguments separately from observed server settings."""

    world: DefaultsWorld = field(
        default_factory=lambda: DefaultsWorld(
            settings=FakeSettings(synchronous_mode=True, fixed_delta_seconds=0.05)
        )
    )
    calls: list[tuple[str, object, bool]] = field(default_factory=list)
    forced_mode: bool | None = None

    def set_timeout(self, _seconds: float) -> None:
        """Retain real adapter timeout plumbing without unrelated clock behavior."""

    def get_world(self) -> DefaultsWorld:
        """Return current settings without inventory queries."""
        return self.world

    def load_world(self, map_name: str, *, reset_settings: bool = True) -> DefaultsWorld:
        """Match CARLA's documented load default."""
        self.calls.append(("load", map_name, reset_settings))
        return self.replace_world(reset_settings=reset_settings)

    def reload_world(self, reset_settings: bool = True) -> DefaultsWorld:  # noqa: FBT001, FBT002 - native CARLA signature.
        """Match CARLA's documented reload default."""
        self.calls.append(("reload", None, reset_settings))
        return self.replace_world(reset_settings=reset_settings)

    def replace_world(self, *, reset_settings: bool) -> DefaultsWorld:
        """Apply server behavior, including a deliberately different observed mode."""
        mode = False if reset_settings else self.world.settings.synchronous_mode
        if self.forced_mode is not None:
            mode = self.forced_mode
        settings = replace(self.world.settings, synchronous_mode=mode)
        self.world = DefaultsWorld(id=self.world.id + 1, settings=settings, inventory_allowed=True)
        return self.world

    def apply_batch_sync(self, commands: list[object], *, do_tick: bool = False) -> list[object]:
        """Count only the actual server tick choice, not a helper's declared default."""
        self.calls.append(("batch", commands, do_tick))
        self.world.current_frame += int(do_tick)
        if self.forced_mode is not None:
            self.world.settings.synchronous_mode = self.forced_mode
        return []


def build_case() -> tuple[DefaultsClient, PythonCarlaAdapter, CarlaScriptApi]:
    """Keep native fake, real adapter, facade and snapshots in one observed path."""
    client = DefaultsClient()
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    return client, adapter, CarlaScriptApi(adapter, RunSnapshots())


def test_load_default_resets_settings_like_carla() -> None:
    """An omitted load flag deliberately retains CARLA's True default."""
    client, adapter, _api = build_case()

    result = adapter.load_world("Town02")

    assert client.calls == [("load", "Town02", True)]
    assert result.settings.synchronous_mode is False


@pytest.mark.parametrize("reset_settings", [False, True])
def test_load_forwards_explicit_reset_flag(*, reset_settings: bool) -> None:
    """Both adapter and facade preserve the requested native reset value."""
    client, _adapter, api = build_case()

    api.load_world("Town02", reset_settings=reset_settings)

    assert client.calls == [("load", "Town02", reset_settings)]


@pytest.mark.parametrize("boundary", ["adapter", "facade"])
def test_reload_requires_explicit_reset_before_native_mutation(boundary: str) -> None:
    """No toolkit default may silently choose keep-settings over CARLA's reset."""
    client, adapter, api = build_case()
    target = adapter if boundary == "adapter" else api

    with pytest.raises(TypeError, match="reset_settings"):
        cast("Callable[[], object]", target.reload_world)()

    assert client.calls == []


@pytest.mark.parametrize("reset_settings", [False, True])
def test_reload_forwards_explicit_reset_flag(*, reset_settings: bool) -> None:
    """Required False still provides intentional keep-settings behavior."""
    client, _adapter, api = build_case()

    api.reload_world(reset_settings=reset_settings)

    assert client.calls == [("reload", None, reset_settings)]


@pytest.mark.parametrize("boundary", ["runtime", "adapter", "facade"])
def test_batch_default_does_not_tick(boundary: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """All three defaults preserve the synchronous owner's current frame."""
    monkeypatch.setattr(experiment_replay, "import_module", Mock())
    client, adapter, api = build_case()
    operations = {
        "runtime": lambda: experiment_replay.apply_batch(client, []),
        "adapter": lambda: adapter.apply_batch([]),
        "facade": lambda: api.apply_batch([]),
    }

    operations[boundary]()

    assert client.calls == [("batch", [], False)]
    assert client.world.current_frame == INITIAL_FRAME


@pytest.mark.parametrize("do_tick", [False, True])
def test_batch_reports_observed_mode_and_preserves_explicit_tick(
    monkeypatch: pytest.MonkeyPatch, *, do_tick: bool
) -> None:
    """An explicit tick stays available and the resulting mode is prominent."""
    monkeypatch.setattr(experiment_replay, "import_module", Mock())
    client, _adapter, api = build_case()

    result = api.apply_batch([], do_tick=do_tick)

    assert client.calls == [("batch", [], do_tick)]
    assert client.world.current_frame == INITIAL_FRAME + int(do_tick)
    assert result == {"responses": [], "synchronous_mode": True, "synchronous_mode_changed": False}


@pytest.mark.parametrize(
    ("starting_mode", "expected_mode"), [(True, False), (False, False), (False, True)]
)
def test_batch_reports_a_server_mode_change(
    monkeypatch: pytest.MonkeyPatch, *, starting_mode: bool, expected_mode: bool
) -> None:
    """Do not assume batch commands leave the mode unchanged."""
    monkeypatch.setattr(experiment_replay, "import_module", Mock())
    client, _adapter, api = build_case()
    client.world.settings.synchronous_mode = starting_mode
    client.forced_mode = expected_mode

    result = api.apply_batch([])

    assert result["synchronous_mode"] is expected_mode
    assert result["synchronous_mode_changed"] is (expected_mode is not starting_mode)


@pytest.mark.parametrize("operation", ["load", "reload", "opendrive"])
@pytest.mark.parametrize(
    "mode",
    [
        (True, True, None, False),
        (True, False, None, True),
        (True, True, True, True),
        (True, False, False, False),
        (False, False, None, False),
        (False, False, True, True),
    ],
)
def test_replacements_report_actual_mode_changes(
    operation: str,
    mode: tuple[bool, bool, bool | None, bool],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server settings, not reset_settings, determine both result metadata fields."""
    starting_mode, reset_settings, forced_mode, expected_mode = mode
    client, _adapter, api = build_case()
    client.world.settings.synchronous_mode = starting_mode
    client.forced_mode = forced_mode
    monkeypatch.setattr(
        experiment_environment,
        "generate_opendrive_world",
        lambda _client, **kwargs: client.replace_world(reset_settings=kwargs["reset_settings"]),
    )

    result = replace_api_world(api, operation, reset_settings=reset_settings)

    assert result["synchronous_mode"] is expected_mode
    assert result["synchronous_mode_changed"] is (expected_mode is not starting_mode)
    assert cast("dict[str, object]", result["settings"])["synchronous_mode"] is expected_mode


def replace_api_world(api: CarlaScriptApi, operation: str, *, reset_settings: bool) -> JsonObject:
    """Use each actual public replacement entry point."""
    operations = {
        "load": lambda: api.load_world("Town02", reset_settings=reset_settings),
        "reload": lambda: api.reload_world(reset_settings=reset_settings),
        "opendrive": lambda: api.generate_opendrive_world(
            "<OpenDRIVE/>", reset_settings=reset_settings
        ),
    }
    return operations[operation]()


def test_discovery_describes_explicit_defaults() -> None:
    """Generated scripts can discover the required reload flag and non-ticking batch."""
    _client, _adapter, api = build_case()

    methods = cast("dict[str, dict[str, str]]", api.describe_api()["methods"])

    assert "reset_settings: 'bool' = True" in methods["load_world"]["signature"]
    assert "reset_settings: 'bool'" in methods["reload_world"]["signature"]
    assert "reset_settings: 'bool' =" not in methods["reload_world"]["signature"]
    assert "do_tick: 'bool' = False" in methods["apply_batch"]["signature"]
