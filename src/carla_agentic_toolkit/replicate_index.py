"""Strict aliases for repetition labels, without inventing reproduction evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_REPLICATE_INDEX = 2**31 - 1


def normalize_replicate_index(values: Mapping[str, object]) -> dict[str, object]:
    """Copy present labels to one canonical field; absent labels stay absent."""
    labels = {
        name: _validated_index(values[name], name)
        for name in ("replicate_index", "seed")
        if name in values
    }
    normalized = dict(values)
    if not labels:
        return normalized
    _require_matching_labels(labels)
    normalized["replicate_index"] = next(iter(labels.values()))
    normalized.pop("seed", None)
    return normalized


def _require_matching_labels(labels: dict[str, int]) -> None:
    if len(labels) > 1 and labels["replicate_index"] != labels["seed"]:
        message = "replicate_index and legacy seed must identify the same repetition"
        raise ValueError(message)


def _validated_index(value: object, field: str) -> int:
    if not _valid_index(value):
        message = f"{field} must be an exact integer in 0..{MAX_REPLICATE_INDEX}"
        raise ValueError(message)
    return cast("int", value)


def _valid_index(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_REPLICATE_INDEX
