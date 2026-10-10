"""Controller creation must be durable before the maintenance step finishes."""

from __future__ import annotations

import threading
from functools import partial
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import traffic_controller_service, traffic_controller_step
from carla_agentic_toolkit.errors import OwnershipError
from carla_agentic_toolkit.models import TrafficControllerStartRequest, TrafficDensityRequest
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.ownership_creation import track_actor_created
from tests.test_incremental_actor_ownership import (
    FIRST_ACTOR_ID,
    WORLD_ID,
    CreationCase,
    SpawnInterrupted,
)
from tests.test_incremental_actor_ownership import (
    creation_case as creation_case,  # noqa: PLC0414 - re-export the shared pytest fixture.
)


def test_controller_journals_before_profiles_or_world_wait(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A later maintenance failure cannot hide returned vehicle IDs."""

    def interrupt(*_args: object) -> None:
        raise SpawnInterrupted

    monkeypatch.setattr(traffic_controller_step, "apply_profiles", interrupt)
    client = creation_case.client
    request = TrafficControllerStartRequest(
        density=TrafficDensityRequest(vehicle_count=1, safe_filter=False)
    )
    with pytest.raises(SpawnInterrupted):
        traffic_controller_step.maintain_traffic_once(
            client,
            request,
            {},
            on_spawn=partial(track_actor_created, creation_case.adapter, creation_case.ownership),
        )
    fresh = RunOwnership(creation_case.journal_path)
    assert fresh.actor_ids() == (FIRST_ACTOR_ID,)
    assert fresh.world_id() == WORLD_ID


def test_controller_stops_instead_of_retrying_ownership_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary retry policy must not swallow a fatal journal observer error."""
    service = traffic_controller_service.InProcessTrafficControllerService()
    client = Mock()
    monkeypatch.setattr(traffic_controller_service, "_client", lambda _request: client)
    attempts = Mock(side_effect=OwnershipError("journal failed"))
    monkeypatch.setattr(service, "_control_once", attempts)
    stop = threading.Event()
    # The old RuntimeError handler loops until this external stop, proving a retry.
    original_wait = stop.wait

    def finish_old_loop(_timeout: float) -> bool:
        stop.set()
        return original_wait(0)

    wait = Mock(side_effect=finish_old_loop)
    monkeypatch.setattr(stop, "wait", wait)
    service._run(stop, generation=0)  # noqa: SLF001 - directly exercise the worker retry boundary.
    assert stop.is_set()
    assert service.get_status().error_type == "actor_ownership_failed"
    attempts.assert_called_once()
    wait.assert_not_called()
