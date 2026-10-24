"""Inherited sensor listeners retain their original handle and episode until cleanup."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter as adapter_module
from carla_agentic_toolkit import experiment_replay, script_runner
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import CameraAttachRequest, Location, Rotation, Transform
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.models import JsonObject
    from carla_agentic_toolkit.rpc_timeouts import RpcTimeoutPolicy
    from carla_agentic_toolkit.script_settings import RunSettings

SENSOR_ID = 7
UNRELATED_ID = 91
ORIGIN_WORLD = 17
STOP_ATTEMPTS = 2


@dataclass
class ServerSensor:
    """Represent the shared server actor, not a client handle's listening state."""

    exists: bool = True
    actor_id: int = SENSOR_ID
    events: list[str] = field(default_factory=list)
    handles: list[SensorHandle] = field(default_factory=list)

    def handle(self) -> SensorHandle:
        """Return a fresh native-like handle with independent local listener bits."""
        handle = SensorHandle(self, f"lookup-{len(self.handles)}")
        self.handles.append(handle)
        return handle

    def delete(self, source: str) -> bool:
        """Acknowledge deletion of the shared actor exactly once."""
        self.events.append(source)
        if not self.exists:
            return False
        self.exists = False
        return True


class SensorHandle:
    """Model local listening state independently from the shared server actor."""

    id = SENSOR_ID
    type_id = "sensor.camera.rgb"

    def __init__(self, server: ServerSensor, name: str) -> None:
        """Keep the native stop acknowledgment separate from server deletion."""
        self.server = server
        self.id = server.actor_id
        self.attributes: dict[str, str] = {}
        self.name = name
        self.listening = False
        self.callback: Callable[[object], None] | None = None
        self.stops = 0
        self.stop_failures = 0
        self.fail_always = False
        self.during_stop: Callable[[], None] | None = None

    def listen(self, callback: Callable[[object], None]) -> None:
        """Install a listener on this handle alone."""
        self.callback = callback
        self.listening = True
        self.server.events.append(f"listen:{self.name}")

    def is_listening(self) -> bool:
        """Expose the client-local state used by existing native stop helpers."""
        return self.listening

    def stop(self) -> None:
        """Inject failures explicitly; a stop after deletion is a fake failure only."""
        self.stops += 1
        self.server.events.append(f"stop:{self.name}")
        if self.during_stop is not None:
            self.during_stop()
        if self.fail_always or self.stop_failures:
            self.stop_failures = max(0, self.stop_failures - 1)
            message = "original listener stop failed"
            raise RuntimeError(message)
        if not self.server.exists:
            message = "fake stop failure injected after actor deletion"
            raise RuntimeError(message)
        self.listening = False

    def destroy(self) -> bool:
        """Expose the direct-destroy regression through the real actor runtime."""
        return self.server.delete(f"direct_destroy:{self.name}")

    def get_transform(self) -> SimpleNamespace:
        """Satisfy the reviewed actor protocol without unrelated simulator behavior."""
        return SimpleNamespace()

    def get_velocity(self) -> SimpleNamespace:
        """Satisfy the reviewed actor protocol for explicit destruction."""
        return SimpleNamespace()


@dataclass
class SensorWorld:
    """Return fresh lookup handles while retaining the shared server actor."""

    id: int = ORIGIN_WORLD
    server: ServerSensor = field(default_factory=ServerSensor)
    sentinel: ServerSensor = field(default_factory=lambda: ServerSensor(actor_id=UNRELATED_ID))
    ticks: int = 0

    def get_actors(self, _ids: object = None) -> SensorWorld:
        """Expose the lookup collection used by actor and sensor runtime helpers."""
        return self

    def get_settings(self) -> SimpleNamespace:
        """Expose asynchronous mode for public capture preflight."""
        return SimpleNamespace(synchronous_mode=False, no_rendering_mode=False)

    def find(self, actor_id: int) -> SensorHandle | None:
        """Lookups do not copy another handle's listener registration."""
        actors = {SENSOR_ID: self.server, UNRELATED_ID: self.sentinel}
        server = actors.get(actor_id)
        return server.handle() if server is not None and server.exists else None

    def tick(self) -> int:
        """Count any unexpected advancement during listener or actor cleanup."""
        self.ticks += 1
        return self.ticks


@dataclass
class SensorClient:
    """Use one retained client stream and a guarded non-ticking destroy response."""

    world: SensorWorld = field(default_factory=SensorWorld)
    batches: list[tuple[int, ...]] = field(default_factory=list)
    tick_flags: list[bool] = field(default_factory=list)

    def get_world(self) -> SensorWorld:
        """Return the currently observed episode."""
        return self.world

    def set_timeout(self, _seconds: float) -> None:
        """Accept the production client's bounded timeout refresh."""

    def apply_batch_sync(self, commands: list[int], *, do_tick: bool) -> list[SimpleNamespace]:
        """Acknowledge only the reviewed native batch commands without ticking."""
        self.batches.append(tuple(commands))
        self.tick_flags.append(do_tick)
        return [self._destroy_response(actor_id) for actor_id in commands]

    def _destroy_response(self, actor_id: int) -> SimpleNamespace:
        actors = {SENSOR_ID: self.world.server, UNRELATED_ID: self.world.sentinel}
        server = actors.get(actor_id)
        deleted = server is not None and server.delete("batch_destroy")
        error = "" if deleted else "unable to destroy actor: not found"
        return SimpleNamespace(actor_id=actor_id, error=error)

    def replace_world(self) -> None:
        """Reuse the numeric sensor ID only in a distinct new episode."""
        self.world = SensorWorld(id=self.world.id + 1)


@dataclass
class SensorCase:
    """Keep the actual adapter/facade and original listener handle observable."""

    client: SensorClient
    adapter: PythonCarlaAdapter
    api: CarlaScriptApi
    ownership: RunOwnership
    origin: SensorWorld

    @property
    def original(self) -> SensorHandle:
        """Return the handle that received the original native Listen call."""
        return self.origin.server.handles[0]


def configure_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace only optional CARLA command construction, not cleanup orchestration."""
    native = SimpleNamespace(command=SimpleNamespace(DestroyActor=lambda actor_id: actor_id))
    monkeypatch.setattr(experiment_replay, "import_module", lambda _name: native)


def build_case(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SensorCase:
    """Subscribe through the real facade to an inherited server sensor."""
    configure_batch(monkeypatch)
    client = SensorClient()
    adapter = PythonCarlaAdapter()
    adapter._connected_client = cast("CarlaClient", client)  # noqa: SLF001
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    ownership.clear()
    api = CarlaScriptApi(adapter, RunSnapshots(), ownership=ownership)
    return SensorCase(client, adapter, api, ownership, client.world)


def subscribe_case(
    case: SensorCase, monkeypatch: pytest.MonkeyPatch, *, created: bool = False
) -> None:
    """Subscribe an inherited or already-created sensor using actual public methods."""
    if created:
        sensor = case.origin.server.handle()
        monkeypatch.setattr(adapter_module, "_configured_blueprint", Mock())
        monkeypatch.setattr(adapter_module, "_parent_actor", Mock())
        monkeypatch.setattr(adapter_module, "_spawn_sensor", Mock(return_value=sensor))
        case.adapter.attach_camera(
            request=CameraAttachRequest(
                "sensor.camera.rgb",
                Transform(Location(0.0, 0.0, 1.0), Rotation(0.0, 0.0, 0.0)),
                {},
                None,
            )
        )
    assert case.api.subscribe_sensor(SENSOR_ID).get("ok") is not False


def assert_unowned(case: SensorCase) -> None:
    """Require no inherited actor adoption or unresolved native creation intent."""
    assert case.ownership.actor_ids() == ()
    assert case.ownership.pending_creations() == 0


def assert_no_tick(case: SensorCase) -> None:
    """Cleanup must not advance either the old or replacement episode."""
    assert case.origin.ticks == 0
    assert case.client.world.ticks == 0
    assert True not in case.client.tick_flags


def assert_no_deletion(case: SensorCase) -> None:
    """Both old and reused numeric IDs remain present after refused cleanup."""
    assert case.origin.server.exists
    assert case.client.world.server.exists
    assert case.client.batches == []


def destroy_result(case: SensorCase) -> JsonObject:
    """Return the explicit per-ID result emitted by the actual facade."""
    payload = case.api.destroy_actors([SENSOR_ID])
    return cast("list[JsonObject]", payload["results"])[0]


def run_case(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    code: str,
    *,
    stop_fails: bool = False,
    replace_at_exit: bool = False,
) -> tuple[SensorCase, dict[str, object]]:
    """Execute actual finite scripts with real listener/facade cleanup orchestration."""
    case = build_case(monkeypatch, tmp_path)

    def create_adapter(**kwargs: object) -> PythonCarlaAdapter:
        adapter = PythonCarlaAdapter(
            host=cast("str", kwargs["host"]),
            port=cast("int", kwargs["port"]),
            timeout=cast("float", kwargs["timeout"]),
            settings_journal=cast("RunSettings", kwargs["settings_journal"]),
            rpc_timeout_policy=cast("RpcTimeoutPolicy", kwargs["rpc_timeout_policy"]),
        )
        adapter._connected_client = cast("CarlaClient", case.client)  # noqa: SLF001
        case.adapter = adapter
        return adapter

    if stop_fails:
        case.origin.find(SENSOR_ID)
        case.origin.server.handles[0].fail_always = True
        monkeypatch.setattr(case.origin, "find", lambda _actor_id: case.original)
    if replace_at_exit:
        native_run = script_runner.runpy.run_path

        def run_and_replace(path_name: str, init_globals: dict[str, object]) -> dict[str, object]:
            result = native_run(path_name, init_globals=init_globals)
            case.client.replace_world()
            return result

        monkeypatch.setattr(script_runner.runpy, "run_path", run_and_replace)
    monkeypatch.setattr(script_runner, "PythonCarlaAdapter", create_adapter)
    script = tmp_path / "inherited-listener.py"
    script.write_text(code, encoding="utf-8")
    outcome = script_runner.run_script_file(
        script_path=script,
        host="isolated-fake",
        port=22157,
        timeout_seconds=10.0,
        ownership_path=tmp_path / "owned-actors.json",
    )
    return case, outcome


@pytest.mark.parametrize("fails", [False, True])
def test_finite_destroy_stops_inherited_listener_before_delete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, fails: bool
) -> None:
    """Successful and failing scripts never stop a deleted sensor during final cleanup."""
    code = "api.subscribe_sensor(7)\nresult = api.destroy_actors([7])\n"
    if fails:
        code += "assert False, 'script failed'\n"

    case, outcome = run_case(monkeypatch, tmp_path, code)

    assert_script_completion(outcome, fails=fails)
    assert "sensor_failures" not in cast("dict[str, object]", outcome["cleanup"])
    assert_original_stopped_before_delete(case)
    assert_unowned(case)
    assert_no_tick(case)


def assert_script_completion(outcome: dict[str, object], *, fails: bool) -> None:
    """Preserve the script's own success or failure rather than a cleanup artifact."""
    assert outcome["ok"] is not fails
    assert outcome["error_type"] == ("AssertionError" if fails else None)


def assert_original_stopped_before_delete(case: SensorCase) -> None:
    """Require one exact target command after the original listener's Stop acknowledgment."""
    assert case.origin.server.events == ["listen:lookup-0", "stop:lookup-0", "batch_destroy"]
    assert case.client.batches == [(SENSOR_ID,)]
    assert case.origin.sentinel.exists


def test_ignored_stop_error_cannot_finish_as_nominal_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An ignored recoverable API error still leaves final listener cleanup accountable."""
    case, outcome = run_case(
        monkeypatch,
        tmp_path,
        "api.subscribe_sensor(7)\napi.close_sensor_subscription(7)\nresult = 'ignored'\n",
        stop_fails=True,
    )

    assert outcome["ok"] is False
    assert outcome["error_type"] == "sensor_cleanup_failed"
    assert cast("dict[str, object]", outcome["cleanup"])["sensor_failures"]
    assert_no_deletion(case)
    assert_unowned(case)


def test_persistent_stop_failure_retains_subscription_and_refuses_destruction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Neither a fresh lookup nor a frozen queue permits deletion after Stop failure."""
    case = build_case(monkeypatch, tmp_path)
    subscribe_case(case, monkeypatch)
    case.original.fail_always = True
    subscription = case.adapter._sensor_subscriptions[SENSOR_ID]  # noqa: SLF001

    for _ in range(STOP_ATTEMPTS):
        assert destroy_result(case)["destroyed"] is False
        assert case.adapter._sensor_subscriptions[SENSOR_ID] is subscription  # noqa: SLF001
    assert case.original.stops == STOP_ATTEMPTS
    assert_no_deletion(case)
    assert_no_tick(case)


def test_transient_stop_failure_retries_original_handle_then_becomes_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Retries use the original listener handle until an actual Stop call returns."""
    case = build_case(monkeypatch, tmp_path)
    subscribe_case(case, monkeypatch)
    case.original.stop_failures = 1

    assert case.api.close_sensor_subscription(SENSOR_ID)["ok"] is False
    assert case.api.close_sensor_subscription(SENSOR_ID)["closed"] is True
    assert case.api.close_sensor_subscription(SENSOR_ID)["closed"] is True

    assert_listener_retry_acknowledged(case)
    assert_no_tick(case)


def assert_listener_retry_acknowledged(case: SensorCase) -> None:
    """Require a second original-handle Stop without destroying its actor."""
    assert case.original.stops == STOP_ATTEMPTS
    assert case.original.listening is False
    assert case.origin.server.exists


@pytest.mark.parametrize("created", [False, True])
@pytest.mark.parametrize("operation", ["close", "close_all", "destroy", "detach"])
def test_episode_replacement_refuses_old_listener_and_reused_id_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    created: bool,
    operation: str,
) -> None:
    """Every cleanup entrypoint verifies the listening episode before touching a handle."""
    case = build_case(monkeypatch, tmp_path)
    subscribe_case(case, monkeypatch, created=created)
    case.client.replace_world()

    assert_refused_cleanup(case, operation)

    assert case.original.stops == 0
    assert case.adapter._sensor_subscriptions.get(SENSOR_ID) is not None  # noqa: SLF001
    assert_no_deletion(case)
    assert_no_tick(case)


def assert_refused_cleanup(case: SensorCase, operation: str) -> None:
    """Treat per-ID failure and recoverable wrapper errors as their existing contracts."""
    if operation == "close_all":
        with pytest.raises(CarlaAdapterError, match="episode"):
            case.adapter.close_sensor_subscriptions()
        return
    operations = {
        "close": lambda: case.api.close_sensor_subscription(SENSOR_ID),
        "destroy": lambda: destroy_result(case),
        "detach": lambda: case.api.detach_sensor(SENSOR_ID),
    }
    result = operations[operation]()
    assert result.get("ok") is False or result.get("destroyed") is False
    assert "episode" in str(result["error"]).lower()


@pytest.mark.parametrize("created", [False, True])
def test_closed_listener_retains_origin_for_later_explicit_destroy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    created: bool,
) -> None:
    """Closing a listener must not authorize deletion of a reused ID in another world."""
    case = build_case(monkeypatch, tmp_path)
    subscribe_case(case, monkeypatch, created=created)
    case.api.close_sensor_subscription(SENSOR_ID)
    case.client.replace_world()

    assert_refused_cleanup(case, "destroy")

    assert case.original.stops == 1
    assert_no_deletion(case)


@pytest.mark.parametrize("operation", ["destroy", "detach"])
def test_episode_change_during_original_stop_forbids_later_deletion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: str,
) -> None:
    """A successful old-world Stop cannot authorize a later replacement-world delete."""
    case = build_case(monkeypatch, tmp_path)
    subscribe_case(case, monkeypatch)
    case.original.during_stop = case.client.replace_world

    assert_refused_cleanup(case, operation)

    assert case.original.stops == 1
    assert_no_deletion(case)
    assert_no_tick(case)


def test_failed_stop_retry_checks_origin_before_touching_old_handle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A retry after replacement remains an episode error, not a silent close success."""
    case = build_case(monkeypatch, tmp_path)
    subscribe_case(case, monkeypatch)
    case.original.stop_failures = 1
    assert case.api.close_sensor_subscription(SENSOR_ID)["ok"] is False
    case.client.replace_world()

    assert_refused_cleanup(case, "close")

    assert case.original.stops == 1
    assert_no_deletion(case)


def test_repeated_wrapper_closes_preserve_failed_original_subscription(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed wrapper close retains its record until a native Stop is acknowledged."""
    case = build_case(monkeypatch, tmp_path)
    subscribe_case(case, monkeypatch)
    case.original.fail_always = True
    subscription = case.adapter._sensor_subscriptions[SENSOR_ID]  # noqa: SLF001

    for _ in range(STOP_ATTEMPTS):
        assert case.api.close_sensor_subscription(SENSOR_ID)["ok"] is False
        assert case.adapter._sensor_subscriptions[SENSOR_ID] is subscription  # noqa: SLF001
    assert case.original.stops == STOP_ATTEMPTS
    assert_no_deletion(case)


def test_finite_final_close_refuses_episode_replacement_before_native_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Simulate external replacement after real script execution but before its final close."""
    case, outcome = run_case(
        monkeypatch,
        tmp_path,
        "api.subscribe_sensor(7)\nresult = 'done'\n",
        replace_at_exit=True,
    )

    assert outcome["ok"] is False
    assert outcome["error_type"] == "sensor_cleanup_failed"
    assert case.original.stops == 0
    assert_no_deletion(case)
    assert_unowned(case)


def test_inherited_cached_handle_cannot_capture_after_episode_replacement(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The new original-handle cache must not permit listening to a replaced-world sensor."""
    case = build_case(monkeypatch, tmp_path)
    subscribe_case(case, monkeypatch)
    case.client.replace_world()
    capture = Mock(return_value=SimpleNamespace(frame=1, save_to_disk=Mock()))
    monkeypatch.setattr(adapter_module, "_capture_image", capture)

    with pytest.raises(CarlaAdapterError, match="episode"):
        case.adapter.capture_sensor_frame(sensor_id=SENSOR_ID, output_path=tmp_path / "frame.png")

    capture.assert_not_called()
    assert case.original.stops == 0
    assert_no_deletion(case)
