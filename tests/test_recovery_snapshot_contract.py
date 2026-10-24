"""Preserve script recovery's existing fresh-frame and error contract during sharing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

from carla_agentic_toolkit import script_recovery

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaWorld

SCRIPT_FAILURE = "Script cleanup could not verify a fresh actor snapshot."
INITIAL_FRAME = 100
EXPECTED_SNAPSHOT_READS = 2


def _world(*, synchronous: bool, returned: int, published: int) -> SimpleNamespace:
    return SimpleNamespace(
        get_snapshot=Mock(
            side_effect=[SimpleNamespace(frame=INITIAL_FRAME), SimpleNamespace(frame=published)]
        ),
        get_settings=Mock(return_value=SimpleNamespace(synchronous_mode=synchronous)),
        tick=Mock(return_value=returned),
        wait_for_tick=Mock(return_value=SimpleNamespace(frame=returned)),
    )


def _script_snapshot(world: SimpleNamespace) -> None:
    return script_recovery._fresh_cleanup_snapshot(cast("CarlaWorld", world))  # noqa: SLF001


def _assert_mode_call(world: SimpleNamespace, *, synchronous: bool) -> None:
    if synchronous:
        world.tick.assert_called_once_with()
        world.wait_for_tick.assert_not_called()
    else:
        world.wait_for_tick.assert_called_once_with(5.0)
        world.tick.assert_not_called()


@pytest.mark.parametrize("synchronous", [False, True])
@pytest.mark.parametrize("published", [101, 103])
def test_script_snapshot_accepts_returned_frame_or_a_newer_publication(
    *, synchronous: bool, published: int
) -> None:
    """Async progress beyond an acknowledged frame cannot invalidate fresh recovery state."""
    world = _world(synchronous=synchronous, returned=101, published=published)

    assert _script_snapshot(world) is None

    _assert_mode_call(world, synchronous=synchronous)
    assert world.get_snapshot.call_count == EXPECTED_SNAPSHOT_READS


@pytest.mark.parametrize("synchronous", [False, True])
@pytest.mark.parametrize(("returned", "published"), [(100, 100), (99, 101), (102, 101)])
def test_script_snapshot_rejects_no_progress_or_unpublished_returned_frame(
    *, synchronous: bool, returned: int, published: int
) -> None:
    """Failure preserves the exact private-wrapper RuntimeError message in either mode."""
    world = _world(synchronous=synchronous, returned=returned, published=published)

    with pytest.raises(RuntimeError) as failure:
        _script_snapshot(world)

    assert str(failure.value) == SCRIPT_FAILURE
    _assert_mode_call(world, synchronous=synchronous)


@pytest.mark.parametrize("synchronous", [False, True])
def test_script_snapshot_preserves_native_frame_delivery_errors(*, synchronous: bool) -> None:
    """Bounded async wait and synchronous tick errors are propagated without rewriting them."""
    world = _world(synchronous=synchronous, returned=101, published=101)
    error = RuntimeError("native fresh-frame delivery failed")
    delivery = world.tick if synchronous else world.wait_for_tick
    delivery.side_effect = error

    with pytest.raises(RuntimeError) as failure:
        _script_snapshot(world)

    assert failure.value is error
    _assert_mode_call(world, synchronous=synchronous)


@pytest.mark.parametrize("synchronous", [False, True])
def test_script_snapshot_preserves_native_publication_read_errors(*, synchronous: bool) -> None:
    """The final publication check remains a single native read, not a retry loop."""
    world = _world(synchronous=synchronous, returned=101, published=101)
    error = RuntimeError("native snapshot publication read failed")
    world.get_snapshot.side_effect = [SimpleNamespace(frame=INITIAL_FRAME), error]

    with pytest.raises(RuntimeError) as failure:
        _script_snapshot(world)

    assert failure.value is error
    _assert_mode_call(world, synchronous=synchronous)
    assert world.get_snapshot.call_count == EXPECTED_SNAPSHOT_READS
