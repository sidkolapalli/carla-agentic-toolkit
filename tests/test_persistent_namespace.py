"""Persistent scripts retain state while keeping the existing validation and result bounds."""

from __future__ import annotations

from types import SimpleNamespace

from carla_agentic_toolkit.persistent_namespace import PersistentNamespace

VEHICLE_ID = 7
SAVED_VALUE = 3


def test_followup_retains_api_and_arbitrary_local_variables() -> None:
    """A later request continues the same namespace and API identity."""
    namespace = PersistentNamespace(SimpleNamespace(value=7))
    first = namespace.execute("vehicle = api.value\nsteps = [1]\nresult = vehicle")
    second = namespace.execute("steps.append(2)\nresult = [vehicle, steps, api.value]")
    assert first["result"] == VEHICLE_ID
    assert second["result"] == [7, [1, 2], 7]


def test_result_is_request_local_and_api_cannot_be_rebound_permanently() -> None:
    """Previous results do not masquerade as fresh telemetry; trusted API is reinjected."""
    namespace = PersistentNamespace(SimpleNamespace(value=7))
    namespace.execute("result = 99\napi = 0")
    assert namespace.execute("value = api.value")["result"] is None
    assert namespace.execute("result = value")["result"] == VEHICLE_ID


def test_forbidden_features_are_rejected_on_every_followup() -> None:
    """A valid first turn cannot disable validation for subsequent code."""
    namespace = PersistentNamespace(object())
    namespace.execute("saved = 3")
    assert namespace.execute("import os")["error_type"] == "script_rejected"
    assert namespace.execute("result = saved")["result"] == SAVED_VALUE


def test_output_and_result_limits_remain_enforced_on_followups() -> None:
    """Persistent state cannot bypass bounded UTF-8 output or aggregate item limits."""
    namespace = PersistentNamespace(object())
    oversized = namespace.execute("print('x' * 600000)")
    unserializable = namespace.execute("result = list(range(10001))")
    assert oversized["error_type"] == "output_too_large"
    assert unserializable["error_type"] == "result_not_serializable"


def test_script_errors_preserve_prior_variables_but_are_explicit() -> None:
    """A caught user error reports failure without pretending the preceding state vanished."""
    namespace = PersistentNamespace(object())
    namespace.execute("saved = 3")
    assert namespace.execute("result = 1 / 0")["error_type"] == "ZeroDivisionError"
    assert namespace.execute("result = saved")["result"] == SAVED_VALUE
