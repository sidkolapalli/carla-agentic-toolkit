"""Frame identity and exclusive local actuation at the managed merge boundary."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import merge_experiment
from carla_agentic_toolkit.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.merge_planner import ManeuverState, rules_decision
from tests.test_merge_fixture import Map, Waypoint

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class Actor:
    """Actor that forbids live pose reads outside a WorldSnapshot."""

    id: int
    type_id: str
    role: str
    location: object
    autopilot: list[bool] = field(default_factory=list)
    controls: list[object] = field(default_factory=list)
    callback: object | None = None

    @property
    def bounding_box(self) -> SimpleNamespace:
        """Expose static actor bounds, which are not frame-dependent kinematics."""
        return SimpleNamespace(
            extent=SimpleNamespace(x=2.4, y=0.95, z=0.7),
            location=SimpleNamespace(x=0.0, y=0.0, z=0.0),
            rotation=SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0),
        )

    def get_transform(self) -> None:
        """Reject mixing current actor state with an earlier snapshot."""
        message = "observations must use snapshot actor transforms"
        raise AssertionError(message)

    def set_autopilot(self, enabled: object, *_args: object) -> None:
        """Record any forbidden autopilot call from a managed fixture."""
        assert isinstance(enabled, bool)
        self.autopilot.append(enabled)

    def apply_control(self, control: object) -> None:
        """Record the one local controller's actuator outputs."""
        self.controls.append(control)

    def listen(self, callback: object) -> None:
        """Retain a sensor listener without inventing nonexistent events."""
        self.callback = callback

    def stop(self) -> None:
        """Close sensor delivery."""
        self.callback = None


@dataclass
class Blueprint:
    """Blueprint with role identity recorded before spawn."""

    id: str
    attributes: dict[str, str] = field(default_factory=dict)

    def set_attribute(self, name: str, value: str) -> None:
        """Record reviewed spawn attributes."""
        self.attributes[name] = value

    def has_attribute(self, _name: str) -> bool:
        """Permit the reviewed role and sensor interval fields."""
        return True


@dataclass
class World:
    """World that records spawn ownership and never ticks from the backend."""

    actors: list[Actor] = field(default_factory=list)

    def get_blueprint_library(self) -> SimpleNamespace:
        """Return the fixed fixture blueprint constructors."""
        return SimpleNamespace(find=Blueprint)

    def spawn_actor(self, blueprint: Blueprint, transform: object, **_kwargs: object) -> Actor:
        """Require the durable run identity before actor creation."""
        role = blueprint.attributes["role_name"]
        actor = Actor(
            len(self.actors) + 1, blueprint.id, role, getattr(transform, "location", None)
        )
        self.actors.append(actor)
        return actor


@dataclass
class Session:
    """Minimal owner contract; backend cannot acquire timing or a second controller."""

    spec: ExperimentSpec = field(default_factory=ExperimentSpec)
    world: World = field(default_factory=World)
    map: Map = field(default_factory=lambda: Map(Waypoint()))
    run_id: str = "run-test"
    world_generation: str = "generation-test"
    owned: list[tuple[int, str, bool]] = field(default_factory=list)
    callbacks: list[Callable[[], object]] = field(default_factory=list)

    def own(self, actor: Actor, *, controller: str, protected: bool = True) -> None:
        """Record creation and controller identity independently."""
        self.owned.append((actor.id, controller, protected))

    def on_close(self, callback: Callable[[], object]) -> None:
        """Retain trailing sensor evidence callbacks for the timing owner."""
        self.callbacks.append(callback)


def _prepare(
    monkeypatch: pytest.MonkeyPatch, spec: ExperimentSpec | None = None
) -> tuple[Session, merge_experiment.MergeExperiment]:
    module = SimpleNamespace(
        Location=SimpleNamespace,
        Rotation=SimpleNamespace,
        Transform=lambda *args, **kwargs: SimpleNamespace(
            location=args[0] if args else None, **kwargs
        ),
        VehicleControl=SimpleNamespace,
    )
    monkeypatch.setattr(merge_experiment, "import_module", lambda _name: module)
    session = Session(spec=spec or ExperimentSpec())
    experiment = merge_experiment.MergeExperiment(session)
    experiment.prepare()
    return session, experiment


def _snapshot(session: Session, frame: int = 10) -> SimpleNamespace:
    def actor_snapshot(actor_id: int) -> SimpleNamespace:
        actor = session.world.actors[actor_id - 1]
        return SimpleNamespace(
            get_transform=lambda: SimpleNamespace(
                location=actor.location, rotation=SimpleNamespace(pitch=0.0, yaw=0.0, roll=0.0)
            ),
            get_velocity=lambda: SimpleNamespace(x=0.0, y=0.0, z=0.0),
        )

    return SimpleNamespace(
        frame=frame, timestamp=SimpleNamespace(elapsed_seconds=frame * 0.05), find=actor_snapshot
    )


def test_prepare_assigns_unique_controllers_without_autopilot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every spawned actor is protected, with role identity present before spawning."""
    session, _experiment = _prepare(monkeypatch)
    vehicles = session.world.actors[:2]
    assert [actor.autopilot for actor in vehicles] == [[], []]
    assert len({item[0] for item in session.owned}) == len(session.world.actors)
    assert all(actor.role.startswith("managed:run-test:") for actor in session.world.actors)


@pytest.mark.parametrize(
    ("fixture", "blueprint"),
    [
        ("town10-merge-v1", "vehicle.tesla.model3"),
        ("town10-merge-ue5-v1", "vehicle.lincoln.mkz"),
    ],
)
def test_fixture_selects_and_records_the_exact_vehicle(
    monkeypatch: pytest.MonkeyPatch, fixture: str, blueprint: str
) -> None:
    """A fixture selects one known vehicle and records it for reproducible comparisons."""
    spec = ExperimentSpec.model_validate({"fixture": fixture})
    session, experiment = _prepare(monkeypatch, spec)

    assert [actor.type_id for actor in session.world.actors[:2]] == [blueprint, blueprint]
    metadata = experiment.fixture_metadata()
    assert (metadata["fixture_version"], metadata["vehicle_blueprint"]) == (fixture, blueprint)


def test_missing_fixture_vehicle_reports_the_required_blueprint_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A release with a different catalog must fail clearly without substituting physics."""

    def missing(_blueprint: str) -> None:
        message = "std::exception"
        raise RuntimeError(message)

    world = World()
    monkeypatch.setattr(
        World, "get_blueprint_library", lambda _world: SimpleNamespace(find=missing)
    )
    experiment = merge_experiment.MergeExperiment(Session(world=world))

    with pytest.raises(UnsupportedFeatureError, match=r"town10-merge-v1.*vehicle\.tesla\.model3"):
        experiment.prepare()
    assert world.actors == []


def test_ue5_tracker_countersteers_before_overshooting_the_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Lincoln needs heading damping before its body reaches the far lane boundary."""
    spec = ExperimentSpec.model_validate({"fixture": "town10-merge-ue5-v1"})
    session, experiment = _prepare(monkeypatch, spec)
    value = experiment.observe(_snapshot(session, frame=100))
    value = replace(
        value,
        policy=replace(value.policy, lateral_m=2.5, yaw_error_degrees=10.0, speed_mps=6.0),
    )
    experiment.state = ManeuverState(phase="committed", first_frame=0, committed_frame=0)

    result = experiment.advance(value)

    controls = cast("dict[str, dict[str, float]]", result["controls"])
    settings = cast("dict[str, float]", experiment.fixture_metadata()["settings"])
    assert controls["policy"]["steer"] < 0
    assert settings["tracker_heading_gain"] == pytest.approx(1.8)
    assert _prepare(monkeypatch)[1].settings.tracker_heading_gain == pytest.approx(0.9)


def test_observation_uses_one_snapshot_and_empty_events_never_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Numerical evidence remains frame-aligned and honestly labeled as ground truth."""
    session, experiment = _prepare(monkeypatch)
    value = experiment.observe(_snapshot(session))
    payload = value.to_dict()
    assert (value.frame, value.policy.frame, value.ego.frame, payload["observation_mode"]) == (
        10,
        10,
        10,
        "range_filtered_ground_truth",
    )
    assert all(not entry["samples"] for entry in value.sensors)


def test_missing_snapshot_actor_is_an_infrastructure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing actor cannot be replaced by an unrelated live pose read."""
    session, experiment = _prepare(monkeypatch)
    snapshot = _snapshot(session)
    snapshot.find = lambda _actor_id: None
    with pytest.raises(CarlaAdapterError, match="snapshot"):
        experiment.observe(snapshot)


def test_baseline_advances_with_no_provider_or_background_tick_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduled local continuation is not an inference failure or intervention."""
    session, experiment = _prepare(monkeypatch)
    report = experiment.advance(experiment.observe(_snapshot(session)))
    intervention = cast("dict[str, object]", report["intervention"])
    assert (report["phase"], report["outcome"], intervention["fallback"]) == (
        "following",
        {"completed": False, "status": "running"},
        False,
    )
    assert all(len(actor.controls) == 1 for actor in session.world.actors[:2])


def test_observation_tracking_error_uses_current_trajectory_not_final_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Following the source lane perfectly has zero tracking error before a merge."""
    session, experiment = _prepare(monkeypatch)
    value = experiment.observe(_snapshot(session))
    assert value.to_dict()["tracking_error_m"] == 0.0


def test_observation_retains_raw_snapshot_position_and_velocity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Projected corridor coordinates supplement the original numerical measurement."""
    session, experiment = _prepare(monkeypatch)
    value = experiment.observe(_snapshot(session))
    assert (value.policy.position_m, value.policy.velocity_mps) == (
        (0.0, 0.0, 0.4),
        (0.0, 0.0, 0.0),
    )


def test_observation_retains_signed_longitudinal_speed_for_reversal_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A backward-moving vehicle has positive speed magnitude but negative lane progress."""
    session, experiment = _prepare(monkeypatch)
    snapshot = _snapshot(session)
    original_find = snapshot.find

    def reverse_actor(actor_id: int) -> SimpleNamespace:
        actor = original_find(actor_id)
        actor.get_velocity = lambda: SimpleNamespace(x=-2.0, y=0.0, z=0.0)
        return actor

    snapshot.find = reverse_actor
    result = experiment.observe(snapshot).to_dict()
    assert (result["speed_mps"], result["longitudinal_speed_mps"]) == (2.0, -2.0)


def test_pending_fallback_context_does_not_overwrite_original_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A current fallback deadline must not erase the original network request identity."""
    session, experiment = _prepare(monkeypatch)
    value = experiment.observe(_snapshot(session))
    deadline = time.monotonic() + 10.0
    original = experiment.policy_request(value, revision=1, deadline_monotonic=deadline)
    experiment.policy_request(value, revision=1, deadline_monotonic=deadline + 1.0)
    assert original is not None
    result = experiment.advance(value, rules_decision(original))
    assert result["intervention"] == {"fallback": False, "reason": None}


def test_cleanup_retains_collision_delivered_while_listener_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final callbacks must reach trailing evidence before listener storage is released."""
    session, experiment = _prepare(monkeypatch)
    experiment.observe(_snapshot(session))
    sensor = session.world.actors[2]
    callback = cast("Callable[[object], None]", sensor.callback)
    sample = SimpleNamespace(
        frame=10,
        normal_impulse=SimpleNamespace(x=2.0, y=3.0, z=4.0),
        other_actor=SimpleNamespace(id=99),
    )
    monkeypatch.setattr(sensor, "stop", lambda: callback(sample))
    report = experiment.close()
    evidence = cast("list[dict[str, object]]", report["sensors"])
    samples = cast("list[dict[str, object]]", evidence[0]["samples"])
    assert len(samples) == 1
    assert samples[0]["collision_impulse"] == {"x": 2.0, "y": 3.0, "z": 4.0}
    assert samples[0]["trailing"] is True
