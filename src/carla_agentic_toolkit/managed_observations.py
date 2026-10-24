"""Optional background handles never widen the reviewed provider sensing range."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaActor
    from carla_agentic_toolkit.managed_engine import ExperimentRun
    from carla_agentic_toolkit.managed_session import ManagedSession
    from carla_agentic_toolkit.managed_spec import ExperimentSpec


def background_actors(session: object) -> tuple[CarlaActor, ...]:
    """Allow legacy fixture doubles while real sessions expose only their owned IDs."""
    callback = getattr(session, "background_actors", None)
    return cast("tuple[CarlaActor, ...]", callback()) if callable(callback) else ()


def record_background(run: ExperimentRun) -> None:
    """Keep full owned-density status in the trace, outside provider observations."""
    if run.spec.background_density is not None:
        report = cast("ManagedSession", run.session).density_status()
        if report:
            run.event("background_density", report)


def prepare_background(run: ExperimentRun) -> None:
    """Populate only after reviewed fixture setup, then record bounded ACK counts."""
    if run.spec.background_density is not None:
        cast("ManagedSession", run.session).prepare_background()
        record_background(run)


def traffic_control_description(spec: ExperimentSpec) -> str:
    """Keep disabled fixture metadata unchanged and describe enabled background control."""
    if spec.background_density is None:
        return "owned scripted vehicles; no Traffic Manager"
    return "owned scripted fixture vehicles; proven-local managed TM backgrounds"
