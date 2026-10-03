"""Parent cleanup lends its lease to bounded native recovery and retains failure evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit import sandbox

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_cleanup_borrows_lease_and_preserves_script_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup cannot race another owner or conceal the timeout that triggered recovery."""
    calls = []
    journal = tmp_path / "owned-actors.json"
    descriptor = 42

    def cleanup(
        host: str,
        port: int,
        path: Path,
        lease_descriptor: int,
        timeout_seconds: float,
    ) -> dict[str, object]:
        calls.append((host, port, path, lease_descriptor, timeout_seconds))
        return {
            "attempted_actor_ids": [8],
            "destroyed_actor_ids": [],
            "failures": [{"actor_id": 8, "error": "native cleanup deadline"}],
        }

    monkeypatch.setattr(sandbox, "cleanup_script_ownership", cleanup, raising=False)
    original = sandbox.ScriptOutcome(ok=False, result=None, stdout="", error_type="script_timeout")
    outcome = sandbox._cleanup_failed_execution(  # noqa: SLF001
        original,
        ownership_path=journal,
        request=sandbox.ExecutionRequest("localhost", 3000, 1, ()),
        lease_descriptor=descriptor,
    )
    assert calls == [("localhost", 3000, journal, descriptor, 10.0)]
    assert outcome.error_type == "script_timeout"
    assert outcome.cleanup == {
        "attempted_actor_ids": [8],
        "destroyed_actor_ids": [],
        "failures": [{"actor_id": 8, "error": "native cleanup deadline"}],
    }
