"""Narrow compatibility for renamed sensor parents and CARLA waypoint order."""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_VALUE_POSITION = 2
_LEGACY_WAYPOINT_ARGS = 3


def parent_alias[R](method: Callable[..., R]) -> Callable[..., R]:
    """Translate deprecated parent_id while retaining the canonical wrapped signature."""

    @wraps(method)
    def compatible(*args: object, **kwargs: object) -> R:
        normalized = _parent_arguments(args, kwargs)
        return method(*args, **normalized)

    return compatible


def _parent_arguments(args: tuple[object, ...], kwargs: Mapping[str, object]) -> dict[str, object]:
    normalized = dict(kwargs)
    if "parent_id" not in normalized:
        return normalized
    aliases = {"parent_id": normalized.pop("parent_id")}
    if len(args) > _VALUE_POSITION:
        aliases["attach_to"] = args[_VALUE_POSITION]
    elif "attach_to" in normalized:
        aliases["attach_to"] = normalized["attach_to"]
    parent = parent_value(aliases, legacy_name="parent_id")
    if len(args) <= _VALUE_POSITION:
        normalized["attach_to"] = parent
    return normalized


def parent_value(values: Mapping[str, object], *, legacy_name: str) -> int | None:
    """Validate present aliases independently before accepting a shared parent ID."""
    canonical = _parent_id(values.get("attach_to"), "attach_to")
    legacy = _parent_id(values.get(legacy_name), legacy_name)
    if "attach_to" in values and legacy_name in values and canonical != legacy:
        message = f"attach_to and {legacy_name} must agree when both are supplied."
        raise TypeError(message)
    return canonical if "attach_to" in values else legacy


def _parent_id(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        message = f"{name} must be an integer or null."
        raise TypeError(message)
    return value


def waypoint_order[**P, R](method: Callable[P, R]) -> Callable[P, R]:
    """Accept the old second-position lane name without changing the public signature."""

    @wraps(method)
    def compatible(*args: P.args, **kwargs: P.kwargs) -> R:
        positional, named = _waypoint_arguments(args, kwargs)
        return cast("Callable[..., R]", method)(*positional, **named)

    return compatible


def _waypoint_arguments(
    args: tuple[object, ...], kwargs: Mapping[str, object]
) -> tuple[tuple[object, ...], dict[str, object]]:
    normalized = dict(kwargs)
    if not _legacy_lane(args):
        return args, normalized
    if len(args) > _LEGACY_WAYPOINT_ARGS:
        message = "The deprecated positional lane_type form requires keyword project_to_road."
        raise TypeError(message)
    if "lane_type" in normalized and normalized["lane_type"] != args[_VALUE_POSITION]:
        message = "Positional and keyword lane_type must agree when both are supplied."
        raise TypeError(message)
    normalized["lane_type"] = args[_VALUE_POSITION]
    return args[:_VALUE_POSITION], normalized


def _legacy_lane(args: tuple[object, ...]) -> bool:
    return len(args) > _VALUE_POSITION and isinstance(args[_VALUE_POSITION], str)
