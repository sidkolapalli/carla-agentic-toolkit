"""Route-specific descriptive metrics; prediction estimates are never collision probabilities."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING, TypeGuard

from carla_agentic_toolkit.trace_schema import json_object

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from carla_agentic_toolkit.models import JsonObject

ROUTE_METRIC_VERSION = "route-observed-metrics-v2"


def route_metrics(events: Sequence[Mapping[str, object]]) -> JsonObject:
    """Add route measurements only when the saved observations actually contain them."""
    observations = [json_object(event["data"]) for event in events if _route_observation(event)]
    if not observations:
        return {}
    return {
        "route_metric_version": ROUTE_METRIC_VERSION,
        "route_progress_m": observations[-1]["route_progress_m"],
        "route_goal_m": observations[-1]["route_length_m"],
        "route_minimum_observed_box_clearance_m": _minimum_clearance(observations),
        "route_accepted_choices_by_source": _choices(events),
        "route_local_guard_frames": _guard_counts(events),
        "route_hazard_trial": _hazard_trial(events),
    }


def _hazard_trial(events: Sequence[Mapping[str, object]]) -> JsonObject:
    for event in reversed(events):
        if event["kind"] not in {"fixture_summary", "execution"}:
            continue
        trial = json_object(event["data"]).get("hazard_trial")
        if isinstance(trial, dict):
            return trial
    return {"valid": None, "status": "unverified"}


def _route_observation(event: Mapping[str, object]) -> bool:
    return event["kind"] == "observation" and "route_progress_m" in json_object(event["data"])


def _choices(events: Sequence[Mapping[str, object]]) -> dict[object, dict[object, int]]:
    accepted = _accepted_revisions(events)
    counts: defaultdict[object, Counter[object]] = defaultdict(Counter)
    for event in events:
        if event["kind"] == "decision_received":
            decision = json_object(event["data"])
            if json_object(decision["context"])["revision"] in accepted:
                counts[decision["source"]][decision["choice_id"]] += 1
    return {source: dict(choices) for source, choices in counts.items()}


def _accepted(event: Mapping[str, object]) -> bool:
    return event["kind"] == "validation" and json_object(event["data"]).get("accepted") is True


def _accepted_revisions(events: Sequence[Mapping[str, object]]) -> set[object]:
    return {json_object(event["data"])["request_revision"] for event in events if _accepted(event)}


def _minimum_clearance(observations: list[JsonObject]) -> float | None:
    clearances = [
        _number(json_object(risk)["clearance_m"])
        for value in observations
        for risk in _iterable(value["traffic"])
    ]
    return min(clearances, default=None)


def _iterable(value: object) -> Iterable[object]:
    if not isinstance(value, Iterable):
        message = "Route trace traffic must be iterable."
        raise TypeError(message)
    return value


def _number(value: object) -> float:
    if not isinstance(value, int | float):
        message = "Route trace clearance must be numerical."
        raise TypeError(message)
    return value


def _guard_counts(events: Sequence[Mapping[str, object]]) -> dict[str, int]:
    reasons = Counter(
        json_object(json_object(event["data"])["intervention"]).get("reason")
        for event in events
        if event["kind"] == "execution"
    )
    return {reason: count for reason, count in reasons.items() if _guard_reason(reason)}


def _guard_reason(reason: object) -> TypeGuard[str]:
    return isinstance(reason, str) and reason in {"imminent_obstacle", "traffic_light_stop"}
