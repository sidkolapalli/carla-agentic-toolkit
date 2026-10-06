"""Export allowlisted route evidence and compute descriptive, reproducible study statistics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING, Any

from carla_agentic_toolkit.experiment_trace import load_trace, summarize_trace

if TYPE_CHECKING:
    from collections.abc import Sequence

STUDY_VERSION = "route-descriptive-study-v1"
STOPPED_MPS = 0.4
MIN_SAMPLES = 2
SCENARIOS = ("lead_brake", "cut_in", "pedestrian_crossing")


def percentile(values: Sequence[float], fraction: float) -> float | None:
    """Use linear interpolation at (n-1)*p, with no distributional inference."""
    if not 0 <= fraction <= 1 or any(not math.isfinite(value) for value in values):
        message = "Percentiles require finite data and a fraction in [0, 1]."
        raise ValueError(message)
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def intervals(samples: Sequence[dict[str, Any]]) -> list[float]:
    """Assign each state/control the observed interval until the next state, never beyond it."""
    times = [float(row["time_s"]) for row in samples]
    if len(times) < MIN_SAMPLES or any(not math.isfinite(value) for value in times):
        message = "At least two finite time samples are required."
        raise ValueError(message)
    durations = [second - first for first, second in pairwise(times)]
    if any(value <= 0 for value in durations):
        message = "Observation time must increase strictly."
        raise ValueError(message)
    return [*durations, 0.0]


def frame_pairs(events: Sequence[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Require one observation and execution at exactly the same frame, without interpolation."""
    observations = _frame_index(events, "observation")
    executions = _frame_index(events, "execution")
    if observations.keys() != executions.keys():
        message = "Observation and execution frame sets differ."
        raise ValueError(message)
    return [(value, executions[frame]) for frame, value in observations.items()]


def _frame_index(events: Sequence[dict[str, Any]], kind: str) -> dict[int, dict[str, Any]]:
    records = [event for event in events if event["kind"] == kind]
    result = {event["frame"]: event["data"] for event in records}
    if len(result) != len(records):
        message = f"Duplicate {kind} frame."
        raise ValueError(message)
    if any(type(frame) is not int or data["frame"] != frame for frame, data in result.items()):
        message = "Envelope and payload frame identities differ."
        raise ValueError(message)
    return result


def _duration_where(
    samples: list[dict[str, Any]], weights: list[float], key: str, value: object
) -> float:
    return math.fsum(
        weight for row, weight in zip(samples, weights, strict=True) if row[key] == value
    )


def _minimum(samples: list[dict[str, Any]], key: str) -> float | None:
    return min((row[key] for row in samples if row[key] is not None), default=None)


def _decision_statistics(decisions: list[dict[str, Any]], trigger: float) -> dict[str, Any]:
    accepted = [row for row in decisions if row["accepted"] and row["source"] == "jev"]
    fallback = [row for row in decisions if row["source"] == "fallback"]
    latency = [row["latency_ms"] for row in decisions if row["latency_ms"] is not None]
    before = [row for row in decisions if row["time_s"] < trigger]
    after = [row for row in decisions if row["time_s"] >= trigger]
    return {
        "accepted_jev_choices": dict(Counter(row["choice"] for row in accepted)),
        "provider_fallbacks": dict(Counter(row["reason"] for row in fallback)),
        "provider_attempts": sum(row["attempts"] for row in decisions),
        "rejected_decisions": sum(not row["accepted"] for row in decisions),
        "latency_samples": len(latency),
        "latency_mean_ms": math.fsum(latency) / len(latency) if latency else None,
        "latency_p50_ms": percentile(latency, 0.5),
        "latency_p95_ms": percentile(latency, 0.95),
        "latency_max_ms": max(latency, default=None),
        "last_pre_trigger": _relative_decision(before[-1], trigger) if before else None,
        "first_post_trigger": _relative_decision(after[0], trigger) if after else None,
    }


def _relative_decision(row: dict[str, Any], trigger: float) -> dict[str, Any]:
    return {**row, "after_trigger_s": row["time_s"] - trigger}


def _tactic_exposure(samples: list[dict[str, Any]], weights: list[float]) -> dict[str, float]:
    totals: dict[str, list[float]] = defaultdict(list)
    for row, weight in zip(samples, weights, strict=True):
        totals[f"{row['choice_source']}:{row['requested_choice']}"].append(weight)
    return {key: math.fsum(values) for key, values in totals.items()}


def analyze_trial(trial: dict[str, Any]) -> dict[str, Any]:
    """Describe one observed run; frame samples and calls are not independent driving trials."""
    samples = trial["samples"]
    weights = intervals(samples)
    elapsed = math.fsum(weights)
    errors = [row["tracking_error_m"] for row in samples]
    accelerations = [
        (second["speed_mps"] - first["speed_mps"]) / weight
        for (first, second), weight in zip(pairwise(samples), weights[:-1], strict=True)
    ]
    signal = _duration_where(samples, weights, "intervention_reason", "traffic_light_stop")
    return {
        "run_id": trial["run_id"],
        "scenario": trial["scenario"],
        "samples": len(samples),
        "elapsed_s": elapsed,
        "final_progress_m": samples[-1]["progress_m"],
        "mean_observed_speed_mps": math.fsum(
            row["speed_mps"] * dt for row, dt in zip(samples, weights, strict=True)
        )
        / elapsed,
        "stopped_s": math.fsum(
            dt for row, dt in zip(samples, weights, strict=True) if row["speed_mps"] < STOPPED_MPS
        ),
        "signal_guard_s": signal,
        "signal_guard_fraction": signal / elapsed,
        "emergency_guard_s": _duration_where(
            samples, weights, "intervention_reason", "imminent_obstacle"
        ),
        "fallback_exposure_s": _duration_where(samples, weights, "choice_source", "fallback"),
        "tactic_exposure_s": _tactic_exposure(samples, weights),
        "hazard_active_s": _duration_where(samples, weights, "hazard_active", value=True),
        "hazard_min_clearance_m": _minimum(samples, "hazard_clearance_m"),
        "all_traffic_min_clearance_m": _minimum(samples, "all_traffic_clearance_m"),
        "hazard_observed_s": math.fsum(
            dt
            for row, dt in zip(samples, weights, strict=True)
            if row["hazard_clearance_m"] is not None
        ),
        "tracking_rmse_m": math.sqrt(math.fsum(value * value for value in errors) / len(errors)),
        "tracking_p95_m": percentile(errors, 0.95),
        "tracking_max_m": max(errors),
        "observed_acceleration_min_mps2": min(accelerations),
        "observed_acceleration_max_mps2": max(accelerations),
        "completed": trial["outcome"]["completed"],
        "cleanup_ok": trial["cleanup_ok"],
        "collision_events": trial["collision_events"],
        **_decision_statistics(trial["decisions"], trial["trigger_s"]),
    }


def _sample(
    value: dict[str, Any], execution: dict[str, Any], origin: float, hazard_id: int
) -> dict[str, Any]:
    hazard = next(
        (risk for risk in value["traffic"] if risk["actor"]["actor_id"] == hazard_id), None
    )
    actor = hazard["actor"] if hazard else {}
    return {
        "frame": value["frame"],
        "time_s": value["simulation_seconds"] - origin,
        "speed_mps": value["speed_mps"],
        "progress_m": value["route_progress_m"],
        "x": value["policy"]["x"],
        "y": value["policy"]["y"],
        "tracking_error_m": value["tracking_error_m"],
        "traffic_light": value["traffic_light"],
        "hazard_active": execution["hazard"]["active"],
        "hazard_speed_mps": actor.get("speed_mps"),
        "hazard_lateral_m": actor.get("lateral_m"),
        "hazard_progress_m": actor.get("progress_m"),
        "hazard_x": actor.get("x"),
        "hazard_y": actor.get("y"),
        "hazard_clearance_m": hazard["clearance_m"] if hazard else None,
        "hazard_ahead_gap_m": hazard["ahead_gap_m"] if hazard else None,
        "hazard_predicted_overlap_s": hazard["predicted_overlap_seconds"] if hazard else None,
        "all_traffic_clearance_m": min(
            (risk["clearance_m"] for risk in value["traffic"]), default=None
        ),
        "choice_source": execution["choice_source"],
        "requested_choice": execution["requested_choice"],
        "executed_choice": execution["executed_choice"],
        "intervention_reason": execution["intervention"].get("reason"),
        **execution["controls"]["policy"],
    }


def _decisions(
    events: Sequence[dict[str, Any]], samples: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    validations = {
        event["data"]["request_revision"]: event["data"]["accepted"]
        for event in events
        if event["kind"] == "validation"
    }
    times = {row["frame"]: row["time_s"] for row in samples}
    rows = []
    for event in events:
        if event["kind"] != "decision_received":
            continue
        data = event["data"]
        revision = data["context"]["revision"]
        rows.append(
            {
                "frame": event["frame"],
                "revision": revision,
                "time_s": times[event["frame"]],
                "choice": data["choice_id"],
                "source": data["source"],
                "accepted": validations[revision],
                "reason": data["reason"],
                "latency_ms": None
                if data["latency_seconds"] is None
                else data["latency_seconds"] * 1000,
                "attempts": data["metadata"].get("attempts", 0),
                "input_tokens": data["metadata"].get("input_tokens"),
                "output_tokens": data["metadata"].get("output_tokens"),
            }
        )
    return rows


def project_trial(path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    """Pin private trace bytes to the published cohort before exporting numerical fields."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected["trace_sha256"]:
        message = "Trace hash differs from the retained cohort."
        raise ValueError(message)
    read = load_trace(path)
    if (
        not read.complete
        or read.errors
        or any(event["run_id"] != expected["run_id"] for event in read.events)
    ):
        message = "A complete, valid trace with the expected run identity is required."
        raise ValueError(message)
    meta = next(event["data"] for event in read.events if event["kind"] == "metadata")
    fixture = next(event["data"] for event in read.events if event["kind"] == "fixture")
    if (
        meta["code_sha256"] != expected["code_sha256"]
        or fixture["scenario"] != expected["scenario"]
    ):
        message = "Implementation or scenario identity differs from the retained cohort."
        raise ValueError(message)
    pairs = frame_pairs(read.events)
    _verify_frame_axis(pairs, expected)
    origin = pairs[0][0]["simulation_seconds"]
    trigger = next(
        execution["hazard"]["started_seconds"]
        for _, execution in pairs
        if execution["hazard"]["started_seconds"] is not None
    )
    role = "pedestrian" if fixture["scenario"] == "pedestrian_crossing" else "lead"
    samples = [
        _sample(value, execution, origin, fixture["actor_ids"][role]) for value, execution in pairs
    ]
    intervals(samples)
    metrics = summarize_trace(read)["metrics"]
    outcome = next(event["data"] for event in read.events if event["kind"] == "outcome")
    cleanup = next(event["data"] for event in read.events if event["kind"] == "cleanup")
    if outcome != expected["outcome"] or cleanup["ok"] != expected["cleanup_ok"]:
        message = "Recorded outcome or cleanup differs from the retained cohort."
        raise ValueError(message)
    return {
        "run_id": expected["run_id"],
        "scenario": expected["scenario"],
        "trace_sha256": digest,
        "code_sha256": expected["code_sha256"],
        "spec": expected["spec"],
        "environment": expected["environment"],
        "outcome": outcome,
        "cleanup_ok": cleanup["ok"],
        "actors_restored": expected["actors_restored"],
        "settings_restored": expected["settings_restored"],
        "collision_events": metrics["collision_events"],
        "hazard_role": role,
        "hazard_actor_id": fixture["actor_ids"][role],
        "trigger_s": trigger - origin,
        "route_goal_m": fixture["route_goal_m"],
        "lane_width_m": fixture["route"][0]["width_m"],
        "route": [{"x": point["x"], "y": point["y"]} for point in fixture["route"]],
        "samples": samples,
        "decisions": _decisions(read.events, samples),
    }


def _verify_frame_axis(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]], expected: dict[str, Any]
) -> None:
    frames = [value["frame"] for value, _ in pairs]
    if len(frames) != expected["observations"] or any(
        second != first + 1 for first, second in pairwise(frames)
    ):
        message = "The study requires every consecutive observation frame from the retained run."
        raise ValueError(message)


def project_cohort(cohort: Path, runs: Path) -> dict[str, Any]:
    """Export the three final Jev trials and retain calibration provenance separately."""
    source = json.loads(cohort.read_text(encoding="utf-8"))
    expected = source["jev_trials"]
    if sorted(row["scenario"] for row in expected) != sorted(SCENARIOS):
        message = "Supply the retained three-scenario cohort."
        raise ValueError(message)
    if any(re.fullmatch(r"[a-f0-9]{32}", row["run_id"]) is None for row in expected):
        message = "Invalid run identity."
        raise ValueError(message)
    return {
        "study_version": STUDY_VERSION,
        "cohort_sha256": hashlib.sha256(cohort.read_bytes()).hexdigest(),
        "trials": [project_trial(runs / row["run_id"] / "events.jsonl", row) for row in expected],
        "calibration": [
            {key: row[key] for key in ("label", "run_id", "code_sha256", "outcome", "cleanup_ok")}
            for row in source["calibration_trials"]
        ],
        "limits": source["limits"],
    }


def scenario_fidelity(trial: dict[str, Any]) -> dict[str, Any]:
    """Measure actor motion independently of the commanded scenario and route completion."""
    rows = trial["samples"]
    active = [
        (first, second)
        for first, second in pairwise(rows)
        if first["hazard_active"]
        and first["hazard_x"] is not None
        and second["hazard_x"] is not None
    ]
    lengths = [
        math.hypot(second["hazard_x"] - first["hazard_x"], second["hazard_y"] - first["hazard_y"])
        for first, second in active
    ]
    speeds = [
        length / (second["time_s"] - first["time_s"])
        for length, (first, second) in zip(lengths, active, strict=True)
    ]
    visible = [
        row
        for row in rows
        if row["time_s"] >= trial["trigger_s"] and row["hazard_lateral_m"] is not None
    ]
    entry = next(
        (row for row in visible if abs(row["hazard_lateral_m"]) <= trial["lane_width_m"] / 2), None
    )
    crossed = bool(visible) and any(
        row["hazard_lateral_m"] * visible[0]["hazard_lateral_m"] <= 0 for row in visible
    )
    return {
        "active_intervals": len(active),
        "active_path_length_m": math.fsum(lengths),
        "active_position_speed_median_mps": percentile(speeds, 0.5),
        "active_reported_speed_median_mps": percentile(
            [first["hazard_speed_mps"] for first, _second in active], 0.5
        ),
        "entered_route_lane": entry is not None,
        "first_lane_entry_after_trigger_s": entry["time_s"] - trial["trigger_s"] if entry else None,
        "crossed_route_centreline": crossed,
        "active_start_lateral_m": active[0][0]["hazard_lateral_m"] if active else None,
        "active_end_lateral_m": active[-1][1]["hazard_lateral_m"] if active else None,
    }


def analyze_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    """Summarize runs separately; aggregate only the recorded provider workload."""
    trials = [
        analyze_trial(row) | {"scenario_fidelity": scenario_fidelity(row)}
        for row in dataset["trials"]
    ]
    latencies = [
        decision["latency_ms"]
        for row in dataset["trials"]
        for decision in row["decisions"]
        if decision["latency_ms"] is not None
    ]
    return {
        "study_version": STUDY_VERSION,
        "cohort_sha256": dataset["cohort_sha256"],
        "data_fingerprint": hashlib.sha256(
            json.dumps(dataset, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
        "trials": trials,
        "workload": {
            "provider_attempts": sum(row["provider_attempts"] for row in trials),
            "accepted_jev_decisions": sum(
                sum(row["accepted_jev_choices"].values()) for row in trials
            ),
            "provider_fallbacks": sum(sum(row["provider_fallbacks"].values()) for row in trials),
            "latency_p50_ms": percentile(latencies, 0.5),
            "latency_p95_ms": percentile(latencies, 0.95),
            "latency_max_ms": max(latencies, default=None),
        },
        "inference": (
            "Descriptive only: one run per scenario; frames and calls are correlated within runs."
        ),
    }


def main() -> None:
    """Project private traces, then recompute statistics from portable numerical data."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("project", "analyze"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=Path)
    options = parser.parse_args()
    if options.operation == "project":
        if options.runs is None:
            parser.error("Projection requires --runs pointing to private saved runs.")
        result = project_cohort(options.input, options.runs)
    else:
        result = analyze_dataset(json.loads(options.input.read_text(encoding="utf-8")))
    options.output.parent.mkdir(parents=True, exist_ok=True)
    with options.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
