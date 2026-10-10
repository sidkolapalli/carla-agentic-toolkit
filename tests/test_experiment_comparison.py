"""Saved-evidence comparison must not invent matched trials or hide invalid runs."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal

import pytest

from carla_agentic_toolkit import experiment_comparison as comparison
from carla_agentic_toolkit.experiment_trace import TraceStore
from carla_agentic_toolkit.managed_spec import ExperimentSpec

PAIR_SIZE = 2
LATENCY_SECONDS = 0.25

if TYPE_CHECKING:
    from pathlib import Path


def _save_run(
    root: Path,
    name: str,
    policy: Literal["rules", "jev"],
    **changes: Any,  # noqa: ANN401
) -> Path:
    """Create real saved records with controlled physical and reproduction evidence."""
    spec = ExperimentSpec(policy=policy).model_dump()
    spec.update(changes.pop("spec", {}))
    metadata = {
        "spec": spec,
        "fixture_version": spec["fixture"],
        "policy_version": policy,
        "planner_version": "planner-v1",
        "controller_version": "controller-v1",
        "package_version": "0.1.0",
        "code_sha256": "a" * 64,
        "replicate_index": spec["replicate_index"],
        "environment": {
            "platform": "linux",
            "python": "3.12",
            "carla_server": "0.9.16",
            "carla_client": "0.9.16",
        },
    }
    metadata.update(changes.pop("metadata", {}))
    fixture = {
        "policy_start": {"x": 1.0, "y": 2.0, "z": 0.5, "yaw": 0.0},
        "ego_start": {"x": 25.0, "y": 5.5, "z": 0.5, "yaw": 0.0},
        "target_start": {"x": 1.0, "y": 5.5, "z": 0.5, "yaw": 0.0},
        "settings": {"target_speed_mps": 6.0},
        "actor_ids": {"policy": len(name)},
    }
    fixture.update(changes.pop("fixture", {}))
    with TraceStore(name, root=root) as store:
        _append(store, "metadata", metadata)
        _append(store, "fixture", fixture)
        _append(store, "observation", {"speed_mps": 1.0, "simulation_seconds": 0.0})
        _append(store, "observation", {"speed_mps": 1.0, "simulation_seconds": 1.0})
        _append(store, "sensor", {"delivery_only": True, "dropped_samples": 0})
        _append(store, "intervention", {"fallback": True})
        _append(
            store,
            "decision_received",
            {
                "latency_seconds": 0.25,
                "staleness_frames": 1,
                "usage": changes.get("usage"),
                "metadata": {"requested_model": "jev-test", "question_version": "choice-test"},
            },
        )
        if changes.get("collision"):
            _append(store, "sensor", {"collision_impulse": {"x": 3.0, "y": 4.0, "z": 0.0}})
        if not changes.get("partial"):
            _append(store, "outcome", {"completed": True, "status": "completed"})
        _append(store, "cleanup", {"ok": not changes.get("invalid", False)})
    return store.path


def _append(store: TraceStore, kind: str, data: dict[str, Any]) -> None:
    store.append(kind, world_generation="world", frame=1, actor_id=7, data=data)


def test_comparison_matches_saved_trials_and_keeps_exact_policy_metadata(tmp_path: Path) -> None:
    """Only policy and ephemeral actor identity differ in a matched pair."""
    rules = _save_run(tmp_path, "rules-one", "rules", collision=True)
    jev = _save_run(tmp_path, "jev-one", "jev")
    report = comparison.compare_traces([rules, jev])

    assert (report["comparable"], report["blockers"]) == (True, [])
    assert (
        report["policies"]["rules"]["sample_count"],
        report["policies"]["jev"]["usage"],
    ) == (1, None)
    metrics = report["policies"]["rules"]["metrics"]
    assert (
        metrics["completion"],
        metrics["observed_collision"],
        metrics["interventions"]["mean"],
        metrics["fallbacks"]["mean"],
        metrics["decision_latency_mean_seconds"]["mean"],
    ) == (
        {"numerator": 1, "denominator": 1, "rate": 1.0},
        {"numerator": 1, "denominator": 1, "rate": 1.0},
        1.0,
        1.0,
        LATENCY_SECONDS,
    )
    assert report["runs"][1]["decision_metadata"] == [
        {"requested_model": "jev-test", "question_version": "choice-test"},
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"spec": {"replicate_index": 8}},
        {"spec": {"timing_mode": "paced"}},
        {"spec": {"fixture": "town10-merge-ue5-v1"}},
        {"metadata": {"code_sha256": "b" * 64}},
        {"metadata": {"environment": {"carla_server": "changed"}}},
        {"metadata": {"controller_version": "changed"}},
        {"metadata": {"planner_version": "changed"}},
        {"fixture": {"policy_start": {"x": 2.0}}},
        {"fixture": {"vehicle_blueprint": "vehicle.lincoln.mkz"}},
    ],
)
def test_mismatched_trials_refuse_comparative_metrics(
    tmp_path: Path,
    changes: dict[str, Any],
) -> None:
    """A changed constraint, replicate, exact pose, environment, or code blocks comparison."""
    rules = _save_run(tmp_path, "rules", "rules")
    jev = _save_run(tmp_path, "jev", "jev", **changes)
    report = comparison.compare_traces([rules, jev])

    assert report["comparable"] is False
    assert report["blockers"]
    assert report["policies"]["rules"]["metrics"] is None
    assert len(report["runs"]) == PAIR_SIZE


def test_missing_counterpart_and_duplicate_runs_cannot_inflate_samples(tmp_path: Path) -> None:
    """Missing policy and reused input are reported without manufacturing a sample."""
    rules = _save_run(tmp_path, "rules", "rules")
    report = comparison.compare_traces([rules, rules])

    assert report["comparable"] is False
    assert report["policies"]["rules"]["sample_count"] == 1
    assert any("duplicate" in reason for reason in report["blockers"])
    assert any("counterpart" in reason for reason in report["blockers"])


def test_invalid_and_partial_runs_remain_visible_but_leave_physical_denominators(
    tmp_path: Path,
) -> None:
    """Each policy retains all trials; only verified terminal evidence contributes rates."""
    paths = [
        _save_run(tmp_path, "rules-good", "rules"),
        _save_run(tmp_path, "rules-invalid", "rules", invalid=True, collision=True),
        _save_run(tmp_path, "jev-good", "jev"),
        _save_run(tmp_path, "jev-partial", "jev", partial=True),
    ]
    report = comparison.compare_traces(paths)

    assert (
        report["comparable"],
        report["policies"]["rules"]["sample_count"],
        report["policies"]["rules"]["excluded_count"],
        report["policies"]["jev"]["status_counts"]["partial"],
    ) == (True, PAIR_SIZE, 1, 1)
    assert (
        report["policies"]["rules"]["metrics"]["observed_collision"],
        report["policies"]["jev"]["metrics"]["completion"]["denominator"],
    ) == ({"numerator": 0, "denominator": 1, "rate": 0.0}, 1)
    assert "excluded" in report["definitions"]["physical_denominator"]


def test_missing_reproduction_metadata_blocks_matching(tmp_path: Path) -> None:
    """Two equally missing version fields are not evidence of identical versions."""
    paths = [
        _save_run(tmp_path, "rules", "rules", metadata={"controller_version": None}),
        _save_run(tmp_path, "jev", "jev", metadata={"controller_version": None}),
    ]
    report = comparison.compare_traces(paths)
    assert report["comparable"] is False
    assert any("metadata" in reason for reason in report["blockers"])


def test_malformed_spec_cannot_count_as_matching_evidence(tmp_path: Path) -> None:
    """Schema-invalid saved data is diagnostic evidence rather than a matched experiment."""
    malformed = _save_run(tmp_path, "rules", "rules", metadata={"spec": "invalid"})
    jev = _save_run(tmp_path, "jev", "jev")
    report = comparison.compare_traces([malformed, jev])
    assert report["comparable"] is False
    assert any("metadata" in reason for reason in report["blockers"])


@pytest.mark.parametrize(
    "changes",
    [
        {"metadata": {"environment": {"platform": "linux"}}},
        {"fixture": {"ego_start": None}},
        {"fixture": {"settings": None}},
    ],
)
def test_equally_missing_environment_or_pose_is_not_a_match(
    tmp_path: Path,
    changes: dict[str, Any],
) -> None:
    """Matching absence cannot stand in for simulator versions or exact initial conditions."""
    paths = [
        _save_run(tmp_path, "rules", "rules", **changes),
        _save_run(tmp_path, "jev", "jev", **changes),
    ]
    assert comparison.compare_traces(paths)["comparable"] is False


def test_static_report_cli_escapes_evidence_and_preserves_unknown_usage(tmp_path: Path) -> None:
    """The command derives JSON and inert HTML from files without simulator/provider calls."""
    changes = {"metadata": {"controller_version": "<script>alert(1)</script>"}}
    paths = [
        _save_run(tmp_path, "rules", "rules", **changes),
        _save_run(tmp_path, "jev", "jev", **changes),
    ]
    destination = tmp_path / "comparison"
    assert comparison.main(["--output", str(destination), *map(str, paths)]) == 0
    report = json.loads((destination / "comparison.json").read_text(encoding="utf-8"))
    rendered = (destination / "comparison.html").read_text(encoding="utf-8")

    assert report["comparable"] is True
    assert (
        "<script>" in rendered,
        "&lt;script&gt;" in rendered,
        "https://" in rendered,
    ) == (False, True, False)
    assert report["policies"]["jev"]["usage"] is None
