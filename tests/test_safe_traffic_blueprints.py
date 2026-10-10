"""Traffic safety uses native vehicle classification without changing legacy selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.traffic_runtime import (
    _is_safe_vehicle_blueprint,
    _traffic_vehicle_blueprints,
)

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaBlueprint, CarlaWorld

MISSING = object()
LEGACY_EXCLUDED_IDS = (
    "vehicle.ford.ambulance",
    "vehicle.carlacola.cola",
    "vehicle.carlamotors.carlacola",
    "vehicle.tesla.cybertruck",
    "vehicle.carlamotors.firetruck",
    "vehicle.bmw.isetta",
    "vehicle.micro.microlino",
    "vehicle.mercedes.sprinter",
)


@dataclass
class NativeAttribute:
    """Expose both native casts, with diagnostic text that is not the string value."""

    value: str
    kind: str = "String"
    reads: list[str] = field(default_factory=list)
    string_error: Exception | None = None

    def as_str(self) -> str:
        """Return a raw string only through the native string accessor."""
        self.reads.append("as_str")
        if self.string_error is not None:
            raise self.string_error
        if self.kind != "String":
            message = "Attribute is not a string."
            raise ValueError(message)
        return self.value

    def as_int(self) -> int:
        """Reject strings as CARLA's typed accessor does, even for numeric-looking text."""
        self.reads.append("as_int")
        if self.kind != "Int":
            message = "Attribute is not an integer."
            raise ValueError(message)
        return int(self.value)

    def __str__(self) -> str:
        """Keep object display distinct from the attribute's native raw value."""
        self.reads.append("display")
        return f"ActorAttribute(type={self.kind}, value={self.value!r})"


@dataclass
class VehicleBlueprint:
    """Distinguish present blank attributes from unavailable legacy attributes."""

    id: str
    attributes: dict[str, object]
    checked: list[str] = field(default_factory=list)
    fetched: list[str] = field(default_factory=list)
    read_error: Exception | None = None

    def has_attribute(self, name: str) -> bool:
        """Record existence checks without treating empty text as absence."""
        self.checked.append(name)
        return name in self.attributes

    def get_attribute(self, name: str) -> object:
        """Expose native-like attribute objects only when the attribute exists."""
        self.fetched.append(name)
        if name == "base_type" and self.read_error is not None:
            raise self.read_error
        return self.attributes[name]


@dataclass
class VehicleLibrary:
    """Preserve native inventory order so the selector must sort deterministically."""

    blueprints: list[VehicleBlueprint]
    filters: list[str] = field(default_factory=list)

    def filter(self, pattern: str) -> list[VehicleBlueprint]:
        """Return a fresh inventory without modifying the source list."""
        self.filters.append(pattern)
        return list(self.blueprints)


@dataclass
class VehicleWorld:
    """Use the actual traffic selector with a fake read-only native library."""

    library: VehicleLibrary

    def get_blueprint_library(self) -> VehicleLibrary:
        """Expose the library directly without any spawning or Traffic Manager calls."""
        return self.library


def _blueprint(
    blueprint_id: str = "vehicle.audi.a2", *, base_type: object = MISSING, wheels: object = 4
) -> VehicleBlueprint:
    attributes: dict[str, object] = {}
    if base_type is not MISSING:
        attributes["base_type"] = NativeAttribute(cast("str", base_type))
    if wheels is not MISSING:
        attributes["number_of_wheels"] = NativeAttribute(str(wheels), kind="Int")
    return VehicleBlueprint(blueprint_id, attributes)


def _safe(blueprint: VehicleBlueprint) -> bool:
    return _is_safe_vehicle_blueprint(cast("CarlaBlueprint", blueprint))


def _select(blueprints: list[VehicleBlueprint], *, safe_filter: bool) -> tuple[str, ...]:
    library = VehicleLibrary(blueprints)
    world = VehicleWorld(library)
    result = _traffic_vehicle_blueprints(cast("CarlaWorld", world), safe_filter=safe_filter)
    assert library.filters == ["vehicle.*"]
    assert library.blueprints == blueprints
    return tuple(blueprint.id for blueprint in result)


@pytest.mark.parametrize(
    "base_type", ["car", "van", "bus", "Bus", "truck", "motorcycle", "bicycle", "", "Car", " "]
)
def test_present_base_type_keeps_only_exact_car(base_type: str) -> None:
    """Native car classification is authoritative, including blank and case-sensitive types."""
    blueprint = _blueprint(base_type=base_type)

    assert _safe(blueprint) is (base_type == "car")


@pytest.mark.parametrize("blueprint_id", LEGACY_EXCLUDED_IDS)
def test_native_car_overrides_legacy_name_exclusions(blueprint_id: str) -> None:
    """Native car metadata supersedes the old heuristic blacklist."""
    blueprint = _blueprint(blueprint_id, base_type="car")

    assert _safe(blueprint) is True


@pytest.mark.parametrize("wheels", [2, 6, MISSING])
def test_native_car_does_not_apply_legacy_wheel_heuristic(wheels: object) -> None:
    """Wheel-count filtering belongs only to the missing-base-type fallback."""
    blueprint = _blueprint(base_type="car", wheels=wheels)

    assert _safe(blueprint) is True
    assert "number_of_wheels" not in blueprint.fetched


@pytest.mark.parametrize("blueprint_id", ["vehicle.audi.a2", *LEGACY_EXCLUDED_IDS])
def test_missing_base_type_preserves_legacy_name_policy(blueprint_id: str) -> None:
    """Older clients without classification retain every existing name exclusion."""
    blueprint = _blueprint(blueprint_id)

    assert _safe(blueprint) is (blueprint_id not in LEGACY_EXCLUDED_IDS)


@pytest.mark.parametrize(
    ("wheels", "expected"), [(4, True), (2, False), (6, False), (MISSING, True)]
)
def test_missing_base_type_preserves_legacy_wheel_policy(wheels: object, *, expected: bool) -> None:
    """The existing wheel-count rules stay unchanged for missing classification."""
    blueprint = _blueprint(wheels=wheels)

    assert _safe(blueprint) is expected


@pytest.mark.parametrize("wheels", [4, MISSING, 2])
def test_present_blank_base_type_never_uses_missing_attribute_fallback(wheels: object) -> None:
    """An exposed but unclassified vehicle is not evidence of a native car."""
    blueprint = _blueprint(base_type="", wheels=wheels)

    assert _safe(blueprint) is False
    assert "number_of_wheels" not in blueprint.fetched


@pytest.mark.parametrize("failure_stage", ["get_attribute", "as_str"])
def test_unreadable_present_classification_cannot_admit_vehicle(failure_stage: str) -> None:
    """A known present non-car never enters the legacy fallback after a read failure."""
    blueprint = _blueprint(base_type="van")
    error = RuntimeError("base_type unavailable")
    if failure_stage == "get_attribute":
        blueprint.read_error = error
    else:
        cast("NativeAttribute", blueprint.attributes["base_type"]).string_error = error

    assert _safe(blueprint) is False


def test_native_base_type_uses_string_cast_not_integer_cast_or_display() -> None:
    """All native casts exist, so probing as_int first must not swallow car classification."""
    blueprint = _blueprint(base_type="car")
    attribute = cast("NativeAttribute", blueprint.attributes["base_type"])

    assert _safe(blueprint) is True
    assert attribute.reads == ["as_str"]


def test_plain_string_attribute_remains_compatible() -> None:
    """Small older blueprint doubles may expose the string value directly."""
    blueprint = _blueprint()
    blueprint.attributes["base_type"] = "car"

    assert _safe(blueprint) is True


def _mixed_blueprints() -> list[VehicleBlueprint]:
    return [
        _blueprint("vehicle.volkswagen.t2", base_type="van"),
        _blueprint("vehicle.mitsubishi.fusorosa", base_type="Bus"),
        _blueprint("vehicle.bmw.grandtourer", base_type=""),
        _blueprint("vehicle.tesla.cybertruck", base_type="car"),
        _blueprint("vehicle.audi.a2"),
    ]


def test_safe_filter_is_deterministically_sorted_after_classification() -> None:
    """Native classification changes membership without changing blueprint ordering."""
    blueprints = _mixed_blueprints()
    expected = ("vehicle.audi.a2", "vehicle.tesla.cybertruck")

    assert _select(blueprints, safe_filter=True) == expected
    assert _select(list(reversed(blueprints)), safe_filter=True) == expected


def test_disabled_safe_filter_returns_full_sorted_inventory_without_attribute_reads() -> None:
    """safe_filter=False bypasses both native classification and legacy heuristics."""
    blueprints = _mixed_blueprints()

    assert _select(blueprints, safe_filter=False) == tuple(sorted(bp.id for bp in blueprints))
    assert all(not bp.checked and not bp.fetched for bp in blueprints)


def test_no_native_cars_preserves_existing_empty_selection_failure() -> None:
    """An all-non-car inventory reports the existing no-blueprints error before spawning."""
    blueprints = [_blueprint(base_type=value) for value in ("van", "bus", "")]

    with pytest.raises(CarlaAdapterError, match="No vehicle blueprints"):
        _select(blueprints, safe_filter=True)
