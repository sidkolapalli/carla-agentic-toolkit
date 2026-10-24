"""Native blueprint input errors stay structured without hiding successful creates."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import adapter_objects
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from carla_agentic_toolkit.tool_inputs import parse_spawn_requests, sensor_blueprint, zero_transform

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from carla_agentic_toolkit.models import JsonObject

BLUEPRINT_ID = "sensor.camera.rgb"
BAD_BLUEPRINT_ID = "sensor.camera.unknown"
ATTRIBUTE_ID = "image_size_unknown"
WORLD_ID = 17
ACTOR_IDS = (101, 102)


@dataclass
class BlueprintCase:
    """Keep the adapter and ownership hooks real at the native input boundary."""

    world: Mock
    library: Mock
    blueprint: Mock
    ownership: RunOwnership
    snapshots: RunSnapshots
    api: CarlaScriptApi


@pytest.fixture
def blueprint_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> BlueprintCase:
    """Replace native dependencies, not the implementation under test."""
    world = Mock(id=WORLD_ID)
    world.get_settings.return_value = SimpleNamespace(no_rendering_mode=False)
    library = Mock()
    blueprint = Mock(id=BLUEPRINT_ID)
    world.get_blueprint_library.return_value = library
    library.find.return_value = blueprint
    world.spawn_actor.side_effect = [SimpleNamespace(id=actor_id) for actor_id in ACTOR_IDS]
    client = cast("CarlaClient", SimpleNamespace(get_world=lambda: world))
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", lambda: client)
    monkeypatch.setattr(adapter_objects, "carla_transform", lambda value: value)
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    snapshots = RunSnapshots()
    api = CarlaScriptApi(adapter, snapshots, ownership=ownership)
    return BlueprintCase(world, library, blueprint, ownership, snapshots, api)


def _request(
    blueprint_id: str = BLUEPRINT_ID, attributes: dict[str, str] | None = None
) -> JsonObject:
    return {
        "blueprint_id": blueprint_id,
        "attributes": attributes or {},
        "transform": zero_transform(),
    }


def _invoke(case: BlueprintCase, operation: str, attributes: dict[str, str]) -> JsonObject:
    if operation == "batch":
        return case.api.spawn_actor_batch([_request(attributes=attributes)])
    if operation == "camera":
        return case.api.attach_camera(_request(attributes=attributes))
    return case.api.attach_sensor(BLUEPRINT_ID, None, zero_transform(), attributes)


def _results(payload: JsonObject) -> list[JsonObject]:
    return cast("list[JsonObject]", payload["results"])


def _failure_message(result: JsonObject, operation: str) -> str:
    if operation == "batch":
        return str(_results(result)[0]["error"])
    assert result["error_type"] == f"attach_{operation}_failed"
    return str(result["message"])


def _assert_no_native_intent(case: BlueprintCase) -> None:
    assert case.world.spawn_actor.call_count == 0
    assert case.ownership.pending_creations() == 0


def _assert_input_failure(
    case: BlueprintCase, result: JsonObject, operation: str, context: str
) -> None:
    assert result["ok"] is False
    message = _failure_message(result, operation)
    assert BLUEPRINT_ID in message
    assert context in message
    _assert_no_native_intent(case)


def _assert_batch_identity(results: list[JsonObject]) -> None:
    assert [(item["request_index"], item["actor_id"]) for item in results] == [
        (0, ACTOR_IDS[0]),
        (1, None),
        (2, ACTOR_IDS[1]),
    ]


def _assert_batch_ownership(case: BlueprintCase, result: JsonObject) -> None:
    assert case.ownership.actor_ids() == ACTOR_IDS
    assert case.ownership.pending_creations() == 0
    assert case.snapshots.read_snapshot("carla-snapshot://actors") == result


@pytest.mark.parametrize("operation", ["batch", "camera", "sensor"])
@pytest.mark.parametrize("error_type", [IndexError, KeyError, ValueError, RuntimeError])
def test_blueprint_lookup_errors_are_contextual_structured_failures(
    blueprint_case: BlueprintCase, operation: str, error_type: type[Exception]
) -> None:
    """CARLA lookup exceptions cannot escape or create a native mutation intent."""
    blueprint_case.library.find.side_effect = error_type("lookup rejected")
    result = _invoke(blueprint_case, operation, {})
    _assert_input_failure(blueprint_case, result, operation, "lookup rejected")


@pytest.mark.parametrize("operation", ["batch", "camera", "sensor"])
@pytest.mark.parametrize("error_type", [IndexError, KeyError, ValueError, RuntimeError])
def test_blueprint_attribute_errors_name_blueprint_and_attribute(
    blueprint_case: BlueprintCase, operation: str, error_type: type[Exception]
) -> None:
    """A native attribute failure is local input rejection, not an unknown spawn."""
    blueprint_case.blueprint.set_attribute.side_effect = error_type("attribute rejected")
    result = _invoke(blueprint_case, operation, {ATTRIBUTE_ID: "invalid"})
    _assert_input_failure(blueprint_case, result, operation, ATTRIBUTE_ID)


def test_unknown_sensor_kind_returns_structured_error_without_native_creation(
    blueprint_case: BlueprintCase,
) -> None:
    """A friendly-kind typo should be handled by the existing facade error policy."""
    result = blueprint_case.api.attach_sensor("rbg", None, zero_transform())
    assert result["ok"] is False
    assert result["error_type"] == "attach_sensor_failed"
    assert "rbg" in str(result["message"])
    assert blueprint_case.library.find.call_count == 0
    _assert_no_native_intent(blueprint_case)


def test_runtime_sensor_blueprint_id_remains_supported() -> None:
    """New runtime sensor IDs do not require a new friendly-kind alias."""
    assert sensor_blueprint("sensor.custom.runtime") == "sensor.custom.runtime"


@pytest.mark.parametrize("stage", ["lookup", "attribute"])
@pytest.mark.parametrize("error_type", [IndexError, KeyError, ValueError, RuntimeError])
def test_partial_batch_continues_and_retains_immediately_journaled_ids(
    blueprint_case: BlueprintCase, stage: str, error_type: type[Exception]
) -> None:
    """One invalid request cannot hide successful neighbors or leave a false intent."""
    observed_ids: list[tuple[int, ...]] = []
    bad_blueprint = Mock(id=BAD_BLUEPRINT_ID)
    rejection = error_type("request rejected")
    bad_blueprint.set_attribute.side_effect = rejection

    def find(blueprint_id: str) -> Mock:
        if blueprint_id != BAD_BLUEPRINT_ID:
            return blueprint_case.blueprint
        observed_ids.append(blueprint_case.ownership.actor_ids())
        if stage == "lookup":
            raise rejection
        return bad_blueprint

    blueprint_case.library.find.side_effect = find
    result = blueprint_case.api.spawn_actor_batch(
        [_request(), _request(BAD_BLUEPRINT_ID, {ATTRIBUTE_ID: "invalid"}), _request()]
    )
    assert result["ok"] is False
    results = _results(result)
    _assert_batch_identity(results)
    assert BAD_BLUEPRINT_ID in str(results[1]["error"])
    assert observed_ids == [(ACTOR_IDS[0],)]
    _assert_batch_ownership(blueprint_case, result)


@pytest.mark.parametrize("operation", ["batch", "camera", "sensor"])
def test_native_transform_translation_precedes_creation_intent(
    blueprint_case: BlueprintCase, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Known local translation failures cannot quarantine an RPC never issued."""
    monkeypatch.setattr(
        adapter_objects, "carla_transform", Mock(side_effect=ValueError("transform rejected"))
    )
    result = _invoke(blueprint_case, operation, {})
    assert result["ok"] is False
    error = _results(result)[0]["error"] if operation == "batch" else result["error"]
    assert error == "transform rejected"
    _assert_no_native_intent(blueprint_case)


def test_empty_native_rpc_error_marks_partial_failure_without_clearing_intent(
    blueprint_case: BlueprintCase,
) -> None:
    """An empty native error is still a failure with an uncertain mutation outcome."""
    blueprint_case.world.spawn_actor.side_effect = RuntimeError()
    result = blueprint_case.api.spawn_actor_batch([_request()])
    assert result["ok"] is False
    assert _results(result)[0]["error"] == ""
    assert blueprint_case.ownership.pending_creations() == 1


@pytest.mark.parametrize("stage", ["lookup", "attribute"])
def test_local_native_blueprint_runtime_error_preserves_non_transport_identity(
    blueprint_case: BlueprintCase, stage: str
) -> None:
    """LibCarla's local std::exception must not masquerade as a failed server RPC."""
    failure = RuntimeError("std::exception")
    if stage == "lookup":
        blueprint_case.library.find.side_effect = failure
    else:
        blueprint_case.blueprint.set_attribute.side_effect = failure
    request = parse_spawn_requests([_request(attributes={ATTRIBUTE_ID: "480"})])[0]
    with pytest.raises(CarlaAdapterError) as caught:
        adapter_objects._configured_blueprint(blueprint_case.world, request)  # noqa: SLF001
    assert type(caught.value).__name__ == "BlueprintInputError"
    assert caught.value.__cause__ is failure
    _assert_no_native_intent(blueprint_case)


def test_blueprint_catalog_rpc_failure_is_not_relabelled_as_local_input(
    blueprint_case: BlueprintCase,
) -> None:
    """The catalog RPC remains outside the local native lookup/setter boundary."""
    failure = RuntimeError("catalog connection lost")
    blueprint_case.world.get_blueprint_library.side_effect = failure
    request = parse_spawn_requests([_request()])[0]
    with pytest.raises(RuntimeError) as caught:
        adapter_objects._configured_blueprint(blueprint_case.world, request)  # noqa: SLF001
    assert caught.value is failure
    blueprint_case.library.find.assert_not_called()
    _assert_no_native_intent(blueprint_case)


def test_legacy_unknown_sensor_kind_does_not_begin_creation_intent(tmp_path: Path) -> None:
    """Legacy preflight cannot treat a known kind typo as an unknown native spawn."""
    attach = Mock()
    adapter = cast(
        "PythonCarlaAdapter",
        SimpleNamespace(get_world_identity=lambda: WORLD_ID, attach_sensor=attach),
    )
    ownership = RunOwnership(tmp_path / "owned-actors.json")
    api = CarlaScriptApi(adapter, RunSnapshots(), ownership=ownership)
    result = api.attach_sensor("rbg", None, zero_transform())
    assert result["ok"] is False
    assert result["error_type"] == "attach_sensor_failed"
    attach.assert_not_called()
    assert ownership.pending_creations() == 0
