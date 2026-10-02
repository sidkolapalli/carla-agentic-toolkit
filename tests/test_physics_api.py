"""Capability-driven actor and vehicle physics behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import experiment_physics, experiment_replay
from carla_agentic_toolkit.errors import UnsupportedFeatureError
from carla_agentic_toolkit.models import Location
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.carla_protocols import CarlaWorld

ACTOR_ID = 51
VECTOR = {"x": 1.0, "y": 2.0, "z": 3.0}
ORIGINAL_MASS = 1500.0
UPDATED_MASS = 1700.0


@dataclass
class PhysicsAdapter:
    """Facade-level physics adapter fake."""

    calls: list[tuple[object, ...]] = field(default_factory=list)

    def configure_actor_physics(
        self,
        *,
        actor_id: int,
        simulate_physics: bool | None,
        gravity: bool | None,
    ) -> dict[str, object]:
        """Record actor physics configuration."""
        self.calls.append(("configure", actor_id, simulate_physics, gravity))
        return {
            "actor_id": actor_id,
            "simulate_physics": simulate_physics,
            "gravity": gravity,
        }

    def apply_actor_physics(
        self,
        *,
        actor_id: int,
        action: str,
        vector: Location,
    ) -> dict[str, object]:
        """Record one vector physics operation."""
        self.calls.append(("apply", actor_id, action, vector))
        return {"actor_id": actor_id, "action": action, "vector": vector.to_dict()}

    def get_vehicle_physics(self, actor_id: int) -> dict[str, object]:
        """Return representative vehicle physics."""
        self.calls.append(("get", actor_id))
        return {"actor_id": actor_id, "mass": 1500.0}

    def update_vehicle_physics(
        self,
        *,
        actor_id: int,
        changes: dict[str, object],
    ) -> dict[str, object]:
        """Record a partial vehicle physics update."""
        self.calls.append(("update", actor_id, changes))
        return {"actor_id": actor_id, **changes}


def test_facade_exposes_composable_physics_operations() -> None:
    """The script API should preserve JSON contracts across physics operations."""
    adapter = PhysicsAdapter()
    api = CarlaScriptApi(cast("PythonCarlaAdapter", adapter), RunSnapshots())

    configured = api.configure_actor_physics(
        ACTOR_ID,
        simulate_physics=True,
        gravity=False,
    )
    applied = api.apply_actor_physics(ACTOR_ID, "impulse", VECTOR)
    inspected = api.get_vehicle_physics(ACTOR_ID)
    updated = api.update_vehicle_physics(ACTOR_ID, {"mass": 1600.0})
    methods = cast("dict[str, object]", api.describe_api()["methods"])

    assert {
        "configured": configured,
        "applied": applied,
        "inspected": inspected,
        "updated": updated,
        "discoverable": {
            "configure_actor_physics",
            "apply_actor_physics",
            "get_vehicle_physics",
            "update_vehicle_physics",
        }
        <= methods.keys(),
    } == {
        "configured": {"actor_id": ACTOR_ID, "simulate_physics": True, "gravity": False},
        "applied": {"actor_id": ACTOR_ID, "action": "impulse", "vector": VECTOR},
        "inspected": {"actor_id": ACTOR_ID, "mass": 1500.0},
        "updated": {"actor_id": ACTOR_ID, "mass": 1600.0},
        "discoverable": True,
    }


@dataclass
class PhysicsActor:
    """Dynamic CARLA actor fake with optional official methods."""

    methods: dict[str, Callable[..., object]]

    def __getattr__(self, name: str) -> object:
        """Resolve only explicitly supplied dynamic CARLA methods."""
        try:
            return self.methods[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


@dataclass
class Actors:
    """Actor collection fake."""

    actor: object

    def find(self, actor_id: int) -> object | None:
        """Return the expected actor."""
        assert actor_id == ACTOR_ID
        return self.actor


@dataclass
class World:
    """World fake exposing one actor collection."""

    actor: object

    def get_actors(self, _actor_ids: list[int] | None = None) -> Actors:
        """Return the actor collection."""
        return Actors(self.actor)


@dataclass
class Vector3D:
    """CARLA vector/location factory fake."""

    x: float
    y: float
    z: float


def test_facade_returns_stable_unsupported_feature_error() -> None:
    """Runtime capability gaps should stay inspectable inside the script result."""

    class MissingPhysicsAdapter:
        """Adapter fake that lacks one runtime capability."""

        def configure_actor_physics(self, **_kwargs: object) -> dict[str, object]:
            """Raise the capability-specific error."""
            message = "Connected CARLA actor does not support set_simulate_physics."
            raise UnsupportedFeatureError(message)

    api = CarlaScriptApi(
        cast("PythonCarlaAdapter", MissingPhysicsAdapter()),
        RunSnapshots(),
    )

    result = api.configure_actor_physics(ACTOR_ID, simulate_physics=True)

    assert result["ok"] is False
    assert result["error_type"] == "unsupported_feature"
    assert result["retryable"] is False


@pytest.mark.parametrize(
    ("action", "method_name"),
    [
        ("impulse", "add_impulse"),
        ("force", "add_force"),
        ("torque", "add_torque"),
        ("target_angular_velocity", "set_target_angular_velocity"),
    ],
)
def test_runtime_calls_each_official_vector_physics_method(
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    method_name: str,
) -> None:
    """Every exposed action should map to its capability-probed CARLA method."""
    values: list[object] = []
    actor = PhysicsActor({method_name: values.append})
    monkeypatch.setattr(
        experiment_physics,
        "import_module",
        lambda _name: type("Carla", (), {"Vector3D": Vector3D}),
    )

    result = experiment_physics.apply_actor_physics(
        World(actor),
        actor_id=ACTOR_ID,
        action=action,
        vector=Location(x=1.0, y=2.0, z=3.0),
    )

    assert result["action"] == action
    assert isinstance(values[0], Vector3D)


def test_runtime_calls_official_actor_physics_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime helpers should call the methods bound by CARLA 0.9.16."""
    calls: list[tuple[str, object]] = []
    actor = PhysicsActor(
        {
            "set_simulate_physics": lambda value: calls.append(("simulate", value)),
            "set_enable_gravity": lambda value: calls.append(("gravity", value)),
            "add_impulse": lambda value: calls.append(("impulse", value)),
        }
    )
    monkeypatch.setattr(
        experiment_physics,
        "import_module",
        lambda _name: type("Carla", (), {"Vector3D": Vector3D}),
    )

    configured = experiment_physics.configure_actor_physics(
        World(actor),
        actor_id=ACTOR_ID,
        simulate_physics=True,
        gravity=False,
    )
    applied = experiment_physics.apply_actor_physics(
        World(actor),
        actor_id=ACTOR_ID,
        action="impulse",
        vector=Location(x=1.0, y=2.0, z=3.0),
    )

    assert {
        "configured": configured,
        "applied": applied,
        "flag_calls": calls[:2],
        "impulse_method": calls[2][0],
        "impulse_vector": isinstance(calls[2][1], Vector3D),
    } == {
        "configured": {"actor_id": ACTOR_ID, "simulate_physics": True, "gravity": False},
        "applied": {"actor_id": ACTOR_ID, "action": "impulse", "vector": VECTOR},
        "flag_calls": [("simulate", True), ("gravity", False)],
        "impulse_method": "impulse",
        "impulse_vector": True,
    }


def test_capability_report_probes_runtime_classes_not_version_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Discovery should reflect methods present in the imported CARLA build."""

    class ActorType:
        def add_impulse(self, _value: object) -> None:
            """Represent one available actor capability."""

    class VehicleType:
        def get_physics_control(self) -> None:
            """Represent one available vehicle capability."""

    class CapabilityTarget:
        def get_map(self) -> CapabilityTarget:
            """Return a map-like capability target."""
            return self

    monkeypatch.setattr(
        experiment_replay,
        "import_module",
        lambda _name: type("Carla", (), {"Actor": ActorType, "Vehicle": VehicleType}),
    )

    report = experiment_replay.capability_report(
        CapabilityTarget(), cast("CarlaWorld", CapabilityTarget())
    )
    actor_physics = cast("dict[str, bool]", report["actor_physics"])
    vehicle_physics = cast("dict[str, bool]", report["vehicle_physics"])

    assert {
        "impulse": actor_physics["add_impulse"],
        "force": actor_physics["add_force"],
        "get_control": vehicle_physics["get_physics_control"],
    } == {"impulse": True, "force": False, "get_control": True}


def test_runtime_reports_missing_physics_capability() -> None:
    """Absent methods should be feature diagnostics, not version assumptions."""
    with pytest.raises(UnsupportedFeatureError, match="set_simulate_physics"):
        experiment_physics.configure_actor_physics(
            World(PhysicsActor({})),
            actor_id=ACTOR_ID,
            simulate_physics=True,
            gravity=None,
        )


@dataclass
class PhysicsControl:
    """Common VehiclePhysicsControl field fake."""

    mass: float = ORIGINAL_MASS
    drag_coefficient: float = 0.3
    max_rpm: float = 5000.0
    moi: float = 1.0
    use_gear_autobox: bool = True
    use_sweep_wheel_collision: bool = False
    center_of_mass: Vector3D = field(default_factory=lambda: Vector3D(0.0, 0.0, 0.0))


def test_vehicle_physics_read_and_partial_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a fixed common field set should cross the JSON boundary."""
    control = PhysicsControl()
    applied: list[PhysicsControl] = []
    actor = PhysicsActor(
        {
            "get_physics_control": lambda: control,
            "apply_physics_control": applied.append,
        }
    )
    monkeypatch.setattr(
        experiment_physics,
        "import_module",
        lambda _name: type("Carla", (), {"Location": Vector3D}),
    )

    before = experiment_physics.get_vehicle_physics(World(actor), ACTOR_ID)
    after = experiment_physics.update_vehicle_physics(
        World(actor),
        actor_id=ACTOR_ID,
        changes={"mass": UPDATED_MASS, "center_of_mass": VECTOR},
    )

    assert before["mass"] == ORIGINAL_MASS
    assert after["mass"] == UPDATED_MASS
    assert after["center_of_mass"] == VECTOR
    assert applied == [control]
