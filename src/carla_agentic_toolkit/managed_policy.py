"""Immutable contracts between observation, candidate selection, and actuation."""

from dataclasses import dataclass


def fallback_choice(phase: str) -> str:
    """Stop on an unanswered route choice; preserve committed merge semantics."""
    if phase == "route_following":
        return "yield"
    return "continue" if phase in {"committed", "settling"} else "defer"


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """Identity and freshness envelope captured at the observation boundary."""

    run_id: str
    world_generation: str
    actor_id: int
    revision: int
    observation_frame: int
    candidate_set_id: str
    maneuver_generation: int
    phase: str
    deadline_monotonic: float


@dataclass(frozen=True, slots=True)
class Candidate:
    """One code-validated maneuver; the provider can select only its ID."""

    candidate_id: str
    target_lane_id: int
    target_speed_mps: float
    horizon_seconds: float
    expires_frame: int


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    """Immutable numerical evidence with a nonempty, unambiguous choice set."""

    context: DecisionContext
    candidates: tuple[Candidate, ...]
    evidence_json: str

    def __post_init__(self) -> None:
        """Reject choice sets that cannot produce an identifiable decision."""
        if not self.candidates:
            msg = "Policy requests require a nonempty candidate set."
            raise ValueError(msg)
        identities = {candidate.candidate_id for candidate in self.candidates}
        if len(identities) != len(self.candidates):
            msg = "Policy candidate IDs must be unique."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Selection plus provenance; the supervisor revalidates before actuation."""

    context: DecisionContext
    choice_id: str
    source: str
    reason: str
    metadata_json: str = "{}"
