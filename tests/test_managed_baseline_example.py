"""The no-key programmatic example preserves lifecycle truth on completion and interruption."""

from __future__ import annotations

import pytest

from carla_agentic_toolkit.managed_spec import ExperimentSpec
from scripts import managed_baseline


class Controller:
    """A deterministic public-controller boundary with recorded lifecycle requests."""

    def __init__(self, states: list[dict[str, object] | BaseException]) -> None:
        """Provide exactly the lifecycle responses exercised by one test."""
        self.states = iter(states)
        self.calls: list[str] = []

    def start(self, spec: ExperimentSpec) -> dict[str, object]:
        """Record launch without contacting a simulator or provider."""
        del spec
        self.calls.append("start")
        return {"run_id": "a" * 32, "state": "starting", "terminated": False}

    def status(self, run_id: str) -> dict[str, object]:
        """Return the next response or interruption at the public boundary."""
        del run_id
        self.calls.append("status")
        value = next(self.states)
        if isinstance(value, BaseException):
            raise value
        return value

    def stop(self, run_id: str) -> dict[str, object]:
        """Record cancellation while leaving termination unverified."""
        del run_id
        self.calls.append("stop")
        return {"state": "stopping", "terminated": False, "cancellation_requested": True}


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Advance only at the example's explicit polling boundary."""
    ticks = iter([0.0, 0.0, 0.0, 1000.0])
    monkeypatch.setattr(managed_baseline.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(managed_baseline.time, "sleep", lambda _seconds: None)


def test_successful_example_returns_verified_result(clock: None) -> None:
    """A complete lifecycle returns the original machine-readable engine result."""
    del clock
    terminal: dict[str, object] = {
        "state": "completed",
        "terminated": True,
        "cleanup": {"ok": True},
        "ok": True,
    }
    controller = Controller([terminal])
    assert managed_baseline.run_baseline(ExperimentSpec(), controller=controller) == terminal
    assert controller.calls == ["start", "status"]


def test_interrupt_requests_stop_and_waits_for_cleanup(clock: None) -> None:
    """Ctrl-C is a cancellation request, never proof of worker termination."""
    del clock
    terminal: dict[str, object] = {
        "state": "cancelled",
        "terminated": True,
        "cleanup": {"ok": True},
        "ok": False,
    }
    controller = Controller([KeyboardInterrupt(), terminal])
    assert managed_baseline.run_baseline(ExperimentSpec(), controller=controller) == terminal
    assert controller.calls == ["start", "status", "stop", "status"]


def test_example_failure_requests_stop_before_propagating(clock: None) -> None:
    """A caller-side failure must leave the detached owner with a stop request."""
    del clock
    controller = Controller([OSError("private status unavailable")])
    with pytest.raises(OSError, match="status unavailable"):
        managed_baseline.run_baseline(ExperimentSpec(), controller=controller)
    assert controller.calls == ["start", "status", "stop"]


def test_poll_deadline_keeps_unverified_termination_false(clock: None) -> None:
    """An unavailable supervisor cannot keep this example polling indefinitely."""
    del clock
    controller = Controller([{"state": "running", "terminated": False}] * 2)
    result = managed_baseline.run_baseline(ExperimentSpec(), controller=controller)
    assert (result["terminated"], result["example_wait_expired"], result["ok"]) == (
        False,
        True,
        False,
    )
    assert controller.calls[-1] == "stop"


def test_no_key_example_rejects_provider_before_launch() -> None:
    """The baseline cannot accidentally spend provider budget from a supplied spec."""
    controller = Controller([])
    with pytest.raises(ValueError, match="rules"):
        managed_baseline.run_baseline(ExperimentSpec(policy="jev"), controller=controller)
    assert controller.calls == []
