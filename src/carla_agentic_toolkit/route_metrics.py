"""Route-specific descriptive metrics; prediction estimates are never collision probabilities."""

from collections import Counter, defaultdict
from typing import Any

ROUTE_METRIC_VERSION = "route-observed-metrics-v1"


def route_metrics(events: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    """Add route measurements only when the saved observations actually contain them."""
    observations = [event["data"] for event in events if _route_observation(event)]
    if not observations:
        return {}
    return {
        "route_metric_version": ROUTE_METRIC_VERSION,
        "route_progress_m": observations[-1]["route_progress_m"],
        "route_goal_m": observations[-1]["route_length_m"],
        "route_minimum_observed_box_clearance_m": _minimum_clearance(observations),
        "route_accepted_choices_by_source": _choices(events),
        "route_local_guard_frames": _guard_counts(events),
    }


def _route_observation(event: dict[str, Any]) -> bool:
    return event["kind"] == "observation" and "route_progress_m" in event["data"]


def _choices(events: tuple[dict[str, Any], ...]) -> dict[str, dict[str, int]]:
    accepted = _accepted_revisions(events)
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        if event["kind"] == "decision_received":
            decision = event["data"]
            if decision["context"]["revision"] in accepted:
                counts[decision["source"]][decision["choice_id"]] += 1
    return {source: dict(choices) for source, choices in counts.items()}


def _accepted(event: dict[str, Any]) -> bool:
    return event["kind"] == "validation" and event["data"].get("accepted") is True


def _accepted_revisions(events: tuple[dict[str, Any], ...]) -> set[int]:
    return {event["data"]["request_revision"] for event in events if _accepted(event)}


def _minimum_clearance(observations: list[dict[str, Any]]) -> float | None:
    clearances = [risk["clearance_m"] for value in observations for risk in value["traffic"]]
    return min(clearances, default=None)


def _guard_counts(events: tuple[dict[str, Any], ...]) -> dict[str, int]:
    reasons = Counter(
        event["data"]["intervention"].get("reason")
        for event in events
        if event["kind"] == "execution"
    )
    return {
        reason: count
        for reason, count in reasons.items()
        if reason in {"imminent_obstacle", "traffic_light_stop"}
    }
