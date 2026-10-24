"""Canonical target names keep historical trace bytes and ownership provenance intact."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from carla_agentic_toolkit import experiment_comparison as comparison
from carla_agentic_toolkit.experiment_trace import TraceStore
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from scripts import project_demo_trace
from tests.test_project_demo_trace import _events, _write

if TYPE_CHECKING:
    from pathlib import Path

TARGET_PROGRESS_M = 40.125
TARGET_START_M = 25.0
PAIR_SIZE = 2


@pytest.mark.parametrize("name", ["target", "ego"])
def test_projection_accepts_both_names_and_emits_only_target(tmp_path: Path, name: str) -> None:
    """Historical names are translated only in the explicitly produced projection."""
    events = _events()
    _rename_events(events, name)
    path = tmp_path / "events.jsonl"
    digest = _write(path, events)
    raw = path.read_bytes()
    samples = project_demo_trace.project_trace(path, digest)["samples"]
    assert all("target" in sample and "ego" not in sample for sample in samples)
    assert samples[0]["target"]["longitudinal_m"] == TARGET_PROGRESS_M
    assert path.read_bytes() == raw


def _rename_events(events: list[dict[str, Any]], name: str) -> None:
    for event in events:
        if event["kind"] == "observation":
            event["data"][name] = event["data"].pop("ego")


def test_projection_rejects_conflicting_dual_actor_names(tmp_path: Path) -> None:
    """A second label cannot silently replace an incompatible recorded actor."""
    events = _events()
    events[1]["data"]["target"] = {**events[1]["data"]["ego"], "actor_id": 99}
    path = tmp_path / "events.jsonl"
    digest = _write(path, events)
    with pytest.raises(ValueError, match=r"target|ego|conflict"):
        project_demo_trace.project_trace(path, digest)


def _saved_spec(policy: str, *, legacy: bool, role: str | None) -> dict[str, Any]:
    spec = ExperimentSpec.model_validate({"policy": policy}).model_dump()
    speed = spec.pop("target_vehicle_speed_mps", spec.pop("ego_speed_mps", 5.0))
    spec["ego_speed_mps" if legacy else "target_vehicle_speed_mps"] = speed
    if legacy:
        spec.pop("controlled_vehicle_role", None)
    elif role is not None:
        spec["controlled_vehicle_role"] = role
    else:
        spec.pop("controlled_vehicle_role", None)
    return spec


def _saved(
    root: Path,
    policy: str,
    *,
    legacy: bool,
    role: str | None = "hero",
    spec_updates: dict[str, object] | None = None,
) -> Path:
    spec = _saved_spec(policy, legacy=legacy, role=role)
    spec.update(spec_updates or {})
    policy_pose = {"x": 1.0, "y": 2.0, "z": 0.5, "yaw": 0.0}
    lane_pose = policy_pose | {"y": 5.5}
    target_pose = lane_pose | {"x": 25.0}
    fixture = {"policy_start": policy_pose, "settings": {"target_speed_mps": 6.0}}
    fixture.update(
        {"ego_start": target_pose, "target_start": lane_pose}
        if legacy
        else {"target_start": target_pose, "target_lane_start": lane_pose}
    )
    metadata = {
        "spec": spec,
        "fixture_version": spec["fixture"],
        "policy_version": policy,
        "planner_version": "planner-v1",
        "controller_version": "controller-v1",
        "package_version": "0.1.0",
        "code_sha256": "a" * 64,
        "replicate_index": 7,
        "environment": {
            "platform": "linux",
            "python": "3.12",
            "carla_client": "0.9.16",
            "carla_server": "0.9.16",
        },
    }
    with TraceStore(policy, root=root) as store:
        for kind, data in (
            ("metadata", metadata),
            ("fixture", fixture),
            ("observation", {"speed_mps": 1.0}),
            ("outcome", {"completed": True, "status": "completed"}),
            ("cleanup", {"ok": True}),
        ):
            store.append(
                kind,
                world_generation="world",
                frame=1,
                actor_id=7,
                data=cast("dict[str, object]", data),
            )
    return store.path


@pytest.mark.parametrize("legacy", [True, False])
def test_complete_saved_pairs_accept_canonical_or_historical_names(
    tmp_path: Path, *, legacy: bool
) -> None:
    """Readers normalize names without claiming an unrecorded historical hero role."""
    paths = [_saved(tmp_path, policy, legacy=legacy) for policy in ("rules", "jev")]
    raw = _trace_bytes(paths)
    report = comparison.compare_traces(paths)
    assert report["comparable"] is True
    evidence = report["runs"][0]["matching_evidence"]
    _assert_fixture_labels(evidence["fixture"])
    assert ("controlled_vehicle_role" in evidence["spec"]) is (not legacy)
    assert _trace_bytes(paths) == raw


def _trace_bytes(paths: list[Path]) -> list[bytes]:
    return [path.read_bytes() for path in paths]


def _assert_fixture_labels(fixture: dict[str, Any]) -> None:
    assert fixture["target_start"]["x"] == TARGET_START_M
    assert fixture["target_lane_start"]["x"] == 1.0
    assert "ego_start" not in fixture


def test_legacy_role_absence_cannot_match_new_explicit_hero(tmp_path: Path) -> None:
    """Names are compatible, but different recorded role provenance is not identical."""
    paths = [_saved(tmp_path, "rules", legacy=True), _saved(tmp_path, "jev", legacy=False)]
    report = comparison.compare_traces(paths)
    assert report["comparable"] is False
    assert len(report["match_groups"]) == PAIR_SIZE


def test_missing_new_role_is_not_fabricated_for_canonical_spec(tmp_path: Path) -> None:
    """Only the legacy spelling identifies a saved spec predating the new role field."""
    paths = [_saved(tmp_path, policy, legacy=False, role=None) for policy in ("rules", "jev")]
    report = comparison.compare_traces(paths)
    assert report["comparable"] is False
    assert any("complete spec" in reason for reason in report["blockers"])


@pytest.mark.parametrize(
    "aliases",
    [
        {"ego_speed_mps": 4.0},
        {"ego_speed_mps": True},
        {"target_vehicle_speed_mps": "5.0"},
    ],
)
def test_invalid_saved_speed_aliases_block_comparison_without_crashing(
    tmp_path: Path, aliases: dict[str, object]
) -> None:
    """Untrusted saved values fail matching without escaping the evidence-error boundary."""
    paths = [
        _saved(tmp_path, policy, legacy=False, spec_updates=aliases) for policy in ("rules", "jev")
    ]
    raw = _trace_bytes(paths)
    report = comparison.compare_traces(paths)
    assert report["comparable"] is False
    assert report["blockers"]
    assert _trace_bytes(paths) == raw
