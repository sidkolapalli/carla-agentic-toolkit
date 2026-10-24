"""Canonical managed labels with explicit historical merge spelling compatibility."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

    from carla_agentic_toolkit.managed_spec import ExperimentSpec

MAX_TARGET_SPEED_MPS = 12.0


def managed_role(spec: ExperimentSpec, run_id: str, role: str) -> str:
    """Assign descriptive labels only; journals independently establish ownership."""
    if role == "policy":
        return spec.controlled_vehicle_role
    label = f"managed:{run_id}:{role}"
    return label if label != spec.controlled_vehicle_role else f"{label}:other"


def normalize_merge_spec(values: Mapping[str, object]) -> dict[str, object]:
    """Validate both speed spellings without adding absent fields or defaults."""
    result = dict(values)
    names = ("ego_speed_mps", "target_vehicle_speed_mps")
    for name in names:
        if name in result:
            _require_speed(result[name], name)
    _rename_value(result, *names)
    return result


def _rename_value(result: dict[str, object], old_name: str, new_name: str) -> None:
    if old_name in result:
        old = result.pop(old_name)
        if new_name in result and result[new_name] != old:
            message = f"Conflicting {old_name} and {new_name} evidence."
            raise ValueError(message)
        result[new_name] = old


def _require_speed(value: object, name: str) -> None:
    if type(value) not in (float, int):
        message = f"{name} must be a finite number in 1..12 metres per second."
        raise ValueError(message)
    number = cast("int | float", value)
    if not math.isfinite(number) or not 1.0 <= number <= MAX_TARGET_SPEED_MPS:
        message = f"{name} must be a finite number in 1..12 metres per second."
        raise ValueError(message)


def normalize_merge_observation(values: Mapping[str, object]) -> dict[str, object]:
    """Translate the historical other-car label, refusing conflicting dual evidence."""
    result = dict(values)
    _rename_value(result, "ego", "target")
    return result


def normalize_merge_fixture(values: Mapping[str, object]) -> dict[str, object]:
    """Preserve both historical car and lane-anchor poses without inventing evidence."""
    result = dict(values)
    if "ego_start" in result:
        _legacy_corridor(result)
    settings = result.get("settings")
    if isinstance(settings, dict):
        result["settings"] = normalize_merge_spec(cast("dict[str, object]", settings))
    return result


def _legacy_corridor(result: dict[str, object]) -> None:
    if "target_lane_start" not in result and "target_start" in result:
        result["target_lane_start"] = result.pop("target_start")
    _rename_value(result, "ego_start", "target_start")
