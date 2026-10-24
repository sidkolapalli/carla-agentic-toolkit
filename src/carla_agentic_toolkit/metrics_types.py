"""Typed shapes of saved-trace summaries and matched rules/Jev comparison reports."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from carla_agentic_toolkit.models import JsonObject

# A validated saved trace envelope; read-only so any concrete event type is accepted.
type TraceEvent = Mapping[str, object]
type TraceEvents = Sequence[TraceEvent]


class TraceCoverage(TypedDict):
    """Counts of the evidence a trace summary was derived from."""

    observations: int
    sensor_events: int


class TraceSummary(TypedDict):
    """Compact reproducible summary of one saved event stream."""

    schema_version: int
    metric_version: str
    run_id: object
    status: str
    event_count: int
    last_sequence: object
    trace_errors: list[str]
    metadata: list[JsonObject]
    coverage: TraceCoverage
    metrics: JsonObject
    usage: list[object] | None
    definitions: dict[str, str]


class MatchingEvidence(TypedDict, total=False):
    """Saved reproduction evidence that must match exactly across compared runs."""

    fixture_version: object
    planner_version: object
    controller_version: object
    package_version: object
    code_sha256: object
    replicate_index: object
    environment: object
    spec: JsonObject
    fixture: JsonObject


class ComparisonRun(TraceSummary):
    """One saved trial with its matching identity and eligibility evidence."""

    source_path: str
    policy: object
    match_id: str | None
    matching_evidence: MatchingEvidence
    matching_errors: list[str]
    eligible: bool
    exclusion_reasons: list[str]
    fixture: JsonObject
    decision_metadata: list[object]


class MatchGroup(TypedDict):
    """Run identities sharing one exact matching identity, by policy."""

    match_id: str
    rules: list[object]
    jev: list[object]


class RateMetric(TypedDict):
    """A counted proportion with its explicit denominator."""

    numerator: int
    denominator: int
    rate: float | None


class MeanMetric(TypedDict):
    """An unweighted mean over the runs that measured the metric."""

    mean: float | None
    denominator: int


type AggregateMetrics = dict[str, RateMetric | MeanMetric]


class RunUsage(TypedDict):
    """Saved provider usage of one run."""

    run_id: object
    usage: list[object]


class PolicySummary(TypedDict):
    """Sample, eligibility, and aggregate evidence for one policy."""

    sample_count: int
    eligible_count: int
    excluded_count: int
    status_counts: dict[str, int]
    metrics: AggregateMetrics | None
    usage: list[RunUsage] | None


class ComparisonReport(TypedDict):
    """Matched descriptive comparison, or its refusal with blockers."""

    schema_version: int
    comparison_version: str
    metric_version: str
    comparable: bool
    blockers: list[str]
    warnings: list[str]
    match_groups: list[MatchGroup]
    runs: list[ComparisonRun]
    policies: dict[str, PolicySummary]
    definitions: dict[str, str]


def event_data(event: TraceEvent) -> JsonObject:
    """Return the object payload that trace validation guarantees for every saved event."""
    data = event["data"]
    if isinstance(data, dict):
        return data
    message = "Trace data must be an object."
    raise TypeError(message)


def metadata_identity(metadata: JsonObject) -> MatchingEvidence:
    """Copy the comparison MATCH_METADATA fields, in that order, as typed matching evidence."""
    return {
        "fixture_version": metadata.get("fixture_version"),
        "planner_version": metadata.get("planner_version"),
        "controller_version": metadata.get("controller_version"),
        "package_version": metadata.get("package_version"),
        "code_sha256": metadata.get("code_sha256"),
        "replicate_index": metadata.get("replicate_index"),
        "environment": metadata.get("environment"),
    }


def measured_number(value: object) -> float:
    """Narrow a saved per-run metric that summarize_trace produced as a number."""
    if isinstance(value, int | float):
        return value
    message = "Saved per-run metric is not numerical."
    raise TypeError(message)
