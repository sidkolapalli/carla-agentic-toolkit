"""Immutable policy boundary contracts shared by local and remote selectors."""

from dataclasses import FrozenInstanceError

import pytest

from carla_agentic_toolkit.managed_policy import Candidate, DecisionContext, PolicyRequest


def context() -> DecisionContext:
    """Return one fully scoped decision identity."""
    return DecisionContext("run", "world", 1, 2, 3, "set", 4, "preparing", 123.0)


def test_context_cannot_be_mutated() -> None:
    """Requests preserve the world identity captured before an inference."""
    captured = context()
    with pytest.raises(FrozenInstanceError):
        setattr(captured, "world_generation", "replacement")  # noqa: B010


@pytest.mark.parametrize(
    "candidates",
    [(), (Candidate("defer", 1, 0.0, 1.0, 5), Candidate("defer", 2, 0.0, 1.0, 5))],
)
def test_request_rejects_empty_or_ambiguous_choices(candidates: tuple[Candidate, ...]) -> None:
    """A policy always selects from a nonempty set of stable unique IDs."""
    with pytest.raises(ValueError, match=r"nonempty|unique"):
        PolicyRequest(context(), candidates, "{}")
