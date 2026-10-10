"""Optional density is explicit immutable data, not a new default controller."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from carla_agentic_toolkit.managed_spec import ExperimentSpec


def test_disabled_density_preserves_existing_complete_spec() -> None:
    """A missing optional feature must not silently change historical matching."""
    spec = ExperimentSpec()
    assert "background_density" not in spec.model_dump()
    assert getattr(spec, "background_density", None) is None


@pytest.mark.parametrize("count", [1, 100])
def test_explicit_density_serializes_requested_and_boundary_parameters(count: int) -> None:
    """The target and maintenance cadence remain separate, exact numerical inputs."""
    config = {"vehicle_count": count, "traffic_manager_port": 8500}
    spec = ExperimentSpec.model_validate({"background_density": config})
    assert spec.model_dump()["background_density"] == config | {"maintenance_interval_steps": 20}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("vehicle_count", 0),
        ("vehicle_count", 101),
        ("vehicle_count", True),
        ("vehicle_count", 2.0),
        ("traffic_manager_port", 0),
        ("traffic_manager_port", 65536),
        ("traffic_manager_port", False),
        ("traffic_manager_port", "8500"),
        ("maintenance_interval_steps", 0),
        ("maintenance_interval_steps", 201),
        ("maintenance_interval_steps", True),
        ("seed", 7),
        ("reset", True),
    ],
)
def test_density_rejects_coercion_and_unreviewed_controls(field: str, value: object) -> None:
    """Invalid permissions/controller data never reaches native resource setup."""
    config = {"vehicle_count": 2, "traffic_manager_port": 8500, field: value}
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({"background_density": config})


@pytest.mark.parametrize("missing", ["vehicle_count", "traffic_manager_port"])
def test_density_requires_explicit_count_and_private_port(missing: str) -> None:
    """No omitted target or port can authorize local hosting."""
    config = {"vehicle_count": 2, "traffic_manager_port": 8500}
    del config[missing]
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({"background_density": config})


def test_density_is_immutable_and_none_is_disabled() -> None:
    """No request can mutate its controller configuration after authorization."""
    spec = ExperimentSpec.model_validate(
        {"background_density": {"vehicle_count": 2, "traffic_manager_port": 8500}}
    )
    config = spec.background_density
    assert config is not None
    with pytest.raises(ValidationError):
        config.vehicle_count = 3
    assert "background_density" not in ExperimentSpec(background_density=None).model_dump()
