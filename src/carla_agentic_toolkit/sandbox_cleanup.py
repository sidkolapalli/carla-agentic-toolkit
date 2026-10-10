"""Preserve trusted cleanup evidence when finishing a sandbox execution."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.ownership import cleanup_report

if TYPE_CHECKING:
    from carla_agentic_toolkit.sandbox import ScriptOutcome


def verified_settings_outcome(outcome: ScriptOutcome, report: dict[str, object]) -> ScriptOutcome:
    """Reject a child's success when trusted settings recovery cannot verify it."""
    if report.get("failures") or report.get("settings_restored") is False:
        return replace(
            outcome,
            ok=False,
            error_type="settings_restore_failed",
            error=str(report.get("failures", "Settings restoration could not be verified.")),
            cleanup=report,
        )
    return replace(outcome, cleanup={**(outcome.cleanup or {}), **report})


def failed_cleanup_outcome(outcome: ScriptOutcome, report: dict[str, object]) -> ScriptOutcome:
    """Merge child and parent failures without dropping settings evidence."""
    if not any(report.values()):
        return outcome
    previous = outcome.cleanup or cleanup_report()
    combined: dict[str, object] = {
        key: [*_list_value(previous, key), *_list_value(report, key)]
        for key in ("attempted_actor_ids", "destroyed_actor_ids", "failures")
    }
    return replace(outcome, cleanup=report | combined)


def _list_value(payload: dict[str, object], key: str) -> list[object]:
    value = payload.get(key)
    return cast("list[object]", value) if isinstance(value, list) else []
