"""Script constructors supply JSON-compatible values without importing CARLA."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit import experiment_navigation
from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.persistent_namespace import PersistentNamespace
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.script_runner import run_script_file
from carla_agentic_toolkit.snapshots import RunSnapshots
from carla_agentic_toolkit.tool_inputs import (
    parse_camera_attach_request,
    parse_spawn_requests,
    parse_traffic_vehicle_path_request,
)

if TYPE_CHECKING:
    from pathlib import Path


ZERO_LOCATION = {"x": 0.0, "y": 0.0, "z": 0.0}
ZERO_ROTATION = {"pitch": 0.0, "yaw": 0.0, "roll": 0.0}
LOCATION = {"x": 1.0, "y": 2.0, "z": 3.0}
ROTATION = {"pitch": 4.0, "yaw": 5.0, "roll": 6.0}


def _run(tmp_path: Path, mode: str, code: str) -> dict[str, object]:
    if mode == "persistent":
        return PersistentNamespace(object()).execute(code)
    script = tmp_path / "values.py"
    script.write_text(code, encoding="utf-8")
    return run_script_file(script_path=script, host="127.0.0.1", port=2000, timeout_seconds=1.0)


@pytest.mark.parametrize("mode", ["finite", "persistent"])
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("Location()", ZERO_LOCATION),
        ("Location(1, 2, 3)", LOCATION),
        ("Location(z=3, x=1, y=2)", LOCATION),
        ("Vector3D(1, 2, 3)", LOCATION),
        ("Rotation()", ZERO_ROTATION),
        ("Rotation(4, 5, 6)", ROTATION),
        ("Transform()", {"location": ZERO_LOCATION, "rotation": ZERO_ROTATION}),
        (
            "Transform(Location(1, 2, 3), Rotation(4, 5, 6))",
            {"location": LOCATION, "rotation": ROTATION},
        ),
    ],
)
def test_native_constructor_order_and_defaults_are_injected(
    tmp_path: Path, mode: str, expression: str, expected: object
) -> None:
    """Both real namespaces expose positional/default constructors and serialize values."""
    outcome = _run(tmp_path, mode, f"result = {expression}")
    assert outcome["ok"] is True
    assert outcome["result"] == expected


@pytest.mark.parametrize("mode", ["finite", "persistent"])
def test_value_attributes_and_fresh_defaults_remain_independent(tmp_path: Path, mode: str) -> None:
    """Attribute writes update JSON fields without sharing default nested objects."""
    code = (
        "first = Transform()\nsecond = Transform()\n"
        "first.location.x = 1\nfirst.rotation.yaw = 5\n"
        "result = [first, second, first.location.x, first.rotation.yaw]"
    )
    outcome = _run(tmp_path, mode, code)
    assert outcome["ok"] is True
    assert outcome["result"] == [
        {
            "location": {"x": 1.0, "y": 0.0, "z": 0.0},
            "rotation": {"pitch": 0.0, "yaw": 5.0, "roll": 0.0},
        },
        {"location": ZERO_LOCATION, "rotation": ZERO_ROTATION},
        1.0,
        5.0,
    ]


def test_persistent_value_roundtrip_and_reserved_constructor_reinjection() -> None:
    """Saved values survive requests, while trusted constructor names follow api's policy."""
    namespace = PersistentNamespace(object())
    first = namespace.execute("saved = Location(1, 2, 3)\nLocation = 0\nresult = saved")
    second = namespace.execute("saved.x = 4\nresult = [saved, Location()]")
    assert first["result"] == LOCATION
    assert second["ok"] is True
    assert second["result"] == [{"x": 4.0, "y": 2.0, "z": 3.0}, ZERO_LOCATION]


@pytest.mark.parametrize("mode", ["finite", "persistent"])
def test_transform_copies_supplied_component_values(tmp_path: Path, mode: str) -> None:
    """Changing a supplied component later does not mutate an existing transform."""
    code = (
        "point = Location(1, 2, 3)\nangles = Rotation(4, 5, 6)\n"
        "pose = Transform(point, angles)\npoint.x = 9\nangles.yaw = 10\nresult = pose"
    )
    outcome = _run(tmp_path, mode, code)
    assert outcome["ok"] is True
    assert outcome["result"] == {"location": LOCATION, "rotation": ROTATION}


@pytest.mark.parametrize("value", [True, "not numeric"])
def test_value_constructors_preserve_parser_rejection_before_native_calls(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """Dictionary-backed values cannot coerce an invalid component into a valid number."""
    calls: list[dict[str, object]] = []
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "set_actor_transform", lambda **kwargs: calls.append(kwargs))
    api = CarlaScriptApi(adapter, RunSnapshots())
    outcome = PersistentNamespace(api).execute(
        f"result = api.set_actor_transform(1, Transform(Location({value!r}, 0, 0)))"
    )
    assert outcome["error_type"] == "TypeError"
    assert calls == []


@pytest.mark.parametrize("mode", ["finite", "persistent"])
@pytest.mark.parametrize(
    "code",
    ["import carla", "from carla import Location", "result = Location.__mro__"],
)
def test_constructors_do_not_lift_import_or_dunder_restrictions(
    tmp_path: Path, mode: str, code: str
) -> None:
    """Pure values do not grant module or reflective access."""
    assert _run(tmp_path, mode, code)["error_type"] == "script_rejected"


@pytest.mark.parametrize("parser", ["spawn", "camera", "path"])
def test_nested_values_are_accepted_by_existing_payload_parsers(parser: str) -> None:
    """Nested constructor values follow the same validation as legacy pose dictionaries."""

    def parse(payload: dict[str, object]) -> dict[str, object]:
        if parser == "path":
            return parse_traffic_vehicle_path_request(1, payload).path[0].to_dict()
        transform = (
            parse_spawn_requests([payload])[0].transform
            if parser == "spawn"
            else parse_camera_attach_request(payload).transform
        )
        return transform.to_dict()

    code = (
        "result = api.parse({'path': [Location(1, 2, 3)]})"
        if parser == "path"
        else "result = api.parse({'blueprint_id': 'sensor.camera.rgb', "
        "'transform': Transform(Location(1, 2, 3), Rotation(4, 5, 6)), 'attributes': {}})"
    )
    outcome = PersistentNamespace(SimpleNamespace(parse=parse)).execute(code)
    assert outcome["ok"] is True
    assert outcome["result"] == (
        LOCATION if parser == "path" else {"location": LOCATION, "rotation": ROTATION}
    )


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ("", {"project_to_road": True, "lane_type": "native-driving"}),
        (", False, 'Sidewalk'", {"project_to_road": False, "lane_type": "native-sidewalk"}),
    ],
)
def test_injected_location_reaches_real_native_waypoint_translation(
    monkeypatch: pytest.MonkeyPatch, arguments: str, expected: dict[str, object]
) -> None:
    """The real facade/adapter converts pure values with its existing native factory."""
    calls: list[tuple[object, dict[str, object]]] = []

    def get_waypoint(location: object, **kwargs: object) -> object:
        calls.append((location, kwargs))
        return SimpleNamespace(
            transform=SimpleNamespace(
                location=location, rotation=SimpleNamespace(pitch=0, yaw=0, roll=0)
            )
        )

    module = SimpleNamespace(
        Location=SimpleNamespace,
        LaneType=SimpleNamespace(Driving="native-driving", Sidewalk="native-sidewalk"),
    )
    monkeypatch.setattr(experiment_navigation, "import_module", lambda _name: module)
    world = SimpleNamespace(get_map=lambda: SimpleNamespace(get_waypoint=get_waypoint))
    adapter = PythonCarlaAdapter()
    monkeypatch.setattr(adapter, "_client", lambda: SimpleNamespace(get_world=lambda: world))
    api = CarlaScriptApi(adapter, RunSnapshots())

    outcome = PersistentNamespace(api).execute(
        f"result = api.get_waypoint(Location(1, 2, 3){arguments})"
    )

    assert outcome["ok"] is True
    assert vars(calls[0][0]) == LOCATION
    assert calls[0][1] == expected
