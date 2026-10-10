"""Replicate labels remain strict matching evidence, not sampled initial conditions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import pytest

from carla_agentic_toolkit import experiment_comparison as comparison
from carla_agentic_toolkit.experiment_trace import TraceStore
from carla_agentic_toolkit.managed_spec import ExperimentSpec

if TYPE_CHECKING:
    from pathlib import Path

POLICIES: tuple[Literal["rules"], Literal["jev"]] = ("rules", "jev")


def _save(
    root: Path,
    name: str,
    policy: Literal["rules", "jev"],
    index: int,
    **options: Any,  # noqa: ANN401
) -> Path:
    sections = _evidence(name, policy, index, options)
    with TraceStore(name, root=root) as store:
        for kind in ("metadata", "fixture"):
            _append(store, kind, sections[kind])
        _append(store, "observation", {"speed_mps": 1.0, "simulation_seconds": 0.0})
        _append(store, "observation", {"speed_mps": 1.0, "simulation_seconds": 1.0})
        if options.get("collision"):
            _append(store, "sensor", {"collision_impulse": {"x": 3.0, "y": 4.0, "z": 0.0}})
        _append(store, "outcome", {"completed": True, "status": "completed"})
        _append(store, "cleanup", {"ok": True})
    return store.path


def _evidence(
    name: str, policy: Literal["rules", "jev"], index: int, options: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    label = options.get("label", "replicate_index")
    spec = ExperimentSpec(policy=policy).model_dump()
    spec.pop("seed", None)
    spec.pop("replicate_index", None)
    spec[label] = index
    metadata = {
        "spec": spec,
        "fixture_version": spec["fixture"],
        "policy_version": policy,
        "planner_version": "planner-v1",
        "controller_version": "controller-v1",
        "package_version": "0.1.0",
        "code_sha256": "a" * 64,
        label: index,
        "environment": {
            "platform": "linux",
            "python": "3.12",
            "carla_client": "0.9.16",
            "carla_server": "0.9.16",
        },
    }
    fixture = {
        "policy_start": {"x": 1.0, "y": 2.0, "z": 0.5, "yaw": 0.0},
        "ego_start": {"x": 25.0, "y": 5.5, "z": 0.5, "yaw": 0.0},
        "target_start": {"x": 1.0, "y": 5.5, "z": 0.5, "yaw": 0.0},
        "settings": {"target_speed_mps": 6.0},
        "vehicle_blueprint": "vehicle.lincoln.mkz_2020",
        "actor_ids": {"policy": len(name)},
        label: index,
    }
    sections = {"metadata": metadata, "spec": spec, "fixture": fixture}
    for section, updates in options.get("changes", {}).items():
        sections[section].update(updates)
    omit = options.get("omit")
    if omit is not None:
        sections[omit[0]].pop(omit[1])
    return sections


def _append(store: TraceStore, kind: str, data: dict[str, Any]) -> None:
    store.append(kind, world_generation="world", frame=1, actor_id=7, data=data)


@pytest.mark.parametrize(
    "labels",
    [
        ("seed", "seed"),
        ("seed", "replicate_index"),
        ("replicate_index", "seed"),
        ("replicate_index", "replicate_index"),
    ],
)
def test_old_and_new_labels_match_without_rewriting_trace_bytes(
    tmp_path: Path, labels: tuple[str, str]
) -> None:
    """Normalize the three label locations, never the source evidence or other fields."""
    paths = [
        _save(tmp_path, policy, policy, 7, label=label)
        for policy, label in zip(POLICIES, labels, strict=True)
    ]
    original = _trace_bytes(paths)
    report = comparison.compare_traces(paths)
    assert (report["comparable"], report["blockers"]) == (True, [])
    identity = report["runs"][0]["matching_evidence"]
    assert _replicate_labels(identity) == [7, 7, 7]
    assert _trace_bytes(paths) == original


def _trace_bytes(paths: list[Path]) -> list[bytes]:
    return [path.read_bytes() for path in paths]


def _replicate_labels(identity: dict[str, Any]) -> list[int]:
    return [
        section["replicate_index"] for section in (identity, identity["spec"], identity["fixture"])
    ]


@pytest.mark.parametrize("section", ["metadata", "spec", "fixture"])
@pytest.mark.parametrize(
    "labels",
    [
        {"seed": 8},
        {"seed": True, "replicate_index": 1},
        {"seed": False, "replicate_index": 0},
        {"seed": "7"},
        {"replicate_index": True},
        {"replicate_index": -1},
        {"replicate_index": 2**31},
    ],
)
def test_invalid_or_conflicting_labels_cannot_form_a_cohort(
    tmp_path: Path, section: str, labels: dict[str, object]
) -> None:
    """Equal Python bool/int values and fixture labels cannot bypass strict aliases."""
    paths = [_save(tmp_path, policy, policy, 7, changes={section: labels}) for policy in POLICIES]
    report = comparison.compare_traces(paths)
    assert report["comparable"] is False
    assert report["blockers"]


@pytest.mark.parametrize(
    ("section", "key"),
    [("metadata", "replicate_index"), ("spec", "fixed_delta_seconds"), ("fixture", "ego_start")],
)
def test_alias_normalization_does_not_supply_missing_evidence(
    tmp_path: Path, section: str, key: str
) -> None:
    """A matching absence is not a complete spec, version, label, or initial pose."""
    paths = [_save(tmp_path, policy, policy, 7, omit=(section, key)) for policy in POLICIES]
    assert comparison.compare_traces(paths)["comparable"] is False


@pytest.mark.parametrize("section", ["metadata", "spec", "fixture"])
def test_matching_strict_dual_labels_are_accepted(tmp_path: Path, section: str) -> None:
    """A fully known equal legacy alias does not change canonical matching evidence."""
    paths = [
        _save(tmp_path, policy, policy, 7, changes={section: {"seed": 7}}) for policy in POLICIES
    ]
    assert comparison.compare_traces(paths)["comparable"] is True


def test_unknown_spec_fields_remain_invalid_after_normalization(tmp_path: Path) -> None:
    """The migration renames one key instead of relaxing complete-spec comparison."""
    paths = [
        _save(tmp_path, policy, policy, 7, label="seed", changes={"spec": {"unreviewed_field": 7}})
        for policy in POLICIES
    ]
    assert comparison.compare_traces(paths)["comparable"] is False


@pytest.mark.parametrize("section", ["metadata", "spec", "fixture"])
def test_present_replicate_labels_agree_with_saved_spec(tmp_path: Path, section: str) -> None:
    """A valid scalar label is still false evidence when it names another replicate."""
    paths = [
        _save(tmp_path, policy, policy, 7, changes={section: {"replicate_index": 8}})
        for policy in POLICIES
    ]
    assert comparison.compare_traces(paths)["comparable"] is False


def test_legacy_fixture_without_replicate_label_remains_compatible(tmp_path: Path) -> None:
    """Previously valid fixtures without a redundant label keep their exact matching gate."""
    paths = [
        _save(tmp_path, policy, policy, 7, label="seed", omit=("fixture", "seed"))
        for policy in POLICIES
    ]
    assert comparison.compare_traces(paths)["comparable"] is True


def test_different_replicates_still_require_their_own_policy_counterparts(tmp_path: Path) -> None:
    """A shared initial condition cannot substitute for matched replicate indices."""
    paths = [_save(tmp_path, "rules-seven", "rules", 7), _save(tmp_path, "jev-nineteen", "jev", 19)]
    report = comparison.compare_traces(paths)
    assert report["comparable"] is False
    assert any("counterpart" in blocker for blocker in report["blockers"])


@pytest.mark.parametrize(
    ("second_index", "changes", "expected_warning"),
    [
        (7, {}, False),
        (19, {}, True),
        (19, {"fixture": {"policy_start": {"x": 2.0, "y": 2.0, "z": 0.5, "yaw": 0.0}}}, False),
        (19, {"fixture": {"settings": {"target_speed_mps": 7.0}}}, False),
        (19, {"spec": {"timing_mode": "paced"}}, False),
        (19, {"metadata": {"code_sha256": "b" * 64}}, False),
        (
            19,
            {
                "metadata": {
                    "environment": {
                        "platform": "windows",
                        "python": "3.12",
                        "carla_client": "0.9.16",
                        "carla_server": "0.9.16",
                    }
                }
            },
            False,
        ),
    ],
)
def test_initial_condition_warning_is_nonblocking_and_context_specific(
    tmp_path: Path, second_index: int, changes: dict[str, Any], *, expected_warning: bool
) -> None:
    """Distinct labels, not equal metrics or same-index policy pairs, trigger the warning."""
    paths = [_save(tmp_path, f"first-{policy}", policy, 7, label="seed") for policy in POLICIES]
    paths.extend(
        _save(tmp_path, f"second-{policy}", policy, second_index, changes=changes, collision=True)
        for policy in POLICIES
    )
    report = comparison.compare_traces(paths)
    assert (report["comparable"], report["blockers"]) == (True, [])
    assert bool(report["warnings"]) is expected_warning


def test_initial_condition_warning_is_visible_in_static_report(tmp_path: Path) -> None:
    """The descriptive warning remains visible without withholding matched metrics."""
    paths = [
        _save(tmp_path, f"{index}-{policy}", policy, index)
        for index in (7, 19)
        for policy in POLICIES
    ]
    output = tmp_path / "report"
    comparison.write_comparison_report(paths, output)
    rendered = (output / "comparison.html").read_text(encoding="utf-8")
    assert "same recorded initial condition" in rendered
