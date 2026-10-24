"""Published demo samples retain exact evidence without exporting private trace payloads."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest

from scripts import project_demo_trace

if TYPE_CHECKING:
    from pathlib import Path


def _actor(frame: int) -> dict[str, object]:
    return {
        "actor_id": 91,
        "frame": frame,
        "longitudinal_m": 12.123456789012345,
        "lateral_m": -0.000000000123456789,
        "speed_mps": 4.75,
        "length_m": 4.791779518127441,
        "width_m": 2.163450002670288,
        "yaw_error_degrees": -0.000274658203125,
        "position_m": [100, 200, 300],
    }


def _observation(frame: int) -> dict[str, object]:
    return {
        "run_id": "demo-run",
        "world_generation": "private-world",
        "frame": frame,
        "simulation_seconds": frame * 0.05,
        "phase": "preparing",
        "policy": _actor(frame),
        "ego": {**_actor(frame), "actor_id": 92, "longitudinal_m": 40.125},
        "lane": {"width_m": 3.5, "target_offset_m": 3.50000029233756, "road_id": 10},
        "sensors": [{"private_path": "/private/sensor.bin"}],
    }


def _events() -> list[dict[str, Any]]:
    values: list[tuple[str, int | None, dict[str, object]]] = [
        (
            "metadata",
            None,
            {
                "code_sha256": "c" * 64,
                "seed": 7,
                "spec": {"policy": "jev", "fixed_delta_seconds": 0.05, "host": "private-host"},
                "environment": {"private_path": "/private/runtime"},
            },
        ),
        ("observation", 10, _observation(10)),
        (
            "decision_received",
            10,
            {
                "reason": "safe_gap",
                "choice_id": "merge",
                "source": "jev",
                "metadata": {"provider_response": "private-provider-payload"},
            },
        ),
        ("execution", 10, {"executed_choice": "merge", "phase": "committed"}),
        ("observation", 11, _observation(11)),
        ("execution", 11, {"executed_choice": "continue", "phase": "committed"}),
        ("observation", 14, _observation(14)),
        ("outcome", 14, {"completed": True, "status": "completed", "private": "outcome"}),
        (
            "cleanup",
            14,
            {"ok": True, "settings_restored": True, "world_replaced": False, "actors": [91, 92]},
        ),
    ]
    return [
        {
            "schema_version": 1,
            "run_id": "demo-run",
            "world_generation": "private-world",
            "actor_id": 91,
            "frame": frame,
            "sequence": sequence,
            "kind": kind,
            "monotonic_seconds": sequence * 0.125,
            "wall_time": "2026-10-03T00:00:00+00:00",
            "data": data,
        }
        for sequence, (kind, frame, data) in enumerate(values, start=1)
    ]


def _write(path: Path, events: list[dict[str, Any]]) -> str:
    data = "".join(json.dumps(event) + "\n" for event in events).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def test_projection_preserves_numbers_and_frames(tmp_path: Path) -> None:
    """No interpolation or rounding may change authoritative numerical values."""
    path = tmp_path / "events.jsonl"
    digest = _write(path, _events())
    result = project_demo_trace.project_trace(path, digest)
    samples = result["samples"]
    assert [(item["observation_sequence"], item["frame"]) for item in samples] == [
        (2, 10),
        (5, 11),
        (7, 14),
    ]
    assert samples[0]["policy"] == {
        key: value
        for key, value in _actor(10).items()
        if key not in {"actor_id", "frame", "position_m"}
    }
    assert [item["simulation_seconds"] for item in samples] == [0.5, 0.55, 0.7000000000000001]


def test_projection_preserves_decision_and_execution_order(tmp_path: Path) -> None:
    """Future decisions and missing executions must never become observed evidence."""
    path = tmp_path / "events.jsonl"
    digest = _write(path, _events())
    samples = project_demo_trace.project_trace(path, digest)["samples"]
    assert samples[0]["decision"] is None
    assert samples[1]["decision"] == {
        "reason": "safe_gap",
        "choice_id": "merge",
        "source": "jev",
        "sequence": 3,
        "frame": 10,
    }
    assert samples[2]["execution"] is None
    assert samples[0]["execution"] == {
        "executed_choice": "merge",
        "phase": "committed",
        "sequence": 4,
    }


def test_projection_exports_only_reviewed_fields(tmp_path: Path) -> None:
    """Private host, runtime paths, sensor payloads and provider metadata stay private."""
    path = tmp_path / "events.jsonl"
    digest = _write(path, _events())
    result = project_demo_trace.project_trace(path, digest)
    assert set(result) == {
        "schema_version",
        "run_id",
        "trace_sha256",
        "code_sha256",
        "replicate_index",
        "policy",
        "fixed_delta_seconds",
        "outcome",
        "cleanup",
        "samples",
    }
    assert (result["trace_sha256"], result["cleanup"], result["outcome"]) == (
        digest,
        {"ok": True, "settings_restored": True, "world_replaced": False},
        {"completed": True, "status": "completed"},
    )
    assert "private" not in json.dumps(result)


@pytest.mark.parametrize("digest", ["0" * 64, "invalid"])
def test_hash_mismatch_rejected(tmp_path: Path, digest: str) -> None:
    """Only explicitly pinned trace bytes may become publishable evidence."""
    path = tmp_path / "events.jsonl"
    _write(path, _events())
    with pytest.raises(ValueError, match="SHA256"):
        project_demo_trace.project_trace(path, digest)


@pytest.mark.parametrize("damage", [b'{"torn":', b"{}\n", b""])
def test_damaged_or_unterminated_trace_rejected(tmp_path: Path, damage: bytes) -> None:
    """A matching hash does not make a truncated or invalid trace complete."""
    path = tmp_path / "events.jsonl"
    _write(path, _events())
    payload = path.read_bytes().rstrip(b"\n") + damage
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="complete"):
        project_demo_trace.project_trace(path, hashlib.sha256(payload).hexdigest())


@pytest.mark.parametrize("missing", ["observation", "outcome", "cleanup", "metadata"])
def test_missing_required_evidence_rejected(tmp_path: Path, missing: str) -> None:
    """No success, cleanup, metadata or trajectory may be fabricated from absence."""
    path = tmp_path / "events.jsonl"
    events = [event for event in _events() if event["kind"] != missing]
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    digest = _write(path, events)
    with pytest.raises(ValueError, match=missing):
        project_demo_trace.project_trace(path, digest)


def test_non_scalar_allowlisted_value_cannot_smuggle_metadata(tmp_path: Path) -> None:
    """Allowlisting field names also enforces the expected primitive value shapes."""
    path = tmp_path / "events.jsonl"
    events = _events()
    events[2]["data"]["reason"] = {"provider_payload": "must-not-be-published"}
    digest = _write(path, events)
    with pytest.raises(ValueError, match="reason"):
        project_demo_trace.project_trace(path, digest)


def test_observation_identity_mismatch_rejected(tmp_path: Path) -> None:
    """Frame-linked actor values must belong to the recorded observation envelope."""
    path = tmp_path / "events.jsonl"
    events = _events()
    events[1]["data"]["policy"]["frame"] = 999
    digest = _write(path, events)
    with pytest.raises(ValueError, match="identity"):
        project_demo_trace.project_trace(path, digest)


def test_cli_rejects_invalid_input_without_overwriting_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unsuccessful projection cannot replace an existing reviewed output."""
    path, output = tmp_path / "events.jsonl", tmp_path / "demo.json"
    _write(path, _events())
    output.write_text("previous")
    monkeypatch.setattr(
        "sys.argv", ["project_demo_trace", str(path), "--sha256", "0" * 64, "--output", str(output)]
    )
    with pytest.raises(SystemExit) as failure:
        project_demo_trace.main()
    assert (failure.value.code, output.read_text()) == (2, "previous")


def test_cli_writes_the_exact_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The public CLI exposes exactly the tested allowlisted schema."""
    path, output = tmp_path / "events.jsonl", tmp_path / "demo.json"
    digest = _write(path, _events())
    monkeypatch.setattr(
        "sys.argv", ["project_demo_trace", str(path), "--sha256", digest, "--output", str(output)]
    )
    project_demo_trace.main()
    assert json.loads(output.read_text()) == project_demo_trace.project_trace(path, digest)


def test_trace_size_bound_applies_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pinned bytes cannot bypass the existing bounded trace-reader contract."""
    path = tmp_path / "events.jsonl"
    digest = _write(path, _events())
    monkeypatch.setattr(project_demo_trace, "MAX_TRACE_BYTES", 10)
    with pytest.raises(ValueError, match="read limit"):
        project_demo_trace.project_trace(path, digest)


def test_credentials_in_allowlisted_text_fail_closed(tmp_path: Path) -> None:
    """The existing trace validator checks even fields selected for publication."""
    path = tmp_path / "events.jsonl"
    events = _events()
    events[2]["data"]["reason"] = "Bearer private-sensitive-material"
    digest = _write(path, events)
    with pytest.raises(ValueError, match="complete"):
        project_demo_trace.project_trace(path, digest)


def test_cli_refuses_hard_link_to_private_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Different path names cannot disguise an output that overwrites the raw trace."""
    path, output = tmp_path / "events.jsonl", tmp_path / "alias.json"
    digest = _write(path, _events())
    original = path.read_bytes()
    output.hardlink_to(path)
    monkeypatch.setattr(
        "sys.argv",
        ["project_demo_trace", str(path), "--sha256", digest, "--output", str(output)],
    )
    with pytest.raises(SystemExit) as failure:
        project_demo_trace.main()
    assert (failure.value.code, path.read_bytes()) == (2, original)


def test_execution_before_observation_is_not_a_valid_match(tmp_path: Path) -> None:
    """Same-frame identity cannot turn an earlier actuation into a subsequent execution."""
    path = tmp_path / "events.jsonl"
    events = _events()
    execution = events.pop(3)
    events.insert(1, execution)
    for sequence, event in enumerate(events, start=1):
        event["sequence"] = sequence
    digest = _write(path, events)
    with pytest.raises(ValueError, match="execution"):
        project_demo_trace.project_trace(path, digest)
