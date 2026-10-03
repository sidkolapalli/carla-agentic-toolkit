"""Pure acceptance tests for the bounded, provider-independent merge baseline."""

from __future__ import annotations

from dataclasses import replace

import pytest

from carla_agentic_toolkit.merge_planner import (
    ActorObservation,
    LaneGeometry,
    ManeuverState,
    MergeObservation,
    PlannerSettings,
    build_policy_request,
    fallback_choice,
    lateral_target,
    rules_decision,
    tracking_control,
    transition,
    valid_candidates,
)

FRAME = 100
POLICY_ID = 101
EGO_ID = 102
TARGET_LANE = -2


def observation() -> MergeObservation:
    """Build one frame-aligned, clear, legal two-lane corridor observation."""
    policy = ActorObservation(
        actor_id=POLICY_ID,
        frame=FRAME,
        longitudinal_m=0.0,
        lateral_m=0.0,
        speed_mps=6.0,
        yaw_error_degrees=0.0,
        length_m=4.8,
        width_m=1.9,
        lane_id=-1,
    )
    ego = replace(
        policy,
        actor_id=EGO_ID,
        longitudinal_m=24.0,
        lateral_m=3.5,
        lane_id=TARGET_LANE,
        speed_mps=5.0,
    )
    lane = LaneGeometry(
        road_id=1,
        source_lane_id=-1,
        target_lane_id=TARGET_LANE,
        width_m=3.5,
        target_offset_m=3.5,
        remaining_m=80.0,
        adjacent=True,
        same_direction=True,
        lane_change_allowed=True,
        source_marking="Broken",
        target_marking="Broken",
    )
    return MergeObservation(
        run_id="run-1",
        world_generation="world-1",
        frame=FRAME,
        simulation_seconds=5.0,
        policy=policy,
        ego=ego,
        lane=lane,
        neighbors=(ego,),
    )


def _choices(value: MergeObservation, state: ManeuverState) -> list[str]:
    return [
        candidate.candidate_id for candidate in valid_candidates(value, state, PlannerSettings())
    ]


@pytest.mark.parametrize("field", ["adjacent", "same_direction", "lane_change_allowed"])
def test_invalid_lane_topology_never_offers_merge(field: str) -> None:
    """Adjacency, direction, and marking permission are required numerical constraints."""
    value = observation()
    value = replace(value, lane=replace(value.lane, **{field: False}))
    assert "merge" not in _choices(value, ManeuverState(phase="preparing"))


@pytest.mark.parametrize("distance", [4.0, -4.0])
def test_insufficient_front_or_rear_gap_defers(distance: float) -> None:
    """Vehicle bounds and relative target-lane gaps constrain the candidate set."""
    value = observation()
    value = replace(value, neighbors=(replace(value.ego, longitudinal_m=distance),))
    assert _choices(value, ManeuverState(phase="preparing")) == ["defer"]


def test_closing_rear_vehicle_blocks_a_small_ttc() -> None:
    """A large distance alone is insufficient when relative closing speed is high."""
    value = observation()
    value = replace(value, neighbors=(replace(value.ego, longitudinal_m=-25.0, speed_mps=20.0),))
    assert _choices(value, ManeuverState(phase="preparing")) == ["defer"]


def test_clear_corridor_offers_a_bounded_merge_candidate() -> None:
    """The reachable clear corridor yields only stable, finite bounded actions."""
    state = ManeuverState(phase="preparing")
    candidates = valid_candidates(observation(), state, PlannerSettings())
    merge = candidates[-1]
    assert (
        merge.candidate_id,
        merge.target_lane_id,
        merge.target_speed_mps,
        merge.expires_frame,
    ) == (
        "merge",
        TARGET_LANE,
        6.0,
        FRAME + 20,
    )


def test_committed_merge_cannot_be_reversed_by_defer() -> None:
    """Fallback after commitment continues the same bounded crossing."""
    value = observation()
    settings = PlannerSettings()
    committed = transition(ManeuverState(phase="preparing"), value, "merge", settings)
    later = replace(
        value,
        frame=FRAME + 10,
        policy=replace(value.policy, frame=FRAME + 10),
        ego=replace(value.ego, frame=FRAME + 10),
        neighbors=(replace(value.ego, frame=FRAME + 10),),
    )
    continued = transition(committed, later, "defer", settings)
    assert (
        committed.phase,
        continued.phase,
        continued.maneuver_generation,
        fallback_choice(continued.phase),
    ) == ("committed", "committed", 1, "continue")
    assert lateral_target(continued, later, settings) >= lateral_target(committed, value, settings)


def test_settling_requires_stable_target_lane_before_completion() -> None:
    """Crossing completion and settled completion are distinct state transitions."""
    value = observation()
    value = replace(value, policy=replace(value.policy, lateral_m=3.5, lane_id=TARGET_LANE))
    settings = PlannerSettings()
    state = ManeuverState(phase="committed", committed_frame=FRAME - 80, maneuver_generation=1)
    settled = transition(state, value, "continue", settings)
    later = replace(
        value,
        frame=FRAME + 10,
        policy=replace(value.policy, frame=FRAME + 10),
        ego=replace(value.ego, frame=FRAME + 10),
        neighbors=(replace(value.ego, frame=FRAME + 10),),
    )
    completed = transition(settled, later, "continue", settings)
    assert (settled.phase, completed.phase, completed.outcome) == (
        "settling",
        "completed",
        "completed",
    )


def test_stale_or_misaligned_observations_abort_without_candidates() -> None:
    """No actuation decision is based on mixed frames or a repeated simulator frame."""
    value = observation()
    stale = ManeuverState(phase="preparing", last_frame=FRAME)
    misaligned = replace(value, ego=replace(value.ego, frame=FRAME - 1))
    assert (
        valid_candidates(misaligned, ManeuverState(), PlannerSettings()),
        transition(stale, value, "merge", PlannerSettings()).outcome,
    ) == ((), "stale_observation")


def test_misaligned_neighbor_cannot_authorize_a_merge() -> None:
    """Gap evidence cannot use a different frame than the controlled actors."""
    value = observation()
    value = replace(value, neighbors=(replace(value.ego, frame=FRAME - 1),))
    state = ManeuverState(phase="preparing")
    assert valid_candidates(value, state, PlannerSettings()) == ()
    assert transition(state, value, "merge", PlannerSettings()).outcome == "misaligned_observation"


def test_completed_merge_retains_its_final_reference_lane() -> None:
    """Terminal evidence measures error against the completed trajectory's destination."""
    value = observation()
    state = ManeuverState(phase="completed", committed_frame=FRAME - 80)
    assert lateral_target(state, value, PlannerSettings()) == value.lane.target_offset_m


def test_failed_crossing_has_a_finite_maneuver_deadline() -> None:
    """A controller that never reaches its lane must terminate instead of drifting."""
    state = ManeuverState(phase="committed", committed_frame=FRAME - 200, maneuver_generation=1)
    result = transition(state, observation(), "continue", PlannerSettings())
    assert (result.phase, result.outcome) == ("aborted", "maneuver_timeout")


def test_tracking_uses_metres_per_second_and_bounded_controls() -> None:
    """The local tracker has no implicit m/s to km/h mismatch or hidden behavior policy."""
    actor = observation().policy
    at_speed = tracking_control(actor, target_speed_mps=6.0, target_lateral_m=0.0)
    slowing = tracking_control(actor, target_speed_mps=0.0, target_lateral_m=100.0)
    assert (at_speed.throttle, at_speed.brake, slowing.throttle, slowing.brake, slowing.steer) == (
        0.0,
        0.0,
        0.0,
        1.0,
        0.7,
    )


def test_rules_and_optional_providers_share_identical_candidate_identity() -> None:
    """No-key rules return one decision against the same immutable policy request."""
    request = build_policy_request(
        observation(),
        ManeuverState(phase="preparing"),
        PlannerSettings(),
        revision=2,
        deadline_monotonic=100.0,
    )
    assert request is not None
    decision = rules_decision(request)
    assert (decision.context, decision.choice_id, decision.source) == (
        request.context,
        "merge",
        "rules",
    )


def test_terminal_or_empty_candidate_sets_do_not_create_policy_requests() -> None:
    """Terminal maneuvers cannot consume provider requests or resume motion."""
    result = build_policy_request(
        observation(),
        ManeuverState(phase="completed"),
        PlannerSettings(),
        revision=2,
        deadline_monotonic=100.0,
    )
    assert result is None


def test_candidate_identity_is_stable_while_actions_remain_applicable() -> None:
    """Paced replies may be briefly old without frame-derived identity invalidation."""
    value = observation()
    later = replace(
        value,
        frame=FRAME + 1,
        policy=replace(value.policy, frame=FRAME + 1),
        ego=replace(value.ego, frame=FRAME + 1),
        neighbors=(replace(value.ego, frame=FRAME + 1),),
    )
    state = ManeuverState(phase="preparing")
    first = build_policy_request(
        value, state, PlannerSettings(), revision=1, deadline_monotonic=100.0
    )
    second = build_policy_request(
        later, state, PlannerSettings(), revision=1, deadline_monotonic=100.0
    )
    assert first is not None
    assert second is not None
    assert first.context.candidate_set_id == second.context.candidate_set_id
