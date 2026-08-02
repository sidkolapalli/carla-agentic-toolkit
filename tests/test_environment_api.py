"""Capability-driven environment, map-layer, and OpenDRIVE behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest

from carla_mcp import experiment_environment
from carla_mcp.errors import CarlaAdapterError, UnsupportedFeatureError
from carla_mcp.script_api import CarlaScriptApi
from carla_mcp.snapshots import RunSnapshots

if TYPE_CHECKING:
    from carla_mcp.adapter import CarlaAdapter

OBJECT_ID = 9001
VERTEX_DISTANCE = 3.0


@dataclass
class EnvironmentAdapter:
    """Facade-level environment adapter fake."""

    calls: list[tuple[object, ...]] = field(default_factory=list)

    def get_environment_objects(
        self,
        *,
        label: str,
        max_count: int,
        include_level_bounds: bool,
    ) -> dict[str, object]:
        """Return one environment object and optional semantic bound."""
        self.calls.append(("list", label, max_count, include_level_bounds))
        result: dict[str, object] = {
            "label": label,
            "objects": [{"id": OBJECT_ID}],
            "truncated": False,
        }
        if include_level_bounds:
            result["level_bounds"] = {
                "label": label,
                "bounding_boxes": [{"location": {}}],
                "truncated": False,
            }
        return result

    def enable_environment_objects(
        self,
        *,
        object_ids: tuple[int, ...],
        enabled: bool,
    ) -> dict[str, object]:
        """Record an environment-object toggle."""
        self.calls.append(("enable", object_ids, enabled))
        return {"object_ids": list(object_ids), "enabled": enabled}

    def set_map_layer(self, *, layer: str, loaded: bool) -> dict[str, object]:
        """Record one map-layer change."""
        self.calls.append(("layer", layer, loaded))
        return {"layer": layer, "loaded": loaded}

    def generate_opendrive_world(
        self,
        *,
        opendrive: str,
        parameters: dict[str, object],
        reset_settings: bool,
    ) -> dict[str, object]:
        """Record bounded OpenDRIVE generation."""
        self.calls.append(("opendrive", opendrive, parameters, reset_settings))
        return {"current_map": "OpenDriveMap", "frame": 1}


def test_facade_exposes_environment_workflows() -> None:
    """The script facade should retain JSON-compatible environment contracts."""
    adapter = EnvironmentAdapter()
    api = CarlaScriptApi(cast("CarlaAdapter", adapter), RunSnapshots())

    listed = api.get_environment_objects("Buildings", max_count=10, include_level_bounds=True)
    toggled = api.enable_environment_objects([OBJECT_ID], enabled=False)
    layer = api.set_map_layer("Props", loaded=False)
    generated = api.generate_opendrive_world(
        "<OpenDRIVE/>",
        {"vertex_distance": 3.0},
        reset_settings=True,
    )

    assert {
        "listed": listed,
        "toggled": toggled,
        "layer": layer,
        "generated": generated,
    } == {
        "listed": {
            "label": "Buildings",
            "objects": [{"id": OBJECT_ID}],
            "truncated": False,
            "level_bounds": {
                "label": "Buildings",
                "bounding_boxes": [{"location": {}}],
                "truncated": False,
            },
        },
        "toggled": {"object_ids": [OBJECT_ID], "enabled": False},
        "layer": {"layer": "Props", "loaded": False},
        "generated": {"current_map": "OpenDriveMap", "frame": 1},
    }


@dataclass
class Vector:
    """CARLA vector fake."""

    x: float
    y: float
    z: float


@dataclass
class Rotation:
    """CARLA rotation fake."""

    pitch: float
    yaw: float
    roll: float


@dataclass
class Transform:
    """CARLA transform fake."""

    location: Vector
    rotation: Rotation


@dataclass
class BoundingBox:
    """CARLA bounding-box fake."""

    location: Vector
    extent: Vector
    rotation: Rotation


@dataclass
class EnvironmentObject:
    """CARLA environment-object fake."""

    id: int = OBJECT_ID
    name: str = "Building_A"
    type: str = "Buildings"
    transform: Transform = field(
        default_factory=lambda: Transform(Vector(1.0, 2.0, 3.0), Rotation(0.0, 90.0, 0.0))
    )
    bounding_box: BoundingBox = field(
        default_factory=lambda: BoundingBox(
            Vector(1.0, 2.0, 3.0), Vector(4.0, 5.0, 6.0), Rotation(0.0, 90.0, 0.0)
        )
    )


@dataclass
class EnvironmentWorld:
    """Dynamic world fake for environment methods."""

    calls: list[tuple[object, ...]] = field(default_factory=list)

    def get_environment_objects(self, label: object) -> list[EnvironmentObject]:
        """Return one labelled object."""
        self.calls.append(("list", label))
        return [EnvironmentObject()]

    def get_level_bbs(self, label: object) -> list[BoundingBox]:
        """Return one labelled level bounding box."""
        self.calls.append(("bounds", label))
        return [EnvironmentObject().bounding_box]

    def enable_environment_objects(self, object_ids: set[int], enabled: object) -> None:
        """Record explicit object IDs."""
        assert isinstance(enabled, bool)
        self.calls.append(("enable", object_ids, enabled))

    def load_map_layer(self, layer: object) -> None:
        """Record loading one layer."""
        self.calls.append(("load", layer))

    def unload_map_layer(self, layer: object) -> None:
        """Record unloading one layer."""
        self.calls.append(("unload", layer))


def _carla_module() -> object:
    return type(
        "Carla",
        (),
        {
            "CityObjectLabel": type("CityObjectLabel", (), {"Buildings": "buildings"}),
            "MapLayer": type("MapLayer", (), {"Props": "props"}),
        },
    )


def test_runtime_queries_toggles_and_layers_by_dynamic_enum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environment operations should resolve enums from the imported runtime."""
    world = EnvironmentWorld()
    monkeypatch.setattr(experiment_environment, "import_module", lambda _name: _carla_module())

    listed = experiment_environment.get_environment_objects(
        world,
        label="Buildings",
        max_count=1,
    )
    bounds = experiment_environment.get_level_bounding_boxes(
        world,
        label="Buildings",
        max_count=1,
    )
    toggled = experiment_environment.enable_environment_objects(
        world,
        object_ids=(OBJECT_ID,),
        enabled=False,
    )
    layer = experiment_environment.set_map_layer(world, layer="Props", loaded=True)

    objects = cast("list[dict[str, object]]", listed["objects"])
    assert {
        "object_id": objects[0]["id"],
        "bound_count": len(cast("list[object]", bounds["bounding_boxes"])),
        "toggled": toggled,
        "layer": layer,
        "enabled_call": ("enable", {OBJECT_ID}, False) in world.calls,
        "layer_call": ("load", "props") in world.calls,
    } == {
        "object_id": OBJECT_ID,
        "bound_count": 1,
        "toggled": {"object_ids": [OBJECT_ID], "enabled": False},
        "layer": {"layer": "Props", "loaded": True},
        "enabled_call": True,
        "layer_call": True,
    }


def test_missing_environment_method_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing runtime methods should not be inferred from version strings."""
    monkeypatch.setattr(experiment_environment, "import_module", lambda _name: _carla_module())
    with pytest.raises(UnsupportedFeatureError, match="get_environment_objects"):
        experiment_environment.get_environment_objects(object(), label="Buildings", max_count=10)
    with pytest.raises(UnsupportedFeatureError, match="get_level_bbs"):
        experiment_environment.get_level_bounding_boxes(object(), label="Buildings", max_count=10)


@dataclass
class OpenDriveParameters:
    """OpenDRIVE generation parameter fake."""

    vertex_distance: float = 2.0


@dataclass
class OpenDriveClient:
    """OpenDRIVE client fake."""

    received: tuple[object, ...] | None = None

    def generate_opendrive_world(
        self,
        opendrive: str,
        parameters: OpenDriveParameters,
        reset_settings: object,
    ) -> dict[str, object]:
        """Record generated world inputs."""
        assert isinstance(reset_settings, bool)
        self.received = (opendrive, parameters, reset_settings)
        return {"world": True}


def test_opendrive_text_and_parameters_are_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generation should accept only bounded OpenDRIVE XML and known parameters."""
    client = OpenDriveClient()
    monkeypatch.setattr(
        experiment_environment,
        "import_module",
        lambda _name: type("Carla", (), {"OpendriveGenerationParameters": OpenDriveParameters}),
    )

    result = experiment_environment.generate_opendrive_world(
        client,
        opendrive="<OpenDRIVE/>",
        parameters={"vertex_distance": VERTEX_DISTANCE},
        reset_settings=True,
    )

    assert result == {"world": True}
    assert client.received is not None
    received_parameters = cast("OpenDriveParameters", client.received[1])
    assert received_parameters.vertex_distance == VERTEX_DISTANCE

    with pytest.raises(CarlaAdapterError, match="Unsupported OpenDRIVE"):
        experiment_environment.generate_opendrive_world(
            client,
            opendrive="<OpenDRIVE/>",
            parameters={"future_field": 1},
            reset_settings=True,
        )
