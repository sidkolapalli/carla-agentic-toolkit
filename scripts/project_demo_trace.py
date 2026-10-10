"""Export allowlisted numerical demo evidence from an explicitly pinned private trace."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any, cast

from carla_agentic_toolkit.experiment_trace import MAX_TRACE_BYTES, load_trace
from carla_agentic_toolkit.managed_names import normalize_merge_observation
from carla_agentic_toolkit.replicate_index import normalize_replicate_index

ACTOR_FIELDS = (
    "longitudinal_m",
    "lateral_m",
    "speed_mps",
    "length_m",
    "width_m",
    "yaw_error_degrees",
)


def project_trace(path: Path, expected_sha256: str) -> dict[str, Any]:
    """Preserve observed values and event ordering without exporting arbitrary payloads."""
    events, digest = _pinned_events(path, expected_sha256)
    metadata = normalize_replicate_index(_metadata(events))
    spec = _mapping(metadata, "spec")
    return {
        "schema_version": 1,
        "run_id": events[0]["run_id"],
        "trace_sha256": digest,
        "code_sha256": _digest(_text(metadata, "code_sha256")),
        "replicate_index": _integer(metadata, "replicate_index"),
        "policy": _text(spec, "policy"),
        "fixed_delta_seconds": _number(spec, "fixed_delta_seconds"),
        "outcome": _outcome(events),
        "cleanup": _cleanup(events),
        "samples": _samples(events),
    }


def _digest(value: str) -> str:
    if re.fullmatch(r"[a-fA-F0-9]{64}", value) is None:
        message = "A complete hexadecimal SHA256 is required."
        raise ValueError(message)
    return value.lower()


def _pinned_events(path: Path, expected: str) -> tuple[tuple[dict[str, Any], ...], str]:
    """Hash and parse the same bounded snapshot, even if the source is still changing."""
    expected = _digest(expected)
    with path.open("rb") as stream:
        payload = stream.read(MAX_TRACE_BYTES + 1)
    if len(payload) > MAX_TRACE_BYTES:
        message = "Trace file exceeds the supported read limit."
        raise ValueError(message)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected:
        message = "Trace SHA256 does not match the explicitly expected digest."
        raise ValueError(message)
    with tempfile.TemporaryDirectory(prefix="carla-demo-trace-") as directory:
        snapshot = Path(directory) / "events.jsonl"
        snapshot.touch(mode=0o600)
        snapshot.write_bytes(payload)
        read = load_trace(snapshot)
    if not read.complete:
        message = "Demo projection requires a complete, undamaged trace."
        raise ValueError(message)
    return read.events, digest


def _metadata(events: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    values = [event["data"] for event in events if event["kind"] == "metadata"]
    if len(values) != 1:
        message = "Demo projection requires exactly one metadata event."
        raise ValueError(message)
    return values[0]


def _last_data(events: tuple[dict[str, Any], ...], kind: str) -> dict[str, Any]:
    for event in reversed(events):
        if event["kind"] == kind:
            return event["data"]
    message = f"Demo projection requires a recorded {kind} event."
    raise ValueError(message)


def _outcome(events: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    data = _last_data(events, "outcome")
    return {"completed": _boolean(data, "completed"), "status": _text(data, "status")}


def _cleanup(events: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    data = _last_data(events, "cleanup")
    return {key: _boolean(data, key) for key in ("ok", "settings_restored", "world_replaced")}


def _identity(event: dict[str, Any]) -> tuple[object, ...]:
    return event["world_generation"], event["actor_id"], event["frame"]


def _executions(events: tuple[dict[str, Any], ...]) -> dict[tuple[object, ...], dict[str, Any]]:
    result: dict[tuple[object, ...], dict[str, Any]] = {}
    for event in events:
        if event["kind"] == "execution":
            key = _identity(event)
            if key in result:
                message = "Ambiguous execution identity in demo trace."
                raise ValueError(message)
            result[key] = {
                "executed_choice": _text(event["data"], "executed_choice"),
                "phase": _text(event["data"], "phase"),
                "sequence": event["sequence"],
            }
    return result


def _samples(events: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    executions = _executions(events)
    decisions: dict[tuple[object, ...], dict[str, Any]] = {}
    result = []
    for event in events:
        identity = _identity(event)
        if event["kind"] == "decision_received":
            decisions[identity[:2]] = _decision(event)
        elif event["kind"] == "observation":
            sample = _observation(event)
            sample["decision"] = decisions.get(identity[:2])
            sample["execution"] = _subsequent_execution(event, executions.get(identity))
            result.append(sample)
    if not result:
        message = "Demo projection requires recorded observation events."
        raise ValueError(message)
    return result


def _subsequent_execution(
    event: dict[str, Any], execution: dict[str, Any] | None
) -> dict[str, Any] | None:
    if execution is not None and execution["sequence"] <= event["sequence"]:
        message = "Matched execution must follow its recorded observation."
        raise ValueError(message)
    return execution


def _decision(event: dict[str, Any]) -> dict[str, Any]:
    data = event["data"]
    return {
        **{key: _text(data, key) for key in ("reason", "choice_id", "source")},
        "sequence": event["sequence"],
        "frame": _integer(event, "frame"),
    }


def _observation(event: dict[str, Any]) -> dict[str, Any]:
    data = normalize_merge_observation(event["data"])
    _check_observation_identity(event, data)
    lane = _mapping(data, "lane")
    return {
        "observation_sequence": event["sequence"],
        "frame": _integer(event, "frame"),
        "simulation_seconds": _number(data, "simulation_seconds"),
        "phase": _text(data, "phase"),
        "policy": _actor(_mapping(data, "policy")),
        "target": _actor(_mapping(data, "target")),
        "lane": {key: _number(lane, key) for key in ("width_m", "target_offset_m")},
    }


def _check_observation_identity(event: dict[str, Any], data: dict[str, Any]) -> None:
    observed = (data.get("run_id"), data.get("world_generation"), data.get("frame"))
    expected = (event["run_id"], event["world_generation"], event["frame"])
    actors = (_mapping(data, "policy"), _mapping(data, "target"))
    actor_frames = tuple(actor.get("frame") for actor in actors)
    if observed != expected or actor_frames != (event["frame"], event["frame"]):
        message = "Observation identity does not match its trace envelope."
        raise ValueError(message)
    if actors[0].get("actor_id") != event["actor_id"]:
        message = "Policy actor identity does not match its trace envelope."
        raise ValueError(message)


def _actor(data: dict[str, Any]) -> dict[str, int | float]:
    return {key: _number(data, key) for key in ACTOR_FIELDS}


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    return _field(data, key, (dict,))


def _text(data: dict[str, Any], key: str) -> str:
    return _field(data, key, (str,))


def _number(data: dict[str, Any], key: str) -> int | float:
    value = cast("int | float", _field(data, key, (int, float)))
    if not math.isfinite(value):
        message = f"Missing or invalid numerical field: {key}."
        raise ValueError(message)
    return value


def _integer(data: dict[str, Any], key: str) -> int:
    return _field(data, key, (int,))


def _boolean(data: dict[str, Any], key: str) -> bool:
    return _field(data, key, (bool,))


def _field(data: dict[str, Any], key: str, expected: tuple[type, ...]) -> Any:  # noqa: ANN401
    value = data.get(key)
    if type(value) not in expected:
        message = f"Missing or invalid field: {key}."
        raise ValueError(message)
    return value


def _write_projection(trace: Path, expected: str, output: Path) -> None:
    if output.resolve() == trace.resolve() or (output.exists() and output.samefile(trace)):
        message = "The output cannot replace the private input trace."
        raise ValueError(message)
    result = project_trace(trace, expected)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")


def main() -> None:
    """Write an explicit public projection only after hash and evidence validation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args()
    try:
        _write_projection(options.trace, options.sha256, options.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
