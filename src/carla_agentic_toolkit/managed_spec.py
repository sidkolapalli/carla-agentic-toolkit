"""Reviewed, immutable input for dedicated-instance driving experiments."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from carla_agentic_toolkit.errors import UnsupportedFeatureError


class ExperimentSpec(BaseModel):
    """Accept numerical experiment data, never generated code or provider endpoints."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)

    schema_version: Literal[1] = 1
    fixture: Literal["town10-merge-v1", "town10-merge-ue5-v1", "town10-route-ue5-v1"] = (
        "town10-merge-v1"
    )
    scenario: Literal["lead_brake", "cut_in", "pedestrian_crossing"] | None = None
    host: str = Field(default="127.0.0.1", min_length=1, max_length=253)
    port: int = Field(default=2000, ge=1, le=65533)
    seed: int = Field(default=7, ge=0, le=2**31 - 1)
    policy: Literal["rules", "jev", "replay"] = "rules"
    replay_run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    timing_mode: Literal["simulation_time", "paced"] = "simulation_time"
    observation_mode: Literal["range_filtered_ground_truth"] = "range_filtered_ground_truth"
    fixed_delta_seconds: float = Field(default=0.05, ge=0.01, le=0.1)
    max_substep_delta_time: float = Field(default=0.01, gt=0, le=0.01)
    max_substeps: int = Field(default=10, ge=1, le=16)
    max_steps: int = Field(default=600, ge=1, le=3600)
    max_wall_seconds: float = Field(default=180.0, ge=1.0, le=3600.0)
    rpc_timeout_seconds: float = Field(default=5.0, ge=0.1, le=10.0)
    target_speed_mps: float = Field(default=6.0, ge=1.0, le=12.0)
    ego_speed_mps: float = Field(default=5.0, ge=1.0, le=12.0)
    observation_range_m: float = Field(default=80.0, ge=20.0, le=150.0)
    decision_interval_steps: int = Field(default=10, ge=1, le=100)
    decision_timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    max_requests: int = Field(default=40, ge=1, le=200)
    max_trace_bytes: int = Field(default=16_777_216, ge=65_536, le=67_108_864)

    @model_validator(mode="after")
    def explicit_route_scenario(self) -> Self:
        """Require a reviewed scenario exactly when using the route fixture."""
        if (self.fixture == "town10-route-ue5-v1") != (self.scenario is not None):
            message = "scenario is required exactly for town10-route-ue5-v1"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def coherent_timing(self) -> Self:
        """Keep the physics budget consistent with the scheduled fixed step."""
        if self.fixed_delta_seconds > self.max_substeps * self.max_substep_delta_time:
            message = "fixed_delta_seconds must fit max_substeps * max_substep_delta_time"
            raise ValueError(message)
        return self

    @model_validator(mode="after")
    def explicit_replay_source(self) -> Self:
        """Require a private run ID only for exact recorded-response replay."""
        if (self.policy == "replay") != (self.replay_run_id is not None):
            message = "replay_run_id is required exactly when policy is replay"
            raise ValueError(message)
        return self

    def require_live_policy(self) -> None:
        """Keep historical replay specs readable while refusing fresh live replay scenes."""
        if self.policy == "replay":
            message = (
                "Fresh live replay is unsupported: use offline saved-context replay with "
                "RecordedPolicy. A new CARLA session cannot preserve the recorded world, "
                "actor, frame, and observation identities."
            )
            raise UnsupportedFeatureError(message)
