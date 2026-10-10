"""Non-ticking server acknowledgements shared by script and managed cleanup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.models import DestroyResult

if TYPE_CHECKING:
    from collections.abc import Callable


class DestroyBatch(Protocol):
    """The existing JSON-compatible batch boundary, without an adapter dependency."""

    def __call__(self, commands: list[dict[str, object]], *, do_tick: bool) -> dict[str, object]:
        """Apply a destruction command and return its server response."""
        ...


def destroy_authoritatively(
    actor_id: int,
    *,
    expected_world_id: int,
    current_world_id: Callable[[], int],
    apply_batch: DestroyBatch,
) -> DestroyResult:
    """Destroy one ID only while its originating episode remains current; never tick."""
    try:
        _require_cleanup_episode(expected_world_id, current_world_id)
        payload = apply_batch([{"action": "destroy_actor", "actor_id": actor_id}], do_tick=False)
        _require_cleanup_episode(expected_world_id, current_world_id)
        return destroy_batch_result(actor_id, payload)
    except (CarlaAdapterError, AttributeError, RuntimeError, TypeError, ValueError) as exc:
        return DestroyResult(
            actor_id, destroyed=False, error=f"Authoritative actor cleanup failed: {exc}"
        )


def _require_cleanup_episode(expected: int, current_world_id: Callable[[], int]) -> None:
    current = current_world_id()
    if type(current) is not int or current != expected:
        message = "CARLA world episode changed during authoritative actor cleanup."
        raise CarlaAdapterError(message)


def destroy_result_cleaned(result: DestroyResult) -> bool:
    """Authoritative absence is clean, but is not a newly completed destruction."""
    return result.destroyed or result.error == "Actor was not found."


def destroy_batch_result(actor_id: int, payload: dict[str, object]) -> DestroyResult:
    """Require one matching server acknowledgement; incomplete responses retain ownership."""
    response = _single_destroy_response(payload)
    if not isinstance(response, dict):
        return DestroyResult(actor_id, destroyed=False, error="Invalid destroy response identity.")
    typed_response = cast("dict[str, object]", response)
    if not _valid_destroy_error(typed_response):
        return DestroyResult(actor_id, destroyed=False, error="Invalid destroy response error.")
    error = _destroy_response_error(typed_response)
    if error:
        return DestroyResult(actor_id, destroyed=False, error=error)
    if not _destroy_response_matches(typed_response.get("actor_id"), actor_id):
        return DestroyResult(actor_id, destroyed=False, error="Invalid destroy response identity.")
    return DestroyResult(actor_id, destroyed=True, error=None)


def _valid_destroy_error(response: dict[str, object]) -> bool:
    error = response.get("error")
    return "error" in response and (error is None or isinstance(error, str))


def _destroy_response_matches(value: object, actor_id: int) -> bool:
    return type(value) is int and value > 0 and value == actor_id


def _destroy_response_error(response: dict[str, object]) -> str | None:
    error = response.get("error")
    if error == "unable to destroy actor: not found":
        return "Actor was not found."
    if error == "Actor was not found.":
        return f"Server destroy failed: {error}"
    return str(error) if error else None


def _single_destroy_response(payload: dict[str, object]) -> object:
    responses = payload.get("responses")
    if not isinstance(responses, list) or len(responses) != 1:
        message = "Invalid destroy response count."
        raise CarlaAdapterError(message)
    return responses[0]
