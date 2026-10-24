"""Script ground truth is spatially selected and bound to a single snapshot."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, cast

import pytest

from carla_agentic_toolkit import experiment_environment
from tests.ground_truth_helpers import carla_module, make_case

if TYPE_CHECKING:
    from tests.ground_truth_helpers import GroundTruthCase, NativeBox

SNAPSHOT_FRAME = 41


@pytest.fixture(autouse=True)
def _native_enums(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(experiment_environment, "import_module", lambda _name: carla_module())


def _boxes(payload: dict[str, object]) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", payload["bounding_boxes"])


def _assert_error(payload: dict[str, object], operation: str) -> None:
    assert payload["ok"] is False
    assert payload["error_type"] == f"{operation}_failed"
    assert payload["message"]


def test_legacy_level_query_keeps_native_order_without_an_origin() -> None:
    """Existing calls do not secretly read a spectator or invent a spatial origin."""
    case = make_case()
    payload = experiment_environment.get_level_bounding_boxes(case.world, max_count=2)
    assert [box["location"]["x"] for box in _boxes(payload)] == [30.0, 2.0]
    assert payload["truncated"] is True


@pytest.mark.parametrize(("distance", "truncated"), [(10.0, True), (5.0, False), (0.0, False)])
def test_level_boxes_filter_then_order_then_limit(distance: float, *, truncated: bool) -> None:
    """The nearest bounds survive a limit, including the exact radius boundary."""
    case = make_case()
    payload = cast("Any", case.api).get_level_bounding_boxes(
        "Buildings", max_count=2, origin={"x": 0, "y": 0, "z": 0}, max_distance=distance
    )
    expected = [2.0, 5.0] if distance else []
    _assert_spatial_result(payload, expected, truncated=truncated)
    assert {"queries": case.world.level_queries, "snapshots": case.world.snapshot_calls} == {
        "queries": ["buildings"],
        "snapshots": 0,
    }


def _assert_spatial_result(
    payload: dict[str, object], expected: list[float], *, truncated: bool
) -> None:
    assert {
        "locations": [box["location"]["x"] for box in _boxes(payload)],
        "distances": [box["distance_m"] for box in _boxes(payload)],
        "truncated": payload["truncated"],
    } == {"locations": expected, "distances": expected, "truncated": truncated}


@pytest.mark.parametrize("bad", [math.nan, math.inf, -1.0, True, "5"])
def test_invalid_level_distance_is_rejected_before_the_native_query(bad: object) -> None:
    """Reject nonfinite, negative and nonnumeric radius inputs before level enumeration."""
    case = make_case()
    payload = cast("Any", case.api).get_level_bounding_boxes(
        origin={"x": 0, "y": 0, "z": 0}, max_distance=bad
    )
    _assert_error(payload, "get_level_bounding_boxes")
    assert case.world.level_queries == []


@pytest.mark.parametrize("bad", [math.nan, math.inf, True, "0"])
def test_invalid_level_origin_is_rejected_before_the_native_query(bad: object) -> None:
    """Reject nonfinite or mistyped coordinates through the shared input parser."""
    case = make_case()
    payload = cast("Any", case.api).get_level_bounding_boxes(origin={"x": bad, "y": 0, "z": 0})
    _assert_error(payload, "get_level_bounding_boxes")
    assert case.world.level_queries == []


def test_distance_filter_requires_an_explicit_origin() -> None:
    """A distance filter must never substitute a spectator or an actor read."""
    case = make_case()
    payload = cast("Any", case.api).get_level_bounding_boxes(max_distance=10)
    _assert_error(payload, "get_level_bounding_boxes")
    assert case.world.level_queries == []


@pytest.mark.parametrize("bad", [0, 1001, True, 1.5])
def test_level_limit_validation_precedes_native_query(bad: object) -> None:
    """Invalid output limits do not issue a native inventory query."""
    case = make_case()
    payload = cast("Any", case.api).get_level_bounding_boxes(max_count=bad)
    _assert_error(payload, "get_level_bounding_boxes")
    assert case.world.level_queries == []


def test_actor_boxes_use_one_snapshot_and_native_local_box_geometry() -> None:
    """The native vertex method receives frozen transforms and complete measured boxes."""
    case = make_case()
    payload = cast("Any", case.api).get_actor_bounding_boxes([17, 18])
    boxes = _boxes(payload)
    assert {
        "frame": payload["frame"],
        "space": payload["coordinate_space"],
        "ids": [box["actor_id"] for box in boxes],
        "first_vertex": boxes[0]["vertices"][0],
        "local_offset": boxes[0]["bounding_box"]["location"],
        "local_yaw": boxes[0]["bounding_box"]["rotation"]["yaw"],
    } == {
        "frame": SNAPSHOT_FRAME,
        "space": "world",
        "ids": [17, 18],
        "first_vertex": pytest.approx({"x": 11.0, "y": 4.0, "z": -2.5}),
        "local_offset": {"x": 2.0, "y": 0.0, "z": 0.0},
        "local_yaw": 90.0,
    }
    _assert_frozen_actor_queries(case)


def _assert_frozen_actor_queries(case: GroundTruthCase) -> None:
    assert case.world.snapshot_calls == 1
    assert case.world.snapshot.found_ids == [17, 18]
    assert case.world.actor_queries == [[17], [18]]
    for actor_id in (17, 18):
        _assert_actor_transform_source(case, actor_id)


def _assert_actor_transform_source(case: GroundTruthCase, actor_id: int) -> None:
    actor = case.world.actors[actor_id]
    state = case.world.snapshot.states[actor_id]
    assert actor.live_transform_calls == 0
    assert actor.bounding_box.transforms == [state.value]
    assert state.transform_calls == 1


def test_actor_absent_from_selected_frame_has_no_live_fallback() -> None:
    """Missing snapshot state is an explicit error, not a newer actor transform."""
    case = make_case()
    case.world.snapshot.states.pop(17)
    payload = cast("Any", case.api).get_actor_bounding_boxes([17])
    _assert_error(payload, "get_actor_bounding_boxes")
    assert "snapshot frame 41" in str(payload["message"])
    assert case.world.actors[17].live_transform_calls == 0
    assert case.world.actors[17].bounding_box.transforms == []


@pytest.mark.parametrize("field", ["location", "extent", "rotation", "vertex", "transform"])
def test_actor_box_nonfinite_geometry_is_a_structured_error(field: str) -> None:
    """Neither local metadata, frozen transforms nor world corners may contain NaN."""
    case = make_case()
    box = case.world.actors[17].bounding_box
    _corrupt_geometry(case, box, field)
    payload = cast("Any", case.api).get_actor_bounding_boxes([17])
    _assert_error(payload, "get_actor_bounding_boxes")
    assert "finite" in str(payload["message"])
    assert case.world.actors[17].live_transform_calls == 0


def _corrupt_geometry(case: GroundTruthCase, box: NativeBox, field: str) -> None:
    if field == "vertex":
        box.invalid_vertex = True
    elif field == "transform":
        case.world.snapshot.states[17].value.location.x = math.nan
    else:
        setattr(getattr(box, field), "yaw" if field == "rotation" else "x", math.nan)


@pytest.mark.parametrize("bad", [[], [True], [0], [-1], ["17"], list(range(1, 1002))])
def test_actor_ids_are_bounded_and_validated_before_snapshot(bad: object) -> None:
    """Invalid IDs and oversized requests never acquire a world snapshot."""
    case = make_case()
    payload = cast("Any", case.api).get_actor_bounding_boxes(bad)
    _assert_error(payload, "get_actor_bounding_boxes")
    assert case.world.snapshot_calls == 0
    assert case.world.actor_queries == []


def test_camera_intrinsics_read_actual_attributes_without_listening_or_settings() -> None:
    """Static calibration metadata does not require frames, world settings or Listen."""
    case = make_case()
    payload = cast("Any", case.api).get_camera_intrinsics(19)
    assert payload == {
        "sensor_id": 19,
        "width": 800,
        "height": 600,
        "fov": 90.0,
        "intrinsic_matrix": payload["intrinsic_matrix"],
    }
    _assert_intrinsic_matrix(payload["intrinsic_matrix"])
    assert {
        "queries": case.world.actor_queries,
        "snapshots": case.world.snapshot_calls,
        "settings": case.world.settings_calls,
        "listeners": case.world.actors[19].listen_calls,
    } == {"queries": [[19]], "snapshots": 0, "settings": 0, "listeners": 0}


def _assert_intrinsic_matrix(matrix: list[list[float]]) -> None:
    assert matrix[0] == pytest.approx([400.0, 0.0, 400.0])
    assert matrix[1] == pytest.approx([0.0, 400.0, 300.0])
    assert matrix[2] == [0.0, 0.0, 1.0]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_size_x", "0"),
        ("image_size_y", "-1"),
        ("image_size_x", "800.5"),
        ("image_size_x", True),
        ("fov", "0"),
        ("fov", "180"),
        ("fov", "nan"),
        ("fov", "inf"),
        ("fov", "wrong"),
        ("fov", False),
    ],
)
def test_invalid_camera_attributes_do_not_produce_a_guessed_matrix(
    field: str, value: object
) -> None:
    """Camera calibration refuses malformed native attributes rather than guessing."""
    case = make_case()
    cast("dict[str, object]", case.world.actors[19].attributes)[field] = value
    payload = cast("Any", case.api).get_camera_intrinsics(19)
    _assert_error(payload, "get_camera_intrinsics")
    assert case.world.snapshot_calls == 0


@pytest.mark.parametrize("field", ["image_size_x", "image_size_y", "fov"])
def test_missing_camera_attributes_are_not_defaulted(field: str) -> None:
    """Missing native calibration fields remain errors, not CARLA defaults."""
    case = make_case()
    case.world.actors[19].attributes.pop(field)
    payload = cast("Any", case.api).get_camera_intrinsics(19)
    _assert_error(payload, "get_camera_intrinsics")


def test_intrinsics_rejects_non_camera_actor() -> None:
    """An unrelated actor is not implicitly treated as a default camera."""
    case = make_case()
    payload = cast("Any", case.api).get_camera_intrinsics(17)
    _assert_error(payload, "get_camera_intrinsics")
