"""A still-published old episode cannot resolve an unacknowledged native reload."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from carla_agentic_toolkit.managed_session import ManagedSession
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.simulator_lease import SimulatorLease
from tests.managed_reload_helpers import _assert_quarantined, _case, _session

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient
    from tests.managed_reload_helpers import ReloadWorld


@pytest.mark.parametrize("recover", [False, True], ids=["close", "recover"])
def test_pending_reload_old_publication_cannot_authorize_cleanup(
    tmp_path: Path, *, recover: bool
) -> None:
    """Late reload publication can follow a timeout while the client retains its old ID."""
    old, _new, client = _case()
    client.reload_error = "reload reply lost"
    client.on_reload = lambda: setattr(client, "world", old)
    with SimulatorLease("localhost", 3000, state_root=tmp_path) as lease:
        session = _session(client, lease)
        with pytest.raises(RuntimeError, match="reload reply lost"):
            session.open()
        evidence = lease.recovery_state
        events = old.events.copy()
        frame = old.frame
        if recover:
            result = ManagedSession.recover(cast("CarlaClient", client), lease, ExperimentSpec())
        else:
            result = session.close()
        _assert_pending_cleanup(result, lease)
        _assert_world_unmutated(old, len(events), frame)
        assert lease.recovery_state == evidence
    _assert_quarantined(tmp_path)


def _assert_pending_cleanup(result: dict[str, object], lease: SimulatorLease) -> None:
    assert result["ok"] is False
    assert result["settings_restored"] is False
    assert cast("dict[str, object]", lease.recovery_state["reload"])["phase"] == "pending"
    _assert_episode_unobserved(result)


def _assert_episode_unobserved(result: dict[str, object]) -> None:
    assert result["world_replaced"] is None
    assert result["world_identity_checked"] is False


def _assert_world_unmutated(world: ReloadWorld, previous_events: int, frame: int) -> None:
    assert world.settings["synchronous_mode"] is True
    assert world.frame == frame
    assert not any(
        name in {"apply", "tick", "actors"} for name, _ in world.events[previous_events:]
    )
