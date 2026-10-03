"""Canonical numerical trace envelopes and bounded credential-safe JSON validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

SCHEMA_VERSION = 1
MAX_JSON_DEPTH = 32
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_SECRET_TEXT = re.compile(r"(?i)(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{20,})")
_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "password",
        "secret",
        "clientsecret",
        "accesstoken",
        "refreshtoken",
        "credentials",
        "credential",
        "privatekey",
    }
)


def validate_event(event: dict[str, Any]) -> None:
    """Validate the stable envelope independently of event-specific numerical data."""
    validate_identifier(event["run_id"], "run_id")
    validate_identifier(event["kind"], "kind")
    _validate_frame(event["frame"])
    _validate_identity_fields(event)
    validate_event_time(event)
    _check_value(event["data"], 0)
    canonical_json(event)


def validate_event_time(event: dict[str, Any]) -> None:
    """Require finite elapsed timing and an explicitly zoned wall timestamp."""
    monotonic = event["monotonic_seconds"]
    if type(monotonic) not in (int, float) or not math.isfinite(monotonic):
        message = "Trace monotonic_seconds must be finite numerical timing."
        raise ValueError(message)
    wall = datetime.fromisoformat(event["wall_time"])
    if wall.tzinfo is None:
        message = "Trace wall_time must include a timezone."
        raise ValueError(message)


def _validate_identity_fields(event: dict[str, Any]) -> None:
    """Require schema, generation, sequence, and object-shaped event evidence."""
    if event["schema_version"] != SCHEMA_VERSION or not isinstance(event["world_generation"], str):
        message = "Unsupported trace schema or world generation."
        raise ValueError(message)
    _validate_sequence_fields(event)


def _validate_sequence_fields(event: dict[str, Any]) -> None:
    """Require sequence and payload identity after schema validation."""
    if not event["world_generation"] or type(event["sequence"]) is not int:
        message = "Trace world/sequence identity is missing."
        raise ValueError(message)
    _validate_actor(event["actor_id"])
    if not isinstance(event["data"], dict):
        message = "Trace data must be an object."
        raise TypeError(message)


def _validate_frame(frame: object) -> None:
    """Keep missing frame identity explicit and reject fabricated numeric IDs."""
    if frame is None:
        return
    if type(frame) is not int or frame < 0:
        message = "Trace frame must be a nonnegative integer or null."
        raise ValueError(message)


def _validate_actor(actor_id: object) -> None:
    """Reject ambiguous actor identifiers while allowing run-level events."""
    if actor_id is None:
        return
    if type(actor_id) is not int or actor_id <= 0:
        message = "Trace actor_id must be a positive integer or null."
        raise ValueError(message)


def validate_identifier(value: str, label: str) -> None:
    """Prevent directory traversal and unbounded identity strings."""
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        message = f"Invalid trace {label}."
        raise ValueError(message)


def _check_value(value: object, depth: int) -> None:
    """Bound nesting and reject sensitive material throughout event data."""
    if depth > MAX_JSON_DEPTH:
        message = "Trace data nesting limit exceeded."
        raise ValueError(message)
    if isinstance(value, dict):
        _check_mapping(cast("Mapping[object, object]", value), depth)
    elif isinstance(value, list | tuple):
        _check_sequence(value, depth)
    elif isinstance(value, str):
        _check_string(value, depth)


def _check_mapping(value: Mapping[object, object], depth: int) -> None:
    for key, item in value.items():
        normalized = re.sub(r"[^a-z]", "", str(key).lower())
        if normalized in _SENSITIVE_KEYS:
            message = "Trace data contains a sensitive credential field."
            raise ValueError(message)
        _check_value(item, depth + 1)


def _check_sequence(value: Sequence[object], depth: int) -> None:
    for item in value:
        _check_value(item, depth + 1)


def _check_string(value: str, depth: int) -> None:
    if _SECRET_TEXT.search(value):
        message = "Trace data contains sensitive credential text."
        raise ValueError(message)
    if value.lstrip().startswith(("{", "[")):
        _check_value(_embedded_json(value), depth + 1)


def _embedded_json(value: str) -> object:
    try:
        return json.loads(value)
    except ValueError:
        return None


def canonical_json(value: object) -> str:
    """Serialize stable exact numerical values and reject nonfinite JSON numbers."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity_digest(value: object) -> str:
    """Hash the exact canonical numerical evidence, including values and key names."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
