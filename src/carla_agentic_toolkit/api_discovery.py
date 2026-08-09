"""Runtime discovery for the curated script facade."""

from __future__ import annotations

from inspect import signature
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from carla_agentic_toolkit.models import JsonObject


def method_catalog(api: object) -> JsonObject:
    """Return signatures and summaries for every public facade method."""
    return {
        name: _method_description(getattr(api, name))
        for name in dir(api)
        if callable(getattr(api, name)) and not name.startswith("_")
    }


def _method_description(method: object) -> JsonObject:
    doc = getattr(method, "__doc__", "") or ""
    typed_method = cast("Callable[..., object]", method)
    return {
        "signature": str(signature(typed_method)),
        "doc": doc.strip().splitlines()[0] if doc.strip() else "",
    }
