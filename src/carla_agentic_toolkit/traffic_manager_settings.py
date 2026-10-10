"""Validate and restore declared targets for only the TM globals a run attempted."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaClient

MAX_TRAFFIC_MANAGERS = 32
MAX_PORT = 65_535
SYNC_SETTING = "synchronous_mode"
_RESTORE_TARGETS: dict[str, object] = {
    "global_distance_to_leading_vehicle": 2.0,
    "global_percentage_speed_difference": 0.0,
    "seed": 0,
    SYNC_SETTING: False,
}
_SETTERS = {
    "global_distance_to_leading_vehicle": "set_global_distance_to_leading_vehicle",
    "global_percentage_speed_difference": "global_percentage_speed_difference",
    "seed": "set_random_device_seed",
    SYNC_SETTING: "set_synchronous_mode",
}
_LEGACY_FIELDS = {"port", "original_mode", "applied_mode"}
_SPARSE_FIELDS = {"port", "restore_targets", "attempted_settings"}


def validate_manager_setting(port: int, *, setting: str, value: object) -> None:
    """Validate an impending attempt before durable or native mutation."""
    _validate_port(port)
    if setting not in _RESTORE_TARGETS:
        message = "Traffic Manager setting is outside the reviewed restoration fields."
        raise ValueError(message)
    _validate_attempt(setting, value)


def record_manager_setting(
    managers: list[dict[str, object]], port: int, *, setting: str, value: object
) -> None:
    """Keep first declared targets and the latest attempt for each port and field."""
    manager = _manager_entry(managers, port)
    _upgrade_legacy(manager)
    targets = cast("dict[str, object]", manager["restore_targets"])
    targets.setdefault(setting, _RESTORE_TARGETS[setting])
    cast("dict[str, object]", manager["attempted_settings"])[setting] = value


def _manager_entry(managers: list[dict[str, object]], port: int) -> dict[str, object]:
    for manager in managers:
        if manager["port"] == port:
            return manager
    if len(managers) >= MAX_TRAFFIC_MANAGERS:
        message = "Script settings journal exceeds its Traffic Manager limit."
        raise ValueError(message)
    manager: dict[str, object] = {"port": port, "restore_targets": {}, "attempted_settings": {}}
    managers.append(manager)
    return manager


def _upgrade_legacy(manager: dict[str, object]) -> None:
    if "original_mode" in manager:
        manager.pop("original_mode")
        mode = manager.pop("applied_mode")
        manager.update(
            restore_targets={SYNC_SETTING: False},
            attempted_settings={SYNC_SETTING: mode},
        )


def validate_manager_entries(value: object, settings: object) -> None:
    """Reject ambiguity before child-supplied evidence authorizes any native calls."""
    managers = _manager_list(value)
    if managers and settings is None:
        message = "Traffic Manager restoration requires a recorded world."
        raise ValueError(message)
    seen: set[int] = set()
    for manager in managers:
        _validate_manager(manager)
        port = cast("int", manager["port"])
        if port in seen:
            message = "Traffic Manager restoration requires unique ports."
            raise ValueError(message)
        seen.add(port)


def _manager_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        message = "Settings journal requires a Traffic Manager list."
        raise TypeError(message)
    if len(value) > MAX_TRAFFIC_MANAGERS:
        message = "Settings journal requires a bounded Traffic Manager list."
        raise ValueError(message)
    return cast("list[dict[str, object]]", value)


def _validate_manager(value: object) -> None:
    if not isinstance(value, dict):
        message = "Invalid Traffic Manager restoration entry."
        raise TypeError(message)
    manager = cast("dict[str, object]", value)
    _validate_port(manager.get("port"))
    if set(manager) == _LEGACY_FIELDS:
        _validate_legacy(manager)
    elif set(manager) == _SPARSE_FIELDS:
        _validate_sparse(manager)
    else:
        message = "Traffic Manager restoration requires exactly the reviewed fields."
        raise ValueError(message)


def _validate_port(port: object) -> None:
    if type(port) is not int or not 0 < port <= MAX_PORT:
        message = "Traffic Manager restoration requires a valid port."
        raise ValueError(message)


def _validate_legacy(manager: dict[str, object]) -> None:
    if manager["original_mode"] is not False or type(manager["applied_mode"]) is not bool:
        message = "Traffic Manager restoration requires the declared asynchronous target."
        raise ValueError(message)


def _validate_sparse(manager: dict[str, object]) -> None:
    targets, attempts = _setting_maps(manager)
    for setting, target in targets.items():
        expected = _RESTORE_TARGETS[setting]
        if type(target) is not type(expected) or target != expected:
            message = "Traffic Manager restoration requires canonical declared targets."
            raise ValueError(message)
        _validate_attempt(setting, attempts[setting])


def _setting_maps(manager: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    targets, attempts = manager["restore_targets"], manager["attempted_settings"]
    if not isinstance(targets, dict) or not isinstance(attempts, dict):
        message = "Traffic Manager restoration requires target and attempted-setting maps."
        raise TypeError(message)
    target_map = cast("dict[str, object]", targets)
    attempt_map = cast("dict[str, object]", attempts)
    _require_reviewed_pairs(target_map, attempt_map)
    return target_map, attempt_map


def _require_reviewed_pairs(targets: dict[str, object], attempts: dict[str, object]) -> None:
    if not targets or set(targets) != set(attempts) or not set(targets) <= set(_RESTORE_TARGETS):
        message = "Traffic Manager restoration requires paired reviewed settings."
        raise ValueError(message)


def _validate_attempt(setting: str, value: object) -> None:
    if setting == SYNC_SETTING:
        valid = type(value) is bool
    elif setting == "seed":
        valid = type(value) is int
    else:
        valid = _finite_number(value)
    if not valid:
        message = f"Traffic Manager attempted setting {setting} has an invalid value."
        raise ValueError(message)


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(cast("float", value))
    except OverflowError:
        return False


def restore_manager_settings(
    client: CarlaClient,
    managers: list[dict[str, object]],
    *,
    require_episode: Callable[[], None],
) -> dict[str, object]:
    """Report acknowledged declared setters, without inventing TM readback getters."""
    evidence: list[dict[str, object]] = []
    async_ports: list[int] = []
    for manager in managers:
        port = cast("int", manager["port"])
        targets = _targets(manager)
        require_episode()
        native = client.get_trafficmanager(port)
        for setting in _RESTORE_TARGETS:
            if setting in targets:
                require_episode()
                getattr(native, _SETTERS[setting])(targets[setting])
        evidence.append({"port": port, "restore_targets": targets})
        if SYNC_SETTING in targets:
            async_ports.append(port)
    return {"traffic_manager_restore_targets": evidence, "traffic_manager_async_ports": async_ports}


def _targets(manager: dict[str, object]) -> dict[str, object]:
    if "original_mode" in manager:
        return {SYNC_SETTING: False}
    return cast("dict[str, object]", manager["restore_targets"]).copy()
