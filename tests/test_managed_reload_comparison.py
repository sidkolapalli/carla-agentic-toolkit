"""Reset-state matching retains native conditions without treating episode IDs as conditions."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

import pytest

from carla_agentic_toolkit.experiment_comparison import compare_traces
from tests.test_experiment_comparison import _save_run

if TYPE_CHECKING:
    from pathlib import Path


def _lights() -> dict[str, Any]:
    return {
        "frame": 4,
        "lights": [
            {
                "actor_id": 30,
                "opendrive_id": "signal-5",
                "pole_index": 2,
                "location": {"x": 1.25, "y": 2.5, "z": 3.0},
                "state": "Red",
            }
        ],
    }


def test_reload_ephemeral_light_fields_do_not_split_comparison_cohorts(tmp_path: Path) -> None:
    """Only light actor ID and setup frame change, with raw evidence preserved exactly."""
    first = _lights()
    second = deepcopy(first)
    second["frame"] = 9
    second["lights"][0]["actor_id"] = 31
    rules = _save_run(tmp_path, "rules-reload", "rules", fixture={"initial_traffic_lights": first})
    selector = _save_run(tmp_path, "jev-reload", "jev", fixture={"initial_traffic_lights": second})
    original = {path: path.read_bytes() for path in (rules, selector)}
    report = compare_traces([rules, selector])
    assert report["comparable"] is True
    assert report["runs"][0]["fixture"]["initial_traffic_lights"] == first
    identity = report["runs"][0]["matching_evidence"]["fixture"]["initial_traffic_lights"]
    _assert_single_light_identity(identity, first)
    _assert_unchanged(original)


def _assert_single_light_identity(identity: dict[str, Any], first: dict[str, Any]) -> None:
    stable = {key: value for key, value in first["lights"][0].items() if key != "actor_id"}
    assert identity == {"lights": [stable]}


def _assert_unchanged(original: dict[Path, bytes]) -> None:
    assert {path: path.read_bytes() for path in original} == original


@pytest.mark.parametrize(
    ("field", "value"),
    [("opendrive_id", "signal-6"), ("pole_index", 3), ("location", {"x": 9.0}), ("state", "Green")],
)
def test_changed_stable_native_light_evidence_still_blocks_comparison(
    tmp_path: Path, field: str, value: object
) -> None:
    """Reload normalization must never discard an actual recorded condition."""
    first = _lights()
    second = deepcopy(first)
    second["lights"][0][field] = value
    paths = [
        _save_run(tmp_path, "rules", "rules", fixture={"initial_traffic_lights": first}),
        _save_run(tmp_path, "jev", "jev", fixture={"initial_traffic_lights": second}),
    ]
    assert compare_traces(paths)["comparable"] is False


def test_historical_absence_is_not_fabricated_as_reset_evidence(tmp_path: Path) -> None:
    """Old runs may compare to old runs, but not to a newly reset recorded condition."""
    old = _save_run(tmp_path, "old-rules", "rules")
    new = _save_run(tmp_path, "new-jev", "jev", fixture={"initial_traffic_lights": _lights()})
    report = compare_traces([old, new])
    assert report["comparable"] is False
    assert "initial_traffic_lights" not in report["runs"][0]["matching_evidence"]["fixture"]


def test_ephemeral_inventory_permutation_preserves_stable_light_matching(tmp_path: Path) -> None:
    """Each actual light condition remains exact even when per-episode inventory order changes."""
    first = _lights()
    another = deepcopy(first["lights"][0])
    another.update(actor_id=99, opendrive_id="signal-6", state="Green")
    first["lights"].append(another)
    second = deepcopy(first)
    second["lights"].reverse()
    second["lights"][0]["actor_id"] = 100
    second["lights"][1]["actor_id"] = 101
    paths = [
        _save_run(tmp_path, "rules-order", "rules", fixture={"initial_traffic_lights": first}),
        _save_run(tmp_path, "jev-order", "jev", fixture={"initial_traffic_lights": second}),
    ]
    original = {path: path.read_bytes() for path in paths}
    report = compare_traces(paths)
    assert report["comparable"] is True
    assert report["runs"][1]["fixture"]["initial_traffic_lights"] == second
    _assert_unchanged(original)
