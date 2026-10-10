"""Validate actor lifecycle evidence before releasing durable ownership."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Sequence

    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.ownership import RunOwnership


def release_controller_destroyed(
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership | None,
    actor_ids: tuple[int, ...],
    world_id: int | None,
) -> None:
    """Release confirmed trims only while their originating episode remains current."""
    if ownership is None or type(world_id) is not int:
        return
    if adapter.get_world_identity() == world_id:
        ownership.discard(actor_ids, world_id=world_id)


def release_batch_destroyed(
    ownership: RunOwnership | None,
    payload: dict[str, object],
    *,
    commands: list[dict[str, object]] | None = None,
) -> None:
    """Remove successful, matching destroy-actor batch responses from a journal."""
    if ownership is not None:
        ownership.discard(_successful_batch_ids(payload.get("responses"), commands))


def _successful_batch_ids(
    value: object, commands: list[dict[str, object]] | None
) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    if commands is None:
        return _response_actor_ids(value)
    return _matching_batch_ids(value, commands)


def _matching_batch_ids(
    responses: Sequence[object], commands: list[dict[str, object]]
) -> tuple[int, ...]:
    if len(responses) != len(commands):
        return ()
    matched = [
        response
        for response, command in zip(responses, commands, strict=True)
        if _matches_destroy_command(response, command)
    ]
    return _response_actor_ids(matched)


def _response_actor_ids(responses: Sequence[object]) -> tuple[int, ...]:
    actor_ids = (_successful_response_id(response) for response in responses)
    return tuple(actor_id for actor_id in actor_ids if actor_id is not None)


def _matches_destroy_command(response: object, command: dict[str, object]) -> bool:
    actor_id = _successful_response_id(response)
    requested = command.get("actor_id")
    return (
        command.get("action") == "destroy_actor" and _actor_id(requested) and actor_id == requested
    )


def _successful_response_id(value: object) -> int | None:
    if not isinstance(value, dict) or value.get("error"):
        return None
    actor_id = value.get("actor_id")
    return cast("int", actor_id) if _actor_id(actor_id) else None


def _actor_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0
