"""Specs are data, with bounded coherent timing and explicit supported scope."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from carla_agentic_toolkit.managed_spec import ExperimentSpec


@pytest.mark.parametrize(
    "field", ["code", "imports", "shell", "provider_url", "background_traffic"]
)
def test_spec_rejects_unreviewed_capabilities(field: str) -> None:
    """Unknown spec fields never become executable/provider configuration."""
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({field: "arbitrary"})


@pytest.mark.parametrize(
    "values",
    [
        {"fixed_delta_seconds": float("nan")},
        {"max_steps": 100_001},
        {"fixed_delta_seconds": 0.1, "max_substeps": 2, "max_substep_delta_time": 0.01},
        {"max_wall_seconds": 0},
        {"port": True},
        {"fixture": "unverified-map"},
        {"policy": "https://attacker.invalid"},
    ],
)
def test_spec_rejects_invalid_bounds(values: dict[str, object]) -> None:
    """Invalid timing or arbitrary strategies fail before simulator connection."""
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(values)


def test_spec_is_immutable_and_default_baseline_needs_no_key() -> None:
    """A run uses one reviewed immutable specification."""
    spec = ExperimentSpec()
    assert spec.policy == "rules"
    assert spec.fixed_delta_seconds <= spec.max_substeps * spec.max_substep_delta_time
    with pytest.raises(ValidationError):
        spec.replicate_index = 99


def test_ue5_fixture_is_explicit_and_preserves_the_original_default() -> None:
    """Changed vehicle physics must have a different fixture identity in saved specs."""
    spec = ExperimentSpec.model_validate({"fixture": "town10-merge-ue5-v1"})

    assert spec.fixture == "town10-merge-ue5-v1"
    assert ExperimentSpec().fixture == "town10-merge-v1"


def test_replay_requires_an_explicit_private_run_identity() -> None:
    """A replay mode may not invent a scene or accept an arbitrary file path."""
    with pytest.raises(ValidationError):
        ExperimentSpec(policy="replay")
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({"policy": "replay", "replay_run_id": "../../secret"})
