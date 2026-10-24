"""CARLA argument names/order coexist with deliberate legacy facade compatibility."""

from __future__ import annotations

from inspect import signature
from typing import cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.models import Location, SensorInfo, Transform
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots
from carla_agentic_toolkit.tool_inputs import parse_camera_attach_request, zero_transform

PARENT_ID = 22
OTHER_PARENT_ID = 23
SENSOR_ID = 101
POINT = {"x": 1.0, "y": 2.0, "z": 3.0}


def _api(monkeypatch: pytest.MonkeyPatch) -> tuple[CarlaScriptApi, Mock, Mock]:
    adapter = PythonCarlaAdapter()

    def sensor(**kwargs: object) -> SensorInfo:
        return SensorInfo(
            sensor_id=SENSOR_ID,
            blueprint_id=str(kwargs["blueprint_id"]),
            parent_actor_id=cast("int | None", kwargs["parent_actor_id"]),
            attributes=cast("dict[str, str]", kwargs["attributes"]),
            transform=cast("Transform", kwargs["transform"]),
        )

    attach = Mock(side_effect=sensor)
    waypoint = Mock(return_value={"waypoint": {}})
    monkeypatch.setattr(adapter, "attach_sensor", attach)
    monkeypatch.setattr(adapter, "get_waypoint", waypoint)
    api = CarlaScriptApi(adapter, RunSnapshots())
    return api, attach, waypoint


@pytest.mark.parametrize("method", ["attach_sensor", "attach_event_sensor"])
@pytest.mark.parametrize("name", ["attach_to", "parent_id"])
def test_parent_names_translate_to_existing_adapter(
    monkeypatch: pytest.MonkeyPatch, method: str, name: str
) -> None:
    """Canonical and deprecated keyword names keep identical native parent IDs."""
    api, attach, _ = _api(monkeypatch)
    arguments: dict[str, object] = {"kind": "collision", name: PARENT_ID}
    if method == "attach_sensor":
        arguments["transform"] = zero_transform()

    result = cast("dict[str, object]", getattr(api, method)(**arguments))

    assert result["sensor_id"] == SENSOR_ID
    assert attach.call_args.kwargs["parent_actor_id"] == PARENT_ID


@pytest.mark.parametrize("method", ["attach_sensor", "attach_event_sensor"])
def test_matching_parent_aliases_are_accepted(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    """Identical explicit dual names are compatible, not silently discarded."""
    api, attach, _ = _api(monkeypatch)
    arguments: dict[str, object] = {
        "kind": "collision",
        "attach_to": PARENT_ID,
        "parent_id": PARENT_ID,
    }
    if method == "attach_sensor":
        arguments["transform"] = zero_transform()
    result = cast("dict[str, object]", getattr(api, method)(**arguments))
    assert result["sensor_id"] == SENSOR_ID
    attach.assert_called_once()


@pytest.mark.parametrize("alias", [PARENT_ID, OTHER_PARENT_ID])
def test_positional_parent_and_legacy_keyword_are_compared_before_preparation(
    monkeypatch: pytest.MonkeyPatch, alias: int
) -> None:
    """A positional canonical parent is not missed by duplicate-key normalization."""
    api, attach, _ = _api(monkeypatch)
    prepare = Mock()
    monkeypatch.setattr(api, "_prepare_owned_creation", prepare)
    if alias == PARENT_ID:
        assert (
            api.attach_sensor("collision", PARENT_ID, zero_transform(), parent_id=alias)[
                "sensor_id"
            ]
            == SENSOR_ID
        )
        attach.assert_called_once()
        return
    with pytest.raises(TypeError, match=r"attach_to|parent_id"):
        api.attach_sensor("collision", PARENT_ID, zero_transform(), parent_id=alias)
    prepare.assert_not_called()
    attach.assert_not_called()


@pytest.mark.parametrize("method", ["attach_sensor", "attach_event_sensor"])
@pytest.mark.parametrize("alias", [OTHER_PARENT_ID, None, True])
def test_conflicting_parent_aliases_fail_before_creation_preparation(
    monkeypatch: pytest.MonkeyPatch, method: str, alias: object
) -> None:
    """A duplicate or boolean parent cannot authorize ownership preparation or native calls."""
    api, attach, _ = _api(monkeypatch)
    prepare = Mock()
    monkeypatch.setattr(api, "_prepare_owned_creation", prepare)
    arguments: dict[str, object] = {"kind": "collision", "attach_to": PARENT_ID, "parent_id": alias}
    if method == "attach_sensor":
        arguments["transform"] = zero_transform()
    with pytest.raises(TypeError, match=r"attach_to|parent_id"):
        getattr(api, method)(**arguments)
    prepare.assert_not_called()
    attach.assert_not_called()


@pytest.mark.parametrize("name", ["attach_to", "parent_actor_id"])
def test_camera_payload_parent_names_keep_native_request(name: str) -> None:
    """Camera payloads use the same canonical name while retaining old JSON callers."""
    payload: dict[str, object] = {
        "blueprint_id": "sensor.camera.rgb",
        "transform": zero_transform(),
        "attributes": {},
        name: PARENT_ID,
    }
    assert parse_camera_attach_request(payload).parent_actor_id == PARENT_ID


@pytest.mark.parametrize("alias", [OTHER_PARENT_ID, None, True])
def test_camera_payload_conflicts_are_not_ignored(alias: object) -> None:
    """The current parser must reject duplicate mismatch instead of ignoring attach_to."""
    payload = {
        "blueprint_id": "sensor.camera.rgb",
        "transform": zero_transform(),
        "attributes": {},
        "attach_to": PARENT_ID,
        "parent_actor_id": alias,
    }
    with pytest.raises(TypeError, match=r"attach_to|parent_actor_id"):
        parse_camera_attach_request(payload)


def test_camera_alias_conflict_precedes_facade_creation_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON alias disagreement cannot begin a durable creation or touch native CARLA."""
    adapter = PythonCarlaAdapter()
    api = CarlaScriptApi(adapter, RunSnapshots())
    prepare, native = Mock(), Mock()
    monkeypatch.setattr(api, "_prepare_owned_creation", prepare)
    monkeypatch.setattr(adapter, "attach_camera", native)
    with pytest.raises(TypeError, match=r"attach_to|parent_actor_id"):
        api.attach_camera(
            {
                "blueprint_id": "sensor.camera.rgb",
                "transform": zero_transform(),
                "attributes": {},
                "attach_to": PARENT_ID,
                "parent_actor_id": OTHER_PARENT_ID,
            }
        )
    prepare.assert_not_called()
    native.assert_not_called()


def test_legacy_waypoint_lane_conflict_is_rejected_before_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two disagreeing lane names are not silently prioritized."""
    api, _, waypoint = _api(monkeypatch)
    with pytest.raises(TypeError, match="lane_type"):
        api.get_waypoint(POINT, "Sidewalk", lane_type="Driving")
    waypoint.assert_not_called()


@pytest.mark.parametrize(
    ("args", "kwargs", "expected_project", "expected_lane"),
    [
        ((POINT,), {}, True, "Driving"),
        ((POINT, False), {}, False, "Driving"),
        ((POINT, False, "Sidewalk"), {}, False, "Sidewalk"),
        ((POINT, "Sidewalk"), {}, True, "Sidewalk"),
        ((POINT, "Sidewalk"), {"project_to_road": False}, False, "Sidewalk"),
        ((POINT,), {"project_to_road": False, "lane_type": "Sidewalk"}, False, "Sidewalk"),
    ],
)
def test_waypoint_native_order_and_legacy_lane_position(
    monkeypatch: pytest.MonkeyPatch,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    *,
    expected_project: bool,
    expected_lane: str,
) -> None:
    """Canonical bool/enum order and unambiguous old lane-name forms call one adapter."""
    api, _, waypoint = _api(monkeypatch)
    result = api.get_waypoint(*cast("tuple", args), **cast("dict", kwargs))
    assert result == {"waypoint": {}}
    assert waypoint.call_args.kwargs == {
        "location": Location(**POINT),
        "project_to_road": expected_project,
        "lane_type": expected_lane,
    }


@pytest.mark.parametrize(
    ("method", "names"),
    [
        ("get_waypoint", ["location", "project_to_road", "lane_type"]),
        ("attach_sensor", ["kind", "attach_to", "transform", "attributes"]),
        ("attach_event_sensor", ["kind", "attach_to", "attributes"]),
    ],
)
def test_wrapped_public_signatures_show_canonical_carla_names(
    method: str, names: list[str]
) -> None:
    """Recovery/compatibility wrappers must preserve useful runtime discovery signatures."""
    api = CarlaScriptApi(PythonCarlaAdapter(), RunSnapshots())
    assert list(signature(getattr(api, method)).parameters) == names
