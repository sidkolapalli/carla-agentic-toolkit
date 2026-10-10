"""Private persistent-session connection hooks, absent from curated API discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.persistent_connection import operation_episode_guard

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.carla_protocols import CarlaWorld
    from carla_agentic_toolkit.models import JsonObject


def adapter_hook(adapter: object, name: str, *args: object) -> object:
    """Use declared native hooks, never fabricate capabilities on dynamic adapter doubles."""
    if callable(getattr(type(adapter), name, None)):
        return getattr(adapter, name)(*args)
    return None


def begin_persistent_request(adapter: object) -> None:
    """Enable the native persistent policy without touching a world or resetting evidence."""
    adapter_hook(adapter, "_enable_persistent_connection")
    adapter_hook(adapter, "_begin_persistent_request")


def guarded_operation(adapter: object, operation: Callable[[], JsonObject]) -> JsonObject:
    """Scope post-lookup episode verification to this facade call alone."""
    guard = cast(
        "Callable[[CarlaWorld], None] | None", adapter_hook(adapter, "_persistent_operation_guard")
    )
    with operation_episode_guard(guard):
        return operation()


def record_operation_error(adapter: object, error: Exception) -> Exception:
    """Keep the original native cause and any terminal episode classification."""
    normalized = adapter_hook(adapter, "_record_persistent_failure", error)
    return normalized if isinstance(normalized, Exception) else error


def persistent_enabled(adapter: object) -> bool:
    """Distinguish persistent-only native normalization from finite execution."""
    return adapter_hook(adapter, "_persistent_operation_guard") is not None


def finish_persistent_outcome(adapter: object, outcome: dict[str, object]) -> dict[str, object]:
    """Retain terminal connection failure even when script code ignored an API error."""
    value = adapter_hook(adapter, "_finish_persistent_outcome", outcome)
    return cast("dict[str, object]", value) if isinstance(value, dict) else outcome
