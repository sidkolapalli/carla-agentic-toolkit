"""Managed repetitions are labelled honestly without implying randomized conditions."""

from __future__ import annotations

import json
from enum import IntEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from carla_agentic_toolkit import managed_engine
from carla_agentic_toolkit.experiment_trace import load_trace
from carla_agentic_toolkit.managed_spec import ExperimentSpec
from carla_agentic_toolkit.replicate_index import normalize_replicate_index
from tests.test_managed_engine import PendingPolicy, _patch_engine
from tests.test_merge_experiment import _prepare

if TYPE_CHECKING:
    from pathlib import Path

MAX_REPLICATE_INDEX = 2**31 - 1


class ReplicateNumber(IntEnum):
    """Integer-shaped external values still lack the required exact input type."""

    FIRST = 1


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, {}),
        ({"policy": "rules"}, {"policy": "rules"}),
        ({"seed": 19, "policy": "rules"}, {"replicate_index": 19, "policy": "rules"}),
    ],
)
def test_alias_normalizer_copies_read_only_input_without_defaulting_evidence(
    values: dict[str, object], expected: dict[str, object]
) -> None:
    """Comparison can normalize historical names without completing incomplete evidence."""
    original = values.copy()
    observed = normalize_replicate_index(MappingProxyType(values))
    assert observed == expected
    assert values == original
    assert observed is not values


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({}, 7),
        ({"seed": 0}, 0),
        ({"seed": 19}, 19),
        ({"seed": MAX_REPLICATE_INDEX}, MAX_REPLICATE_INDEX),
        ({"replicate_index": 0}, 0),
        ({"replicate_index": 19}, 19),
        ({"replicate_index": MAX_REPLICATE_INDEX}, MAX_REPLICATE_INDEX),
        ({"replicate_index": 0, "seed": 0}, 0),
        ({"replicate_index": 19, "seed": 19}, 19),
        (
            {"replicate_index": MAX_REPLICATE_INDEX, "seed": MAX_REPLICATE_INDEX},
            MAX_REPLICATE_INDEX,
        ),
    ],
)
def test_spec_serializes_only_the_canonical_replicate_index(
    values: dict[str, object], expected: int
) -> None:
    """Historical seed input remains readable, but new saved data has one honest name."""
    original = values.copy()
    spec = ExperimentSpec.model_validate(values)

    _assert_canonical_replicate(spec.model_dump(), expected)
    _assert_canonical_replicate(json.loads(spec.model_dump_json()), expected)
    assert spec.seed == expected
    assert values == original


def _assert_canonical_replicate(values: dict[str, object], expected: int) -> None:
    assert values["replicate_index"] == expected
    assert "seed" not in values


@pytest.mark.parametrize("field", ["seed", "replicate_index"])
@pytest.mark.parametrize("value", [True, False, 1.0, "1", None, -1, 2**31])
def test_each_replicate_alias_requires_an_exact_bounded_integer(field: str, value: object) -> None:
    """Neither spelling accepts coercion or Python's bool-as-int equality."""
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({field: value})


@pytest.mark.parametrize("field", ["seed", "replicate_index"])
def test_replicate_alias_rejects_integer_subclasses(field: str) -> None:
    """An exact integer contract cannot silently normalize an enum into evidence."""
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate({field: ReplicateNumber.FIRST})


@pytest.mark.parametrize(
    "values",
    [
        {"replicate_index": 7, "seed": 19},
        {"replicate_index": 1, "seed": True},
        {"replicate_index": True, "seed": 1},
        {"replicate_index": 0, "seed": False},
        {"replicate_index": False, "seed": 0},
        {"replicate_index": 1, "seed": 1.0},
        {"replicate_index": 1.0, "seed": 1},
        {"replicate_index": 1, "seed": "1"},
        {"replicate_index": "1", "seed": 1},
        {"replicate_index": 7, "seed": -1},
        {"replicate_index": 2**31, "seed": 7},
        {"replicate_index": None, "seed": 7},
        {"replicate_index": 7, "seed": None},
    ],
)
def test_matching_dual_input_must_validate_both_aliases(values: dict[str, object]) -> None:
    """A valid canonical label cannot hide an invalid or conflicting legacy value."""
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate(values)


def test_spec_schema_has_only_the_canonical_replicate_field() -> None:
    """New clients discover replicate semantics without a false random-seed field."""
    properties = ExperimentSpec.model_json_schema()["properties"]
    assert "seed" not in properties
    assert properties["replicate_index"] == {
        "default": 7,
        "maximum": MAX_REPLICATE_INDEX,
        "minimum": 0,
        "title": "Replicate Index",
        "type": "integer",
    }


@pytest.mark.parametrize("field", ["replicate_index", "seed"])
def test_replicate_identity_and_legacy_property_are_read_only(field: str) -> None:
    """The accepted compatibility spelling cannot mutate an immutable run spec."""
    spec = ExperimentSpec()
    with pytest.raises(ValidationError):
        setattr(spec, field, 19)
    _assert_canonical_replicate(spec.model_dump(), 7)


def test_json_spec_preserves_strict_legacy_alias_validation() -> None:
    """JSON loading uses the same dual-name validation as Python mappings."""
    spec = ExperimentSpec.model_validate_json('{"seed":19,"replicate_index":19}')
    _assert_canonical_replicate(spec.model_dump(), 19)
    with pytest.raises(ValidationError):
        ExperimentSpec.model_validate_json('{"seed":true,"replicate_index":1}')


def test_managed_metadata_records_replicate_without_random_seed_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The actual engine writes canonical top-level and saved-spec trace identities."""
    _patch_engine(monkeypatch, PendingPolicy())
    monkeypatch.setattr(
        managed_engine, "create_policy", lambda _spec, _root: managed_engine.RulesPolicy()
    )
    result = managed_engine.run_experiment(
        ExperimentSpec.model_validate({"seed": 19, "max_steps": 1}),
        "replicate-metadata",
        state_root=tmp_path,
        cancelled=lambda: False,
        publish_status=lambda _status: None,
    )
    assert result["state"] == "completed"
    read = load_trace(tmp_path / "runs/replicate-metadata/events.jsonl")
    metadata = next(event["data"] for event in read.events if event["kind"] == "metadata")
    _assert_canonical_replicate(metadata, 19)
    _assert_canonical_replicate(cast("dict[str, object]", metadata["spec"]), 19)


@pytest.mark.parametrize("replicate", [7, 19, 31])
def test_fixture_metadata_labels_repeated_fixed_initial_conditions(
    monkeypatch: pytest.MonkeyPatch, replicate: int
) -> None:
    """A repetition label does not introduce RNG, Traffic Manager, or pose variation."""
    session, experiment = _prepare(monkeypatch, ExperimentSpec.model_validate({"seed": replicate}))
    baseline_session, baseline = _prepare(monkeypatch)
    metadata = experiment.fixture_metadata()
    _assert_canonical_replicate(metadata, replicate)
    _assert_fixed_initial_conditions(metadata, baseline.fixture_metadata())
    assert [actor.location for actor in session.world.actors] == [
        actor.location for actor in baseline_session.world.actors
    ]


def _assert_fixed_initial_conditions(
    observed: dict[str, object], baseline: dict[str, object]
) -> None:
    for field in ("policy_start", "ego_start", "target_start", "settings"):
        assert observed[field] == baseline[field]
