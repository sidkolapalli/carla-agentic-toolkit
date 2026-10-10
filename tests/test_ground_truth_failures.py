"""Ground-truth boundary failures remain explicit without fallback measurements."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Never, cast

import pytest

from carla_agentic_toolkit import experiment_environment
from tests.ground_truth_helpers import carla_module, make_case

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.fixture(autouse=True)
def _native_enums(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(experiment_environment, "import_module", lambda _name: carla_module())


def _assert_error(payload: dict[str, object]) -> None:
    assert payload["ok"] is False
    assert payload["message"]


@pytest.mark.parametrize("boundary", ["snapshot", "vertices", "level", "actor_lookup"])
@pytest.mark.parametrize("failure", [AttributeError, RuntimeError, TypeError, ValueError])
def test_native_query_failures_are_structured(
    monkeypatch: pytest.MonkeyPatch, boundary: str, failure: type[Exception]
) -> None:
    """Native read failures cannot produce guessed geometry or a second reading."""
    case = make_case()

    def fail(*_args: object) -> Never:
        message = "native read failed"
        raise failure(message)

    targets: dict[str, tuple[object, str, Callable[[], dict[str, object]]]] = {
        "snapshot": (case.world, "get_snapshot", lambda: case.api.get_actor_bounding_boxes([17])),
        "vertices": (
            case.world.actors[17].bounding_box,
            "get_world_vertices",
            lambda: case.api.get_actor_bounding_boxes([17]),
        ),
        "level": (case.world, "get_level_bbs", case.api.get_level_bounding_boxes),
        "actor_lookup": (case.world, "get_actors", lambda: case.api.get_camera_intrinsics(19)),
    }
    target, method, query = targets[boundary]
    monkeypatch.setattr(target, method, fail)
    payload = query()
    _assert_error(payload)
    assert "native read failed" in str(payload["message"])
    assert case.world.actors[17].live_transform_calls == 0


@pytest.mark.parametrize("frame", [False, -1, 1.5, math.nan])
def test_invalid_snapshot_frame_is_not_accepted(frame: object) -> None:
    """Every successful actor-box result carries a real integer publication frame."""
    case = make_case()
    cast("Any", case.world.snapshot).frame = frame
    payload = case.api.get_actor_bounding_boxes([17])
    _assert_error(payload)
    assert case.world.actor_queries == []


def test_actor_missing_native_handle_does_not_hide_absence() -> None:
    """A snapshot entry alone does not invent the actor's native local box."""
    case = make_case()
    case.world.actors.pop(17)
    payload = case.api.get_actor_bounding_boxes([17])
    _assert_error(payload)
    assert "not found" in str(payload["message"])
    assert case.world.snapshot_calls == 1


def test_nonfinite_native_level_box_is_not_spatially_selected() -> None:
    """Invalid native positions cannot disappear silently through distance filtering."""
    case = make_case()
    case.world.boxes[0].location.x = math.nan
    payload = case.api.get_level_bounding_boxes(origin={"x": 0, "y": 0, "z": 0})
    _assert_error(payload)
    assert "finite" in str(payload["message"])


def test_unrepresentable_distance_is_a_structured_input_error() -> None:
    """A mathematically huge Python integer is not a representable CARLA distance."""
    case = make_case()
    payload = case.api.get_level_bounding_boxes(
        origin={"x": 0, "y": 0, "z": 0}, max_distance=10**1000
    )
    _assert_error(payload)
    assert case.world.level_queries == []


def test_finite_fov_with_unrepresentable_focal_length_is_an_error() -> None:
    """Projection arithmetic must not leak division-by-zero or emit infinity."""
    case = make_case()
    case.world.actors[19].attributes["fov"] = "5e-324"
    payload = case.api.get_camera_intrinsics(19)
    _assert_error(payload)
    assert case.world.actors[19].listen_calls == 0
