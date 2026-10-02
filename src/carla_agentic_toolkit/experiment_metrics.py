"""Versioned physical metrics and static reports derived only from saved trace events."""

from __future__ import annotations

import html
import json
import math
from collections import defaultdict
from itertools import pairwise
from statistics import fmean
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.experiment_trace import TraceRead

METRIC_VERSION = "physical-metrics-v1"
LOW_SPEED_MPS = 0.2
METRIC_DEFINITIONS = {
    "collision_events": "Count of delivered collision events; does not prove absence of collision.",
    "collision_impulse_magnitude_sum_kg_mps": "Sum of impulse-vector magnitudes in kg m/s.",
    "lane_invasion_events": "Count of delivered lane-invasion events with at least one marking.",
    "hesitation_seconds": "Actor-seconds between observations whose starting |speed| <=0.2 m/s.",
    "reversal_count": (
        "Sign changes in longitudinal_speed_mps, ignoring |speed| <=0.2 m/s samples; "
        "legacy traces fall back to speed_mps, which must be signed to represent reversals."
    ),
    "tracking_error_rmse_m": "Root mean square of recorded tracking_error_m; missing is null.",
    "interventions": "Count of intervention events; fallbacks count only explicit fallback=true.",
    "decision_latency_mean_seconds": "Mean recorded provider response latency_seconds.",
    "decision_staleness_max_frames": "Maximum recorded staleness_frames; missing is null.",
    "sensor_dropped_samples": "Sum of maximum cumulative dropped_samples per world/actor/sensor.",
    "sensor_trailing_events": "Count of sensor events explicitly labeled trailing=true.",
    "real_time_factor": "Per-world simulation duration / full trace monotonic wall duration.",
    "gap_ttc_assumptions": (
        "Gap and TTC require numerical positions, declared reference points, and positive closing "
        "speed along the same path. No TTC is inferred from semantic scores or unsigned speed. "
        "Crossing-path and acceleration predictions are outside this metric version."
    ),
}


def summarize_trace(read: TraceRead) -> dict[str, Any]:
    """Compute a compact, reproducible summary from the immutable saved event stream."""
    events = read.events
    observations = _of_kind(events, "observation")
    sensors = _of_kind(events, "sensor")
    decisions = _of_kind(events, "decision_received")
    outcomes = _of_kind(events, "outcome")
    return {
        "schema_version": 1,
        "metric_version": METRIC_VERSION,
        "run_id": events[0]["run_id"] if events else None,
        "status": _status(read, outcomes),
        "event_count": len(events),
        "last_sequence": events[-1]["sequence"] if events else None,
        "trace_errors": list(read.errors),
        "metadata": [event["data"] for event in _of_kind(events, "metadata")],
        "coverage": {"observations": len(observations), "sensor_events": len(sensors)},
        "metrics": _metrics(events, observations, sensors, decisions, outcomes),
        "usage": _usage(decisions),
        "definitions": METRIC_DEFINITIONS,
    }


def _metrics(
    events: tuple[dict[str, Any], ...],
    observations: list[dict[str, Any]],
    sensors: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Keep event counters distinct from numerical physical measurements."""
    return {
        **_sensor_metrics(sensors),
        **_motion_metrics(observations),
        **_decision_metrics(events, decisions),
        **_duration_metrics(events, observations),
        "completed": _completed(outcomes),
    }


def _of_kind(events: tuple[dict[str, Any], ...], kind: str) -> list[dict[str, Any]]:
    return [event for event in events if event["kind"] == kind]


def _status(read: TraceRead, outcomes: list[dict[str, Any]]) -> str:
    if not read.complete or _has_infrastructure_failure(read.events):
        return "infrastructure_invalid"
    if not outcomes:
        return "partial"
    return str(outcomes[-1]["data"].get("status", "finished"))


def _has_infrastructure_failure(events: tuple[dict[str, Any], ...]) -> bool:
    return any(_invalid_event(event) for event in events)


def _invalid_event(event: dict[str, Any]) -> bool:
    if event["kind"] in {"trace_interrupted", "infrastructure_error"}:
        return True
    if event["kind"] == "cleanup":
        return _failed_cleanup(event["data"])
    return event["data"].get("status") in {
        "infrastructure_invalid",
        "simulator_error",
        "cleanup_failed",
    }


def _failed_cleanup(data: dict[str, Any]) -> bool:
    return (
        data.get("ok") is False or bool(data.get("failures")) or bool(data.get("sensor_failures"))
    )


def _completed(outcomes: list[dict[str, Any]]) -> bool | None:
    if not outcomes:
        return None
    value = outcomes[-1]["data"].get("completed")
    return value if isinstance(value, bool) else None


def _sensor_metrics(sensors: list[dict[str, Any]]) -> dict[str, Any]:
    return {**_collision_metrics(sensors), **_sensor_delivery_metrics(sensors)}


def _collision_metrics(sensors: list[dict[str, Any]]) -> dict[str, Any]:
    collisions = [
        event for event in sensors if isinstance(event["data"].get("collision_impulse"), dict)
    ]
    return {
        "collision_events": len(collisions),
        "collision_impulse_magnitude_sum_kg_mps": sum(_impulse(event) for event in collisions),
    }


def _sensor_delivery_metrics(sensors: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "lane_invasion_events": sum(_lane_invasion(event) for event in sensors),
        "sensor_dropped_samples": _dropped_samples(sensors),
        "sensor_trailing_events": sum(event["data"].get("trailing") is True for event in sensors),
    }


def _impulse(event: dict[str, Any]) -> float:
    vector = event["data"]["collision_impulse"]
    values = [_number(vector.get(axis)) for axis in ("x", "y", "z")]
    if None in values:
        return 0.0
    return math.sqrt(sum(cast("float", value) ** 2 for value in values))


def _lane_invasion(event: dict[str, Any]) -> bool:
    value = event["data"].get("lane_invasion")
    if not isinstance(value, dict):
        return False
    return (_number(value.get("count")) or 0) > 0


def _dropped_samples(sensors: list[dict[str, Any]]) -> int:
    streams: dict[tuple[object, ...], int] = {}
    for event in sensors:
        identity = (event["world_generation"], event["actor_id"], event["data"].get("sensor_id"))
        dropped = _number(event["data"].get("dropped_samples")) or 0
        streams[identity] = max(streams.get(identity, 0), int(dropped))
    return sum(streams.values())


def _motion_metrics(observations: list[dict[str, Any]]) -> dict[str, Any]:
    groups = _actor_groups(observations)
    errors = _numbers(observations, "tracking_error_m")
    return {
        "hesitation_seconds": sum(_hesitation(group) for group in groups.values()),
        "reversal_count": sum(_reversals(group) for group in groups.values()),
        "tracking_error_rmse_m": math.sqrt(fmean(value * value for value in errors))
        if errors
        else None,
    }


def _actor_groups(events: list[dict[str, Any]]) -> dict[tuple[object, ...], list[dict[str, Any]]]:
    groups: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        groups[(event["world_generation"], event["actor_id"])].append(event)
    return groups


def _hesitation(group: list[dict[str, Any]]) -> float:
    return sum(_slow_interval(first, second) for first, second in pairwise(group))


def _slow_interval(first: dict[str, Any], second: dict[str, Any]) -> float:
    speed = _number(first["data"].get("speed_mps"))
    if speed is None or abs(speed) > LOW_SPEED_MPS:
        return 0.0
    first_time = _number(first["data"].get("simulation_seconds"))
    second_time = _number(second["data"].get("simulation_seconds"))
    if first_time is None or second_time is None:
        return 0.0
    return max(second_time - first_time, 0.0)


def _reversals(group: list[dict[str, Any]]) -> int:
    signed = [_longitudinal_speed(event["data"]) for event in group]
    speeds = _moving_speeds(signed)
    signs = [speed > 0 for speed in speeds]
    return sum(first != second for first, second in pairwise(signs))


def _longitudinal_speed(data: dict[str, Any]) -> float | None:
    return _number(data.get("longitudinal_speed_mps", data.get("speed_mps")))


def _moving_speeds(speeds: list[float | None]) -> list[float]:
    return [speed for speed in speeds if speed is not None and abs(speed) > LOW_SPEED_MPS]


def _decision_metrics(
    events: tuple[dict[str, Any], ...], decisions: list[dict[str, Any]]
) -> dict[str, Any]:
    interventions = _of_kind(events, "intervention")
    latency = _numbers(decisions, "latency_seconds")
    staleness = _numbers(decisions, "staleness_frames")
    return {
        "interventions": len(interventions),
        "fallbacks": sum(event["data"].get("fallback") is True for event in interventions),
        "decision_latency_mean_seconds": fmean(latency) if latency else None,
        "decision_staleness_max_frames": max(staleness) if staleness else None,
    }


def _duration_metrics(
    events: tuple[dict[str, Any], ...], observations: list[dict[str, Any]]
) -> dict[str, Any]:
    wall = _wall_duration(events)
    simulation = _simulation_duration(observations)
    return {
        "simulation_duration_seconds": simulation,
        "wall_duration_seconds": wall,
        "real_time_factor": _real_time_factor(simulation, wall),
    }


def _wall_duration(events: tuple[dict[str, Any], ...]) -> float | None:
    if not events[1:]:
        return None
    return max(float(events[-1]["monotonic_seconds"]) - float(events[0]["monotonic_seconds"]), 0.0)


def _simulation_duration(observations: list[dict[str, Any]]) -> float | None:
    worlds: dict[str, list[float]] = defaultdict(list)
    for event in observations:
        timestamp = _number(event["data"].get("simulation_seconds"))
        if timestamp is not None:
            worlds[event["world_generation"]].append(timestamp)
    return _world_durations(worlds)


def _world_durations(worlds: dict[str, list[float]]) -> float | None:
    if not worlds:
        return None
    return sum(max(values) - min(values) for values in worlds.values())


def _real_time_factor(simulation: float | None, wall: float | None) -> float | None:
    if simulation is None or wall is None or wall <= 0:
        return None
    return simulation / wall


def _usage(decisions: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    usage = [event["data"].get("usage") for event in decisions]
    if not usage or any(value is None for value in usage):
        return None
    return usage


def _numbers(events: list[dict[str, Any]], key: str) -> list[float]:
    values = (_number(event["data"].get(key)) for event in events)
    return [value for value in values if value is not None]


def _number(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(cast("int | float", value))
    return number if math.isfinite(number) else None


def write_trace_report(path: Path) -> dict[str, Path]:
    """Derive summary JSON and a self-contained escaped HTML report from saved evidence."""
    from carla_agentic_toolkit.experiment_trace import load_trace  # noqa: PLC0415

    summary = summarize_trace(load_trace(path))
    summary_path = path.with_name("summary.json")
    report_path = path.with_name("report.html")
    summary_path.write_text(
        json.dumps(summary, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    report_path.write_text(_report_html(summary), encoding="utf-8")
    return {"summary": summary_path, "report": report_path}


def _report_html(summary: dict[str, Any]) -> str:
    rows = "".join(_metric_row(key, value) for key, value in summary["metrics"].items())
    definitions = html.escape(json.dumps(summary["definitions"], indent=2))
    metadata = html.escape(json.dumps(summary["metadata"], indent=2))
    run_id = html.escape(str(summary["run_id"]))
    status = html.escape(str(summary["status"]))
    return (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>CARLA experiment report</title><style>"
        "body{font:16px system-ui;max-width:900px;margin:3rem auto;padding:0 1rem;color:#18232d}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{padding:.65rem;border-bottom:1px solid #ddd;text-align:left}"
        "pre{white-space:pre-wrap;background:#f4f6f7;padding:1rem}h1{font-size:2rem}</style>"
        f"<h1>CARLA experiment</h1><p>Run: {run_id}</p><p>Status: <strong>{status}</strong></p>"
        f"<p>Recorded events: {summary['event_count']}</p>"
        f"<table><tr><th>Metric</th><th>Value</th></tr>{rows}</table>"
        "<h2>Definitions and assumptions</h2><p>Missing measurements are unknown. "
        "No comparative performance claim is made by this report.</p>"
        f"<pre>{definitions}</pre><h2>Reproduction metadata</h2><pre>{metadata}</pre></html>"
    )


def _metric_row(key: str, value: object) -> str:
    label = html.escape(key.replace("_", " ").capitalize())
    rendered = "Unknown" if value is None else html.escape(str(value))
    return f"<tr><td>{label}</td><td>{rendered}</td></tr>"
