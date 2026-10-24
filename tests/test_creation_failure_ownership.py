"""A journal failure cannot be hidden by recovered API errors or unchecked rollback."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import script_runner
from carla_agentic_toolkit.errors import OwnershipError
from carla_agentic_toolkit.ownership import RunOwnership
from carla_agentic_toolkit.persistent_namespace import PersistentNamespace
from tests.test_incremental_actor_ownership import (
    FIRST_ACTOR_ID,
    CreationCase,
    _create,
    _spawn_request,
)
from tests.test_incremental_actor_ownership import (
    creation_case as creation_case,  # noqa: PLC0414 - re-export the shared pytest fixture.
)

if TYPE_CHECKING:
    from pathlib import Path


def _failed_journal(*_args: object, **_kwargs: object) -> None:
    message = "Actor journal write failed."
    raise OwnershipError(message)


def _rollback(creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch, error: str) -> Mock:
    batch = Mock(return_value={"responses": [{"actor_id": FIRST_ACTOR_ID, "error": error}]})
    monkeypatch.setattr(creation_case.adapter, "apply_batch", batch)
    return batch


@pytest.mark.parametrize("helper", ["traffic", "walkers"])
def test_confirmed_no_actor_response_completes_creation_intent(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch, helper: str
) -> None:
    """A definite try_spawn collision is not an unknown interrupted RPC."""
    monkeypatch.setattr(creation_case.world, "try_spawn_actor", lambda *_args: None)
    _create(creation_case.api, helper)
    creation_case.ownership.require_completed_creations()


@pytest.mark.parametrize("error", ["", "unable to destroy actor: not found", "server busy"])
def test_journal_failure_uses_non_ticking_authoritative_rollback(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch, error: str
) -> None:
    """Known raw IDs are checked at the server even when they never reached the journal."""
    monkeypatch.setattr(creation_case.ownership, "add", _failed_journal)
    batch = _rollback(creation_case, monkeypatch, error)
    result = creation_case.api.spawn_actor_batch([_spawn_request()])
    assert result["ok"] is False
    batch.assert_called_once_with(
        [{"action": "destroy_actor", "actor_id": FIRST_ACTOR_ID}], do_tick=False
    )
    # No later native mutation is permitted after an ownership failure.
    creation_case.api.spawn_actor_batch([_spawn_request()])
    assert len(creation_case.world.actors) == 1


def test_unreadable_journal_still_rolls_back_using_known_creation_episode(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Journal loading cannot erase the raw ID and originating episode needed for rollback."""
    spawn = creation_case.world.spawn_actor

    def corrupt_after_creation(*args: object) -> object:
        actor = spawn(*args)
        monkeypatch.setattr(creation_case.ownership, "_load", _failed_journal)
        return actor

    monkeypatch.setattr(creation_case.world, "spawn_actor", corrupt_after_creation)
    batch = _rollback(creation_case, monkeypatch, "")
    result = creation_case.api.spawn_actor_batch([_spawn_request()])
    assert result["ok"] is False
    batch.assert_called_once_with(
        [{"action": "destroy_actor", "actor_id": FIRST_ACTOR_ID}], do_tick=False
    )


def test_ignored_ownership_error_cannot_finish_finite_worker_successfully(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clean-looking empty journal cannot hide an unconfirmed known-ID rollback."""
    monkeypatch.setattr(RunOwnership, "add", _failed_journal)
    monkeypatch.setattr(
        script_runner, "PythonCarlaAdapter", lambda **_kwargs: creation_case.adapter
    )
    _rollback(creation_case, monkeypatch, "server busy")
    script = tmp_path / "script.py"
    script.write_text(
        f"api.spawn_actor_batch([{_spawn_request()!r}])\nresult = 5\n", encoding="utf-8"
    )
    outcome = script_runner.run_script_file(
        script_path=script,
        host="localhost",
        port=3000,
        timeout_seconds=30,
        ownership_path=tmp_path / "worker-owned.json",
    )
    assert outcome["ok"] is False
    _assert_failed_cleanup(outcome)


def test_ignored_ownership_error_blocks_persistent_request_and_future_creation(
    creation_case: CreationCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persistent requests cannot continue spawning after an ignored fatal journal error."""
    monkeypatch.setattr(creation_case.ownership, "add", _failed_journal)
    _rollback(creation_case, monkeypatch, "server busy")
    namespace = PersistentNamespace(creation_case.api)
    code = f"api.spawn_actor_batch([{_spawn_request()!r}])\nresult = 5\n"
    outcome = namespace.execute(code)
    assert outcome["ok"] is False
    _assert_failed_cleanup(outcome)
    assert namespace.execute(code)["ok"] is False
    assert len(creation_case.world.actors) == 1


def _assert_failed_cleanup(outcome: dict[str, object]) -> None:
    cleanup = outcome["cleanup"]
    assert isinstance(cleanup, dict)
    assert cast("dict[str, object]", cleanup)["failures"]
