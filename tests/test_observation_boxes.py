"""Native local bounds must not be confused with snapshot actor control origins."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from itertools import product
from types import SimpleNamespace
from typing import Any, cast

import pytest

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.merge_experiment import MergeExperiment
from carla_agentic_toolkit.merge_fixture import MergeCorridor, Pose
from carla_agentic_toolkit.merge_models import ActorObservation, PlannerSettings
from carla_agentic_toolkit.merge_safety import gap_evidence
from carla_agentic_toolkit.route_actors import RouteActors
from carla_agentic_toolkit.route_experiment import RouteExperiment
from carla_agentic_toolkit.route_geometry import RoutePath, RoutePoint, tracking_control
from carla_agentic_toolkit.route_models import RouteActor
from carla_agentic_toolkit.route_risk import box_separation, traffic_evidence


@dataclass
class Vector:
    """A native-shaped three-vector independent of CARLA imports."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Rotation:
    """CARLA's degree-valued pitch, yaw and roll."""

    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0


@dataclass
class Transform:
    """The owner snapshot's pose, not a mutable actor getter."""

    location: Vector = field(default_factory=Vector)
    rotation: Rotation = field(default_factory=Rotation)


def _bounds() -> SimpleNamespace:
    return SimpleNamespace(
        location=Vector(1.5, -0.4, 0.8),
        rotation=Rotation(yaw=25.0),
        extent=Vector(2.0, 0.9, 1.2),
    )


@dataclass
class Actor:
    """Static native bounds plus identity; live kinematic reads are forbidden."""

    id: int = 1
    type_id: str = "vehicle.tesla.model3"
    bounding_box: SimpleNamespace = field(default_factory=_bounds)

    def get_transform(self) -> None:
        """Detect accidentally leaving the authoritative owner snapshot."""
        message = "only the supplied snapshot may provide the actor transform"
        raise AssertionError(message)


def _rotate(vector: tuple[float, float, float], rotation: Rotation) -> tuple[float, float, float]:
    """Independently apply the documented CARLA matrix to a column vector."""
    cp, sp = math.cos(math.radians(rotation.pitch)), math.sin(math.radians(rotation.pitch))
    cy, sy = math.cos(math.radians(rotation.yaw)), math.sin(math.radians(rotation.yaw))
    cr, sr = math.cos(math.radians(rotation.roll)), math.sin(math.radians(rotation.roll))
    rows = (
        (cp * cy, cy * sp * sr - sy * cr, -cy * sp * cr - sy * sr),
        (cp * sy, sy * sp * sr + cy * cr, -sy * sp * cr + cy * sr),
        (sp, -cp * sr, cp * cr),
    )
    return cast(
        "tuple[float, float, float]",
        tuple(sum(a * b for a, b in zip(row, vector, strict=True)) for row in rows),
    )


def _world_point(local: Vector, transform: Transform) -> tuple[float, float, float]:
    rotated = _rotate((local.x, local.y, local.z), transform.rotation)
    return cast(
        "tuple[float, float, float]",
        tuple(
            a + b
            for a, b in zip(
                rotated,
                (transform.location.x, transform.location.y, transform.location.z),
                strict=True,
            )
        ),
    )


def _corners(actor: Actor, transform: Transform) -> tuple[tuple[float, float, float], ...]:
    bounds = actor.bounding_box
    vertices = []
    for signs in product((-1, 1), repeat=3):
        rotated = _rotate(
            tuple(
                sign * extent
                for sign, extent in zip(
                    signs, (bounds.extent.x, bounds.extent.y, bounds.extent.z), strict=True
                )
            ),
            bounds.rotation,
        )
        local = Vector(
            *(
                value + offset
                for value, offset in zip(
                    rotated, (bounds.location.x, bounds.location.y, bounds.location.z), strict=True
                )
            )
        )
        vertices.append(_world_point(local, transform))
    return tuple(vertices)


def _snapshot(actors: dict[str, Actor], transforms: dict[str, Transform]) -> SimpleNamespace:
    frozen = {
        actor.id: SimpleNamespace(
            get_transform=lambda pose=transforms[role]: pose,
            get_velocity=Vector,
        )
        for role, actor in actors.items()
    }
    return SimpleNamespace(
        frame=10, timestamp=SimpleNamespace(elapsed_seconds=0.5), find=frozen.get
    )


def _route(actors: dict[str, Actor]) -> RouteActors:
    route = RouteActors(object(), RoutePath((RoutePoint(0, 0), RoutePoint(100, 0))))
    route.handles.update(actors)
    return route


def _merge(actors: dict[str, Actor]) -> MergeExperiment:
    experiment = MergeExperiment(
        SimpleNamespace(
            spec=ExperimentSpec(),
            run_id="boxes",
            world_generation="world",
            initial_traffic_lights={"frame": 10, "lights": []},
        )
    )
    experiment.corridor = MergeCorridor(
        Pose(0, 0, 0, 0),
        Pose(24, 3.5, 0, 0),
        Pose(0, 3.5, 0, 0),
        1,
        -1,
        -2,
        3.5,
        "Broken",
        "Broken",
    )
    cast("dict[str, object]", vars(experiment)["_actors"]).update(
        {"policy": actors["policy"], "target": actors["ego"]}
    )
    return experiment


def _observed(backend: str, actor: Actor, transform: Transform) -> RouteActor | ActorObservation:
    actors = {"policy": actor, "ego": Actor(2)}
    snapshot = _snapshot(actors, {"policy": transform, "ego": Transform(Vector(24, 3.5))})
    if backend == "route":
        return _route(actors).observe(snapshot)["policy"]
    return _merge(actors).observe(snapshot).policy


def _box(value: RouteActor | ActorObservation) -> dict[str, Any]:
    result = asdict(value).get("box")
    assert isinstance(result, dict), "snapshot observation must record corrected box geometry"
    return result.get("geometry", result)


@pytest.mark.parametrize("backend", ["route", "merge"])
def test_observed_box_applies_local_offset_and_heading_without_moving_origin(backend: str) -> None:
    """Translation rotates with the actor while its tracker still receives the origin."""
    native = Actor()
    transform = Transform(Vector(20, 3, 0.5), Rotation(yaw=60))
    value = _observed(backend, native, transform)
    box = _box(value)
    assert box["center_m"] == pytest.approx(_world_point(native.bounding_box.location, transform))
    assert box["yaw_degrees"] == pytest.approx(85)
    _assert_origin(value, transform)


def _assert_origin(value: RouteActor | ActorObservation, transform: Transform) -> None:
    if isinstance(value, RouteActor):
        _assert_route_origin(value)
    else:
        assert value.position_m == (
            transform.location.x,
            transform.location.y,
            transform.location.z,
        )
        assert (value.longitudinal_m, value.lateral_m, value.yaw_error_degrees) == (20, 3, 60)


def _assert_route_origin(value: RouteActor) -> None:
    assert (value.x, value.y, value.z, value.yaw_degrees) == (20, 3, 0.5, 60)
    assert (value.progress_m, value.lateral_m) == (20, 3)
    baseline = RouteActor(value.actor_id, value.kind, 20, 3, 60, 0, 0, 0)
    path = _route({}).path
    assert tracking_control(path, value, target_speed_mps=0) == tracking_control(
        path, baseline, target_speed_mps=0
    )


@pytest.mark.parametrize("backend", ["route", "merge"])
@pytest.mark.parametrize(
    ("actor_rotation", "box_rotation"),
    [
        (Rotation(pitch=35, yaw=40, roll=20), Rotation(pitch=-15, yaw=60, roll=30)),
        (Rotation(roll=70), Rotation()),
        (Rotation(pitch=70), Rotation()),
        (Rotation(pitch=90), Rotation()),
    ],
)
def test_tilted_projection_encloses_all_native_corners(
    backend: str, actor_rotation: Rotation, box_rotation: Rotation
) -> None:
    """Vertical extents cannot disappear from a conservative two-dimensional bound."""
    native = Actor()
    native.bounding_box.rotation = box_rotation
    transform = Transform(Vector(20, 3, 0.5), actor_rotation)
    box = _box(_observed(backend, native, transform))
    heading = math.radians(box["yaw_degrees"])
    forward, side = (math.cos(heading), math.sin(heading)), (-math.sin(heading), math.cos(heading))
    for vertex in _corners(native, transform):
        delta = (vertex[0] - box["center_m"][0], vertex[1] - box["center_m"][1])
        assert (
            abs(sum(a * b for a, b in zip(delta, forward, strict=True)))
            <= box["length_m"] / 2 + 1e-10
        )
        assert (
            abs(sum(a * b for a, b in zip(delta, side, strict=True))) <= box["width_m"] / 2 + 1e-10
        )


@pytest.mark.parametrize("backend", ["route", "merge"])
def test_composed_heading_is_not_a_sum_of_yaws_when_tilted(backend: str) -> None:
    """A nonplanar actor rotation acts on the box's full local forward vector."""
    native = Actor()
    transform = Transform(Vector(20, 3, 0.5), Rotation(pitch=35, yaw=40, roll=20))
    native.bounding_box.rotation = Rotation(pitch=-15, yaw=60, roll=30)
    forward = _rotate(_rotate((1, 0, 0), native.bounding_box.rotation), transform.rotation)
    expected = math.degrees(math.atan2(forward[1], forward[0]))
    assert _box(_observed(backend, native, transform))["yaw_degrees"] == pytest.approx(expected)


def _corner_gap(
    first: tuple[tuple[float, float, float], ...],
    second: tuple[tuple[float, float, float], ...],
    *,
    seconds: float = 0,
    padding: float = 0,
) -> float:
    axes = ((1.0, 0.0), (0.0, 1.0))
    gaps = []
    for axis in axes:
        a = [point[0] * axis[0] + point[1] * axis[1] for point in first]
        b = [point[0] * axis[0] + (point[1] - 2 * seconds) * axis[1] for point in second]
        gaps.append(max(min(b) - max(a), min(a) - max(b)) - 2 * padding)
    return max(gaps)


def test_separation_and_predicted_overlap_consume_corrected_boxes() -> None:
    """Independently transformed corners expose the prior origin-centred clearance error."""
    first, second = Actor(), Actor(2)
    first.bounding_box = SimpleNamespace(
        location=Vector(2, 0), rotation=Rotation(yaw=90), extent=Vector(2, 1, 0.7)
    )
    second.bounding_box = SimpleNamespace(
        location=Vector(), rotation=Rotation(), extent=Vector(2, 1, 0.7)
    )
    transforms = {"policy": Transform(rotation=Rotation(yaw=90)), "ego": Transform(Vector(0, 6))}
    actors = {"policy": first, "ego": second}
    snapshot = _snapshot(actors, transforms)
    original = snapshot.find
    snapshot.find = lambda actor_id: SimpleNamespace(
        get_transform=original(actor_id).get_transform,
        get_velocity=lambda: Vector(y=-2 if actor_id == second.id else 0),
    )
    values = _route(actors).observe(snapshot)
    corners = _corners(first, transforms["policy"]), _corners(second, transforms["ego"])
    expected = _corner_gap(*corners)
    risk = traffic_evidence(values["policy"], (values["ego"],))[0]
    assert (box_separation(values["policy"], values["ego"]), risk.clearance_m) == pytest.approx(
        (expected, expected)
    )
    assert risk.predicted_overlap_seconds == pytest.approx(_corner_overlap(corners))


def _corner_overlap(corners: tuple[tuple[tuple[float, float, float], ...], ...]) -> float:
    return next(
        step / 10
        for step in range(41)
        if _corner_gap(*corners, seconds=step / 10, padding=0.2) <= 0
    )


def test_merge_bumper_gap_uses_corrected_box_projection_not_control_origin() -> None:
    """Offset and sideways boxes change bumper gaps without changing the tracker target."""
    policy, ego = Actor(), Actor(2)
    policy.bounding_box.location = Vector(2, 0)
    policy.bounding_box.rotation = Rotation(yaw=90)
    ego.bounding_box.location = Vector(-1, 0)
    ego.bounding_box.rotation = Rotation()
    actors = {"policy": policy, "ego": ego}
    transforms = {"policy": Transform(), "ego": Transform(Vector(20, 3.5))}
    value = _merge(actors).observe(_snapshot(actors, transforms))
    first, second = _corners(policy, transforms["policy"]), _corners(ego, transforms["ego"])
    expected = min(vertex[0] for vertex in second) - max(vertex[0] for vertex in first)
    assert gap_evidence(value, PlannerSettings())[0]["gap_m"] == pytest.approx(expected)
    assert value.policy.longitudinal_m == 0


@pytest.mark.parametrize("backend", ["route", "merge"])
@pytest.mark.parametrize("missing", ["location", "rotation"])
def test_native_missing_bounds_fail_instead_of_fabricating_zero_offsets(
    backend: str, missing: str
) -> None:
    """Native unavailable geometry is not silently mislabeled as a corrected measurement."""
    native = Actor()
    vars(native.bounding_box).pop(missing)
    with pytest.raises(CarlaAdapterError, match=r"bounding.box"):
        _observed(backend, native, Transform())


@pytest.mark.parametrize("backend", ["route", "merge"])
def test_fixture_metadata_records_raw_local_box_location_rotation_and_extent(backend: str) -> None:
    """Per-role static native bounds make a new trace's correction independently auditable."""
    actors = {"policy": Actor(), "ego": Actor(2)}
    if backend == "merge":
        metadata = _merge(actors).fixture_metadata()
    else:
        experiment = RouteExperiment(
            SimpleNamespace(
                initial_traffic_lights={"frame": 10, "lights": []},
                spec=ExperimentSpec.model_validate(
                    {"fixture": "town10-route-ue5-v1", "scenario": "lead_brake"}
                ),
            )
        )
        experiment.actors = _route(actors)
        experiment.path = experiment.actors.path
        metadata = experiment.fixture_metadata()
    boxes = metadata.get("actor_bounding_boxes")
    assert isinstance(boxes, dict), "fixture metadata must retain native bounding-box offsets"
    expected = {
        "location": {"x": 1.5, "y": -0.4, "z": 0.8},
        "rotation": {"pitch": 0.0, "yaw": 25.0, "roll": 0.0},
        "extent": {"x": 2.0, "y": 0.9, "z": 1.2},
    }
    roles = ("policy", "target") if backend == "merge" else tuple(actors)
    assert boxes == dict.fromkeys(roles, expected)


def test_legacy_route_actor_without_box_retains_old_approximation() -> None:
    """Historical DTOs have no evidence from which to infer a native local offset."""
    first = RouteActor(1, "vehicle", 0, 0, 0, 0, 0, 0)
    second = RouteActor(2, "vehicle", 10, 0, 0, 0, 0, 0)
    assert asdict(first).get("box") is None
    assert box_separation(first, second) == pytest.approx(5.2)


def test_legacy_merge_actor_without_box_keeps_control_pose_and_dimensions() -> None:
    """Existing offline observations remain constructible without invented geometry."""
    value = ActorObservation(1, 10, 20, 3, 0, 60, 4, 1.8, -1, (20, 3, 0.5))
    assert asdict(value).get("box") is None
    assert (value.position_m, value.longitudinal_m, value.length_m) == ((20, 3, 0.5), 20, 4)
