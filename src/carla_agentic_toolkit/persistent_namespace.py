"""Persistent execution reuses the finite script validator, builtins, and serialization."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from carla_agentic_toolkit.script_runner import (
    MAX_SCRIPT_STDOUT_BYTES,
    _BoundedWriter,
    _error,
    _safe_builtins,
    _script_outcome,
    _validate_script,
)
from carla_agentic_toolkit.snapshots import RunSnapshots

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_SESSION_SNAPSHOTS = 32


class SessionSnapshots(RunSnapshots):
    """Retain a bounded latest set while preserving the existing snapshot API."""

    def register_snapshot(self, uri: str, payload: Mapping[str, object]) -> None:
        """Evict the oldest snapshot before admitting another distinct URI."""
        if uri not in self._snapshots and len(self._snapshots) >= MAX_SESSION_SNAPSHOTS:
            del self._snapshots[next(iter(self._snapshots))]
        super().register_snapshot(uri, payload)


class PersistentNamespace:
    """Keep user variables; reassert trusted builtins/API and clear each request's result."""

    def __init__(self, api: object, snapshots: RunSnapshots | None = None) -> None:
        """Bind the one session API and its bounded snapshot storage."""
        self._api = api
        self._snapshots = snapshots if snapshots is not None else SessionSnapshots()
        self._namespace: dict[str, object] = {}

    def execute(self, code: str) -> dict[str, object]:
        """Validate each request before executing in the same restricted namespace."""
        rejection = _validate_script(code)
        if rejection is not None:
            return _error("script_rejected", rejection, stdout="")
        self._namespace.update(__builtins__=_safe_builtins(), api=self._api, result=None)
        stream = _BoundedWriter(MAX_SCRIPT_STDOUT_BYTES)
        error = self._execute(code, stream)
        outcome = (
            error
            if error is not None
            else _script_outcome(self._namespace.get("result"), stream.getvalue(), self._snapshots)
        )
        finalize = getattr(self._api, "_finalize_owned_outcome", None)
        return finalize(outcome) if callable(finalize) else outcome

    def _execute(self, code: str, stream: _BoundedWriter) -> dict[str, object] | None:
        try:
            with contextlib.redirect_stdout(stream):
                # Validated generated code runs only inside the existing OS sandbox.
                exec(compile(code, "<carla-session>", "exec"), self._namespace)  # noqa: S102
        except Exception as exc:  # noqa: BLE001 - return arbitrary user-code exceptions.
            return _error(type(exc).__name__, str(exc), stdout=stream.getvalue())
        if stream.truncated:
            return _error(
                "output_too_large",
                "Script stdout exceeded its byte limit.",
                stdout=stream.getvalue(),
            )
        return None
