"""Matched rules/Jev comparisons derived exclusively from saved numerical evidence."""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING, Any

from carla_agentic_toolkit.experiment_metrics import METRIC_VERSION, summarize_trace
from carla_agentic_toolkit.experiment_trace import identity_digest, load_trace
from carla_agentic_toolkit.managed_names import normalize_merge_fixture, normalize_merge_spec
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.replicate_index import normalize_replicate_index

if TYPE_CHECKING:
    from collections.abc import Sequence

COMPARISON_VERSION = "matched-comparison-v1"
MAX_INPUT_RUNS = 200
MATCH_METADATA = (
    "fixture_version",
    "planner_version",
    "controller_version",
    "package_version",
    "code_sha256",
    "replicate_index",
    "environment",
)
MEAN_METRICS = (
    "collision_events",
    "collision_impulse_magnitude_sum_kg_mps",
    "lane_invasion_events",
    "hesitation_seconds",
    "reversal_count",
    "tracking_error_rmse_m",
    "interventions",
    "fallbacks",
    "decision_latency_mean_seconds",
    "decision_staleness_max_frames",
    "sensor_dropped_samples",
    "sensor_trailing_events",
    "real_time_factor",
)
DEFINITIONS = {
    "matching": (
        "Exact spec except policy/replay_run_id and exact fixture except actor_ids, "
        "plus matching replicate_index, environment, and "
        "package/code/planner/controller/fixture versions. "
        "Every cohort needs equal rules/Jev counts."
    ),
    "physical_denominator": (
        "Per-policy verified terminal runs with observations and successful cleanup. Partial, "
        "cancelled, infrastructure-invalid and insufficient-evidence runs are excluded, retained "
        "in sample/status/exclusion counts. Different attrition can bias comparison."
    ),
    "completion": "Explicit completed=true / eligible runs; unknown completion is excluded.",
    "observed_collision": (
        "Eligible runs with delivered collision events / eligible runs. Zero delivered events "
        "does not establish collision absence; inspect sensor coverage, drops and trailing data."
    ),
    "numeric_means": (
        "Unweighted mean of per-run metrics among eligible runs with that measurement; each "
        "metric includes its own measured-run denominator. Latency is a mean of per-run means."
    ),
    "usage": "Saved usage by run, or null when any included policy run has unknown usage.",
    "interpretation": (
        "Descriptive evidence only; no significance, improved realism, or safety claim. "
        "Interventions/fallbacks belong to the shared controller, not provider reasoning."
    ),
}


def compare_traces(paths: Sequence[Path]) -> dict[str, Any]:
    """Retain every distinct saved trial and fail closed on incomplete matching evidence."""
    if not 1 <= len(paths) <= MAX_INPUT_RUNS:
        message = f"Supply 1..{MAX_INPUT_RUNS} saved trace paths."
        raise ValueError(message)
    runs, blockers = _load_runs(paths)
    groups = _match_groups(runs)
    blockers.extend(_matching_blockers(groups))
    comparable = not blockers
    return {
        "schema_version": 1,
        "comparison_version": COMPARISON_VERSION,
        "metric_version": METRIC_VERSION,
        "comparable": comparable,
        "blockers": blockers,
        "warnings": _replicate_warnings(runs),
        "match_groups": groups,
        "runs": runs,
        "policies": {
            policy: _policy_summary(runs, policy, comparable=comparable)
            for policy in ("rules", "jev")
        },
        "definitions": DEFINITIONS,
    }


def _load_runs(paths: Sequence[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    runs: list[dict[str, Any]] = []
    blockers: list[str] = []
    identities: set[str] = set()
    for path in paths:
        run = _read_run(path)
        identity = str(run["run_id"] or path.resolve())
        if identity in identities:
            blockers.append(f"duplicate input run: {identity}")
            continue
        identities.add(identity)
        runs.append(run)
        blockers.extend(f"{identity}: {reason}" for reason in run["matching_errors"])
    return runs, blockers


def _read_run(path: Path) -> dict[str, Any]:
    read = load_trace(path)
    summary = summarize_trace(read)
    metadata = _one_data(read.events, "metadata")
    fixture = _one_data(read.events, "fixture")
    identity, errors = _match_identity(metadata, fixture)
    exclusions = _exclusions(summary, read.events)
    return {
        **summary,
        "source_path": str(path.resolve()),
        "policy": _saved_spec(metadata).get("policy"),
        "match_id": identity_digest(identity) if not errors else None,
        "matching_evidence": identity,
        "matching_errors": errors,
        "eligible": not exclusions,
        "exclusion_reasons": exclusions,
        "fixture": fixture,
        "decision_metadata": [
            event["data"].get("metadata")
            for event in read.events
            if event["kind"] == "decision_received"
        ],
    }


def _one_data(events: tuple[dict[str, Any], ...], kind: str) -> dict[str, Any]:
    matches = [event["data"] for event in events if event["kind"] == kind]
    return matches[0] if len(matches) == 1 else {}


def _match_identity(
    metadata: dict[str, Any],
    fixture: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    spec = metadata.get("spec")
    if not isinstance(spec, dict):
        return {}, ["missing or ambiguous reproduction metadata/spec"]
    try:
        metadata = normalize_replicate_index(metadata)
        spec = normalize_replicate_index(spec)
        canonical_spec = normalize_merge_spec(spec)
        fixture = normalize_merge_fixture(normalize_replicate_index(fixture))
    except ValueError as error:
        return {}, [f"invalid matching evidence: {error}"]
    errors = _identity_errors(metadata, spec, fixture)
    identity = {key: metadata.get(key) for key in MATCH_METADATA}
    identity["spec"] = _without(canonical_spec, {"policy", "replay_run_id"})
    identity["fixture"] = _without(fixture, {"actor_ids"})
    return identity, errors


def _identity_errors(
    metadata: dict[str, Any],
    spec: dict[str, Any],
    fixture: dict[str, Any],
) -> list[str]:
    errors = _metadata_errors(metadata, spec)
    if spec.get("policy") not in ("rules", "jev"):
        errors.append("comparison supports only saved rules and Jev trials")
    if not _fixture_complete(fixture):
        errors.append("missing or ambiguous exact fixture evidence")
    errors.extend(_replicate_errors(metadata, spec, fixture))
    return errors


def _replicate_errors(
    metadata: dict[str, Any], spec: dict[str, Any], fixture: dict[str, Any]
) -> list[str]:
    sources = {"metadata": metadata}
    if "replicate_index" in fixture:
        sources["fixture"] = fixture
    return [
        f"{name} replicate index differs from the saved spec"
        for name, source in sources.items()
        if source.get("replicate_index") != spec.get("replicate_index")
    ]


def _metadata_errors(metadata: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    errors = _environment_errors(metadata.get("environment"))
    if any(metadata.get(key) in (None, "", {}) for key in MATCH_METADATA):
        errors.append("missing required reproduction metadata")
    if not _valid_saved_spec(spec):
        errors.append("missing or invalid complete spec metadata")
    if metadata.get("policy_version") != spec.get("policy"):
        errors.append("saved policy version disagrees with spec metadata")
    return errors


def _environment_errors(environment: object) -> list[str]:
    if not isinstance(environment, dict):
        return ["missing environment metadata"]
    required = ("platform", "python", "carla_client", "carla_server")
    if any(not environment.get(key) for key in required):
        return ["incomplete environment metadata"]
    return []


def _fixture_complete(fixture: dict[str, Any]) -> bool:
    poses = ("policy_start", "target_start", "target_lane_start")
    return all(_pose_complete(fixture.get(key)) for key in poses) and bool(fixture.get("settings"))


def _pose_complete(pose: object) -> bool:
    return isinstance(pose, dict) and {"x", "y", "z", "yaw"} <= pose.keys()


def _valid_saved_spec(spec: dict[str, Any]) -> bool:
    try:
        historical_role = "ego_speed_mps" in spec and "controlled_vehicle_role" not in spec
        spec = normalize_merge_spec(normalize_replicate_index(spec))
        expected = ExperimentSpec.model_validate(spec).model_dump()
        if historical_role:
            expected.pop("controlled_vehicle_role")
    except ValueError:
        return False
    else:
        return expected == spec


def _saved_spec(metadata: dict[str, Any]) -> dict[str, Any]:
    spec = metadata.get("spec")
    return spec if isinstance(spec, dict) else {}


def _without(data: dict[str, Any], omitted: set[str]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if key not in omitted}


def _exclusions(summary: dict[str, Any], events: tuple[dict[str, Any], ...]) -> list[str]:
    reasons = []
    if summary["status"] in {"infrastructure_invalid", "partial", "cancelled"}:
        reasons.append(summary["status"])
    if not isinstance(summary["metrics"]["completed"], bool):
        reasons.append("unknown completion")
    if summary["coverage"]["observations"] == 0:
        reasons.append("no observations")
    if _one_data(events, "cleanup").get("ok") is not True:
        reasons.append("cleanup not verified")
    return reasons


def _match_groups(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"rules": [], "jev": []})
    for run in runs:
        if run["match_id"] is not None:
            groups[run["match_id"]][run["policy"]].append(run["run_id"])
    return [{"match_id": key, **policies} for key, policies in sorted(groups.items())]


def _matching_blockers(groups: list[dict[str, Any]]) -> list[str]:
    if not groups:
        return ["no complete matching cohorts"]
    return [
        f"missing or unequal policy counterpart for cohort {group['match_id']}"
        for group in groups
        if len(group["rules"]) != len(group["jev"])
    ]


def _replicate_warnings(runs: list[dict[str, Any]]) -> list[str]:
    indices: dict[str, set[int]] = defaultdict(set)
    for run in runs:
        if run["match_id"] is not None:
            evidence = run["matching_evidence"]
            context = _without(evidence, {"replicate_index"})
            context["spec"] = _without(evidence["spec"], {"replicate_index"})
            context["fixture"] = _without(evidence["fixture"], {"replicate_index"})
            indices[identity_digest(context)].add(evidence["replicate_index"])
    return [
        f"Replicate indices {sorted(values)} share the same recorded initial condition "
        f"in matching context {context}. Replicate labels do not sample independent "
        "initial conditions or guarantee bitwise-repeatable outcomes."
        for context, values in sorted(indices.items())
        if len(values) > 1
    ]


def _policy_summary(
    runs: list[dict[str, Any]],
    policy: str,
    *,
    comparable: bool,
) -> dict[str, Any]:
    trials = [run for run in runs if run["policy"] == policy]
    eligible = _eligible(trials)
    return {
        "sample_count": len(trials),
        "eligible_count": len(eligible),
        "excluded_count": len(trials) - len(eligible),
        "status_counts": dict(Counter(run["status"] for run in trials)),
        "metrics": _aggregate(eligible) if comparable else None,
        "usage": _usage(trials),
    }


def _eligible(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [run for run in runs if run["eligible"]]


def _aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = [run["metrics"] for run in runs]
    return {
        "completion": _rate(sum(item["completed"] is True for item in metrics), len(metrics)),
        "observed_collision": _rate(
            sum(item["collision_events"] > 0 for item in metrics),
            len(metrics),
        ),
        **{key: _mean(metrics, key) for key in MEAN_METRICS},
    }


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def _mean(metrics: list[dict[str, Any]], key: str) -> dict[str, Any]:
    values = [item[key] for item in metrics if item[key] is not None]
    return {"mean": fmean(values) if values else None, "denominator": len(values)}


def _usage(runs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    if not runs or any(run["usage"] is None for run in runs):
        return None
    return [{"run_id": run["run_id"], "usage": run["usage"]} for run in runs]


def write_comparison_report(paths: Sequence[Path], output: Path) -> dict[str, Path]:
    """Write reproducible JSON and inert HTML; source traces are never changed."""
    report = compare_traces(paths)
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    json_path = output / "comparison.json"
    html_path = output / "comparison.html"
    serialized = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
    json_path.write_text(serialized + "\n", encoding="utf-8")
    html_path.write_text(_render_report(report, serialized), encoding="utf-8")
    return {"summary": json_path, "report": html_path}


def _render_report(report: dict[str, Any], serialized: str) -> str:
    status = "Matched descriptive comparison" if report["comparable"] else "Comparison refused"
    rows = "".join(_policy_row(policy, values) for policy, values in report["policies"].items())
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>CARLA matched comparison</title><style>"
        "body{font:16px system-ui;max-width:1000px;margin:3rem auto;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{padding:.6rem;border-bottom:1px solid #ccc}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f7;padding:1rem}</style>"
        f"<h1>{status}</h1><p>{html.escape(DEFINITIONS['interpretation'])}</p>"
        "<table><tr><th>Policy</th><th>Samples</th><th>Eligible</th><th>Excluded</th></tr>"
        f"{rows}</table>{_metric_table(report)}<h2>Definitions and saved evidence</h2>"
        f"<pre>{html.escape(serialized)}</pre></html>"
    )


def _policy_row(policy: str, values: dict[str, Any]) -> str:
    return (
        f"<tr><td>{html.escape(policy)}</td><td>{values['sample_count']}</td>"
        f"<td>{values['eligible_count']}</td><td>{values['excluded_count']}</td></tr>"
    )


def _metric_table(report: dict[str, Any]) -> str:
    if not report["comparable"]:
        return "<p>Comparative metrics withheld. Inspect the blockers below.</p>"
    rules = report["policies"]["rules"]["metrics"]
    jev = report["policies"]["jev"]["metrics"]
    rows = "".join(_metric_row(key, rules[key], jev[key]) for key in rules)
    return (
        "<h2>Physical metrics</h2><table><tr><th>Metric</th><th>Rules</th><th>Jev</th></tr>"
        + rows
        + "</table>"
    )


def _metric_row(key: str, rules: dict[str, Any], jev: dict[str, Any]) -> str:
    cells = "".join(
        f"<td>{html.escape(json.dumps(value, sort_keys=True))}</td>" for value in (rules, jev)
    )
    return f"<tr><th>{html.escape(key)}</th>{cells}</tr>"


def main(argv: Sequence[str] | None = None) -> int:
    """Compare saved files without contacting CARLA or a provider."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory for static reports")
    parser.add_argument("traces", nargs="+", type=Path, help="Saved events.jsonl paths")
    args = parser.parse_args(argv)
    paths = write_comparison_report(args.traces, args.output)
    print(json.dumps({key: str(path) for key, path in paths.items()}))  # noqa: T201
    report = json.loads(paths["summary"].read_text(encoding="utf-8"))
    return 0 if report["comparable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
