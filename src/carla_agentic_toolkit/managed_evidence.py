"""Convert bounded sensor drains into physical event records without losing provenance."""

from __future__ import annotations

from typing import cast


def sensor_records(payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    """Retain delivery metadata once and flatten actual measurements for physical metrics."""
    common = {key: value for key, value in payload.items() if key != "samples"}
    samples = cast("list[dict[str, object]]", payload.get("samples", []))
    delivery = common | {
        "delivery_only": True,
        "sample_count": len(samples),
        "trailing": False,
        "drain_phase": "cleanup" if payload.get("trailing") else "observation",
    }
    measurements = tuple(common | sample | {"delivery_only": False} for sample in samples)
    return (delivery, *measurements)


def trailing_drains(cleanup: dict[str, object]) -> tuple[dict[str, object], ...]:
    """Unwrap callback evidence retained by the session's authoritative cleanup report."""
    callbacks = cast("list[object]", cleanup.get("trailing", []))
    return tuple(drain for callback in callbacks for drain in _callback_drains(callback))


def _callback_drains(callback: object) -> list[dict[str, object]]:
    if not isinstance(callback, dict):
        return []
    return cast("list[dict[str, object]]", callback.get("sensors", []))
