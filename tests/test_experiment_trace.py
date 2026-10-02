"""TDD contracts for immutable numerical evidence and derived experiment reports."""

from __future__ import annotations

import json
import math
import os
from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit import experiment_trace as trace

if TYPE_CHECKING:
    from pathlib import Path


def test_trace_orders_events_and_preserves_numerical_evidence(tmp_path: Path) -> None:
    """Every event has stable identity and exact original numerical payloads."""
    with trace.TraceStore("run-a", root=tmp_path) as store:
        first = store.append("metadata", world_generation="world-1", frame=None, data={"seed": 7})
        second = store.append(
            "observation",
            world_generation="world-1",
            frame=42,
            actor_id=9,
            data={"speed_mps": 4.125, "features": {"schema_version": 1, "gap_m": 6.25}},
        )
    read = trace.load_trace(store.path)

    assert [event["sequence"] for event in read.events] == [1, 2]
    assert read.events == (first, second)
    assert read.complete is True
    assert second["data"] == {"speed_mps": 4.125, "features": {"schema_version": 1, "gap_m": 6.25}}


def test_trace_refuses_run_reuse_and_secret_or_nonfinite_payloads(tmp_path: Path) -> None:
    """Neither existing evidence nor secret-bearing/invalid new values may be written."""
    with trace.TraceStore("unique", root=tmp_path) as store:
        with pytest.raises(ValueError, match="sensitive"):
            store.append("metadata", world_generation="w", frame=None, data={"api_key": "secret"})
        with pytest.raises(ValueError, match="JSON compliant"):
            store.append("observation", world_generation="w", frame=1, data={"speed": math.nan})
    with pytest.raises(FileExistsError):
        trace.TraceStore("unique", root=tmp_path)


@pytest.mark.parametrize("frame", [-1, True, 1.5])
def test_trace_rejects_invalid_frame_identity(tmp_path: Path, frame: object) -> None:
    """Frames must be integral simulator IDs, never boolean or approximate values."""
    with (
        trace.TraceStore("frame-check", root=tmp_path) as store,
        pytest.raises(ValueError, match="frame"),
    ):
        store.append("observation", world_generation="w", frame=cast("int", frame), data={})


def test_trace_byte_limit_preserves_prior_events(tmp_path: Path) -> None:
    """Exhausting retention is observable and cannot truncate already recorded evidence."""
    with trace.TraceStore("bounded", root=tmp_path, max_bytes=1024) as store:
        first = store.append("metadata", world_generation="w", frame=None, data={})
        with pytest.raises(trace.TraceLimitError):
            store.append("observation", world_generation="w", frame=1, data={"large": "x" * 2048})
    assert trace.load_trace(store.path).events == (first,)


def test_interrupted_append_keeps_complete_prefix_and_marks_trace_partial(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A torn final JSONL record is recoverable but cannot be reported as a valid run."""
    with trace.TraceStore("interrupted", root=tmp_path) as store:
        first = store.append("metadata", world_generation="w", frame=None, data={})
        original_write = os.write

        def fail_write(descriptor: int, payload: bytes) -> int:
            original_write(descriptor, payload[:10])
            message = "disk interrupted"
            raise OSError(message)

        monkeypatch.setattr(trace.os, "write", fail_write)
        with pytest.raises(OSError, match="disk interrupted"):
            store.append("outcome", world_generation="w", frame=1, data={"completed": True})
    read = trace.load_trace(store.path)

    assert read.events == (first,)
    assert read.complete is False
    assert trace.summarize_trace(read)["status"] == "infrastructure_invalid"


def test_metrics_use_physical_units_and_do_not_invent_missing_usage(tmp_path: Path) -> None:
    """Metric definitions use raw frame-linked values; absent provider usage is unknown."""
    with trace.TraceStore("metrics", root=tmp_path) as store:
        _metric_events(store)
    summary = trace.summarize_trace(trace.load_trace(store.path))

    assert summary["metrics"] == {
        "collision_events": 1,
        "collision_impulse_magnitude_sum_kg_mps": 5.0,
        "lane_invasion_events": 1,
        "completed": True,
        "hesitation_seconds": 0.1,
        "reversal_count": 1,
        "tracking_error_rmse_m": pytest.approx(math.sqrt(25 / 3)),
        "interventions": 1,
        "fallbacks": 1,
        "decision_latency_mean_seconds": 0.25,
        "decision_staleness_max_frames": 2,
        "sensor_dropped_samples": 2,
        "sensor_trailing_events": 1,
        "simulation_duration_seconds": 0.2,
        "wall_duration_seconds": summary["metrics"]["wall_duration_seconds"],
        "real_time_factor": summary["metrics"]["real_time_factor"],
    }
    assert summary["usage"] is None
    assert summary["status"] == "completed"


def _metric_events(store: trace.TraceStore) -> None:
    """Write a compact deterministic run with known physical metrics."""
    for frame, seconds, speed, error in [
        (1, 0.0, 0.1, 0.0),
        (2, 0.1, 2.0, 3.0),
        (3, 0.2, -1.0, 4.0),
    ]:
        store.append(
            "observation",
            world_generation="w",
            frame=frame,
            actor_id=7,
            data={"simulation_seconds": seconds, "speed_mps": speed, "tracking_error_m": error},
        )
    store.append(
        "sensor",
        world_generation="w",
        frame=3,
        actor_id=7,
        data={
            "measurement_frame": 2,
            "collision_impulse": {"x": 3.0, "y": 4.0, "z": 0.0},
            "lane_invasion": {"count": 1, "markings": ["Solid"]},
            "dropped_samples": 2,
            "trailing": True,
        },
    )
    store.append("intervention", world_generation="w", frame=3, actor_id=7, data={"fallback": True})
    store.append(
        "decision_received",
        world_generation="w",
        frame=3,
        actor_id=7,
        data={"latency_seconds": 0.25, "staleness_frames": 2},
    )
    store.append(
        "outcome",
        world_generation="w",
        frame=3,
        actor_id=7,
        data={"completed": True, "status": "completed"},
    )


def test_reversal_metric_prefers_signed_longitudinal_velocity(tmp_path: Path) -> None:
    """Positive speed magnitude cannot hide a reversal in lane-projected velocity."""
    with trace.TraceStore("signed-reversal", root=tmp_path) as store:
        for frame, longitudinal in enumerate((2.0, -2.0), start=1):
            store.append(
                "observation",
                world_generation="w",
                frame=frame,
                actor_id=7,
                data={"speed_mps": 2.0, "longitudinal_speed_mps": longitudinal},
            )
    summary = trace.summarize_trace(trace.load_trace(store.path))
    assert summary["metrics"]["reversal_count"] == 1


def test_missing_outcome_is_partial_and_html_matches_saved_summary(tmp_path: Path) -> None:
    """A well-formed file without terminal evidence is still an incomplete experiment."""
    with trace.TraceStore("partial", root=tmp_path) as store:
        store.append("observation", world_generation="w", frame=1, data={"speed_mps": 2.5})
    paths = trace.write_trace_report(store.path)
    summary = json.loads(paths["summary"].read_text(encoding="utf-8"))
    html = paths["report"].read_text(encoding="utf-8")

    assert summary == trace.summarize_trace(trace.load_trace(store.path))
    assert summary["status"] == "partial"
    assert "partial" in html
    assert "Collision events" in html


def test_resume_appends_recovery_without_replacing_original_bytes(tmp_path: Path) -> None:
    """A verified supervisor can resume a dead writer with the same monotonic sequence."""
    with trace.TraceStore("recover", root=tmp_path) as store:
        store.append("metadata", world_generation="w", frame=None, data={})
    original = store.path.read_bytes()
    with trace.TraceStore.resume("recover", root=tmp_path) as resumed:
        resumed.append("cleanup", world_generation="w", frame=None, data={"ok": True})

    assert store.path.read_bytes().startswith(original)
    assert [event["sequence"] for event in trace.load_trace(store.path).events] == [1, 2]


def test_replay_requires_exact_scene_and_candidate_identity(tmp_path: Path) -> None:
    """A recorded provider answer cannot silently become fixed-action replay on a new scene."""
    observation = {"gap_m": 14.125, "speed_mps": 3.0}
    candidates = [{"candidate_id": "yield", "target_speed_mps": 2.0}]
    decision = {"choice_id": "yield", "reason": "recorded answer"}
    with trace.TraceStore("replay", root=tmp_path) as store:
        store.append(
            "decision_received",
            world_generation="w",
            frame=10,
            actor_id=7,
            data={
                "observation_id": trace.identity_digest(observation),
                "candidate_set_id": trace.identity_digest(candidates),
                "decision": decision,
            },
        )
    replay = trace.RecordedDecisionReplay(trace.load_trace(store.path))

    assert (
        replay.decision_for(
            world_generation="w",
            frame=10,
            actor_id=7,
            observation=observation,
            candidates=candidates,
        )
        == decision
    )
    with pytest.raises(ValueError, match="identity"):
        replay.decision_for(
            world_generation="w",
            frame=10,
            actor_id=7,
            observation={"gap_m": 1.0, "speed_mps": 3.0},
            candidates=candidates,
        )


def test_malformed_complete_record_is_invalid_evidence_not_loader_crash(tmp_path: Path) -> None:
    """Missing required envelope keys are retained as invalid evidence."""
    path = tmp_path / "malformed.jsonl"
    path.write_text("{}\n", encoding="utf-8")

    read = trace.load_trace(path)

    assert read.events == ()
    assert read.complete is False


def test_nested_serialized_credential_is_not_logged(tmp_path: Path) -> None:
    """Provider metadata JSON strings receive the same credential checks as objects."""
    with (
        trace.TraceStore("serialized-secret", root=tmp_path) as store,
        pytest.raises(ValueError, match="sensitive"),
    ):
        store.append(
            "metadata",
            world_generation="w",
            frame=None,
            data={"metadata_json": '{"api_key":"hidden-value"}'},
        )


def test_resume_refuses_a_live_writer(tmp_path: Path) -> None:
    """Recovery cannot append concurrently with an active experiment process."""
    with trace.TraceStore("active", root=tmp_path), pytest.raises(BlockingIOError):
        trace.TraceStore.resume("active", root=tmp_path)


def test_resume_torn_record_preserves_invalidity_and_recovery_events(tmp_path: Path) -> None:
    """Recovery records stay readable after an interrupted JSON line without repairing history."""
    with trace.TraceStore("torn", root=tmp_path) as store:
        store.append("metadata", world_generation="w", frame=None, data={})
    with store.path.open("ab") as stream:
        stream.write(b'{"torn":')
    original = store.path.read_bytes()
    with trace.TraceStore.resume("torn", root=tmp_path) as resumed:
        resumed.append("cleanup", world_generation="w", frame=None, data={"ok": True})
    read = trace.load_trace(store.path)

    assert store.path.read_bytes().startswith(original)
    assert [event["kind"] for event in read.events] == ["metadata", "trace_interrupted", "cleanup"]
    assert trace.summarize_trace(read)["status"] == "infrastructure_invalid"


@pytest.mark.parametrize("change", [{"monotonic_seconds": "bad"}, {"wall_time": None}])
def test_loader_rejects_invalid_event_time(tmp_path: Path, change: dict[str, object]) -> None:
    """Unusable timing evidence must not crash or contaminate a physical summary."""
    with trace.TraceStore("timing", root=tmp_path) as store:
        event = store.append("metadata", world_generation="w", frame=None, data={})
    event.update(change)
    store.path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    read = trace.load_trace(store.path)

    assert not read.complete
    assert trace.summarize_trace(read)["status"] == "infrastructure_invalid"


def test_report_retains_reproduction_metadata(tmp_path: Path) -> None:
    """Saved versions, seeds and environment remain available beside outcome metrics."""
    metadata: dict[str, object] = {
        "code_version": "abc123",
        "fixture_version": "merge-v1",
        "seeds": {"world": 7},
    }
    with trace.TraceStore("versions", root=tmp_path) as store:
        store.append("metadata", world_generation="w", frame=None, data=metadata)
    paths = trace.write_trace_report(store.path)
    summary = json.loads(paths["summary"].read_text())

    assert summary["metadata"] == [metadata]
    assert "abc123" in paths["report"].read_text()
