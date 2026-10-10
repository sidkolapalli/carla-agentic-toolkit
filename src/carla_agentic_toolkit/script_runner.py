"""Child-process runner for sandboxed CARLA scripts."""

from __future__ import annotations

import argparse
import ast
import builtins
import contextlib
import io
import json
import math
import runpy
import sys
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.creation_health import finish_creation_outcome
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.ownership import RunOwnership, cleanup_owned_actors, cleanup_report
from carla_agentic_toolkit.rpc_timeouts import RUN_DEADLINE_FILENAME, RpcTimeoutPolicy, run_deadline
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.script_settings import SETTINGS_FILENAME, RunSettings
from carla_agentic_toolkit.snapshots import RunSnapshots

RESULT_NAME = "result"
API_NAME = "api"
# Bound captured UTF-8 output separately from the Rust 1 MiB JSON pipe cap.
MAX_SCRIPT_STDOUT_BYTES = 1024 * 512

_FORBIDDEN_NAMES = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "memoryview",
        "open",
        "setattr",
        "vars",
    }
)


class _BoundedWriter(io.StringIO):
    """A StringIO that stops retaining data after a byte limit.

    Writes beyond the limit are silently dropped. The Rust runner has
    its own independent 1 MiB cap on pipe output.
    """

    def __init__(self, max_bytes: int) -> None:
        super().__init__()
        self._max_bytes = max_bytes
        self._bytes_written = 0
        self.truncated: bool = False

    def write(self, s: str) -> int:
        if self.truncated:
            return len(s)
        remaining = self._max_bytes - self._bytes_written
        # Slicing first avoids encoding a potentially enormous print argument.
        encoded = s[: remaining + 1].encode("utf-8", errors="surrogatepass")
        if len(encoded) > remaining:
            self.truncated = True
            super().write(encoded[:remaining].decode("utf-8", errors="ignore"))
            return len(s)
        self._bytes_written += len(encoded)
        return super().write(s)


_SAFE_BUILTIN_NAMES = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "dict",
        "divmod",
        "enumerate",
        "filter",
        "float",
        "frozenset",
        "int",
        "len",
        "list",
        "map",
        "max",
        "min",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
    }
)


def main() -> None:
    """Run one script and print a JSON result."""
    args = _parse_args()
    outcome = run_script_file(
        script_path=args.script,
        host=args.host,
        port=args.port,
        timeout_seconds=args.timeout_seconds,
        ownership_path=args.ownership_file,
        require_settings_journal=args.ownership_file is not None,
        require_rpc_deadline=args.ownership_file is not None,
    )
    sys.stdout.write(json.dumps(outcome, sort_keys=True, allow_nan=False) + "\n")


def run_script_file(  # noqa: PLR0913 - direct callers may omit parent-initialized recovery evidence.
    *,
    script_path: Path,
    host: str,
    port: int,
    timeout_seconds: float,
    ownership_path: Path | None = None,
    require_settings_journal: bool = False,
    require_rpc_deadline: bool = False,
) -> dict[str, object]:
    """Execute one script file with a curated CARLA API object."""
    policy = _execution_rpc_policy(script_path, timeout_seconds, required=require_rpc_deadline)
    if isinstance(policy, dict):
        return cast("dict[str, object]", policy)
    code = script_path.read_text(encoding="utf-8")
    rejection = _validate_script(code)
    if rejection is not None:
        return _error("script_rejected", rejection, stdout="")
    snapshots = RunSnapshots()
    settings = RunSettings(
        _settings_path(ownership_path), require_existing=require_settings_journal
    )
    adapter = PythonCarlaAdapter(
        host=host,
        port=port,
        timeout=min(timeout_seconds, 10.0),
        settings_journal=settings,
        rpc_timeout_policy=policy,
    )
    ownership = _run_ownership(ownership_path)
    api = CarlaScriptApi(adapter=adapter, snapshots=snapshots, ownership=ownership)
    stream = _BoundedWriter(MAX_SCRIPT_STDOUT_BYTES)
    listener_errors: list[str] = []
    try:
        with (
            contextlib.redirect_stdout(stream),
            _sensor_listener_lifetime(adapter, listener_errors),
        ):
            globals_after_run = runpy.run_path(
                str(script_path),
                init_globals={
                    "__builtins__": _safe_builtins(),
                    API_NAME: api,
                    RESULT_NAME: None,
                },
            )
    except Exception as exc:
        restored = _restore_execution_settings(api, adapter, settings)
        outcome = _error(
            type(exc).__name__,
            str(exc),
            stdout=stream.getvalue(),
        )
        return _finish_execution(outcome, adapter, ownership, restored, listener_errors)
    restored = _restore_execution_settings(api, adapter, settings)
    if stream.truncated:
        outcome = _error(
            "output_too_large",
            f"Script stdout exceeded {MAX_SCRIPT_STDOUT_BYTES} bytes.",
            stdout=stream.getvalue(),
        )
    else:
        outcome = _finished_script(globals_after_run, stream, snapshots, listener_errors)
    return _finish_execution(outcome, adapter, ownership, restored, listener_errors)


def _settings_path(ownership_path: Path | None) -> Path | None:
    return ownership_path.with_name(SETTINGS_FILENAME) if ownership_path is not None else None


def _run_ownership(path: Path | None) -> RunOwnership | None:
    return RunOwnership(path) if path is not None else None


def _execution_rpc_policy(
    script_path: Path, timeout_seconds: float, *, required: bool
) -> RpcTimeoutPolicy | dict[str, object]:
    try:
        return RpcTimeoutPolicy(
            absolute_deadline=run_deadline(
                script_path.with_name(RUN_DEADLINE_FILENAME), timeout_seconds, required=required
            )
        )
    except (OSError, ValueError, TypeError) as exc:
        return _error("execution_deadline_invalid", str(exc), stdout="")


def _restore_execution_settings(
    api: CarlaScriptApi, adapter: PythonCarlaAdapter, settings: RunSettings
) -> dict[str, object]:
    """Stop background mutators before restoring the first settings baseline."""
    try:
        api.close()
    except CarlaAdapterError as exc:
        failure: dict[str, object] = {"actor_id": None, "error": str(exc)}
        return cleanup_report(failures=(failure,)) | {"settings_restored": False}
    return settings.restore(adapter)


def _finish_execution(
    outcome: dict[str, object],
    adapter: PythonCarlaAdapter,
    ownership: RunOwnership | None,
    restored: dict[str, object],
    listener_errors: list[str],
) -> dict[str, object]:
    """Keep success actors while retaining every terminal cleanup failure."""
    outcome = finish_creation_outcome(outcome, ownership)
    if restored.get("settings_restored") is False:
        if outcome["ok"]:
            outcome = _error(
                "settings_restore_failed", str(restored["failures"]), stdout=str(outcome["stdout"])
            )
        cleanup = restored
    elif not outcome["ok"]:
        cleanup = cleanup_owned_actors(adapter, ownership) | _settings_evidence(restored)
    else:
        cleanup = restored
    outcome["cleanup"] = _listener_cleanup_evidence(cleanup, listener_errors)
    return outcome


def _settings_evidence(restored: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in restored.items()
        if key not in {"attempted_actor_ids", "destroyed_actor_ids", "failures"}
    }


@contextlib.contextmanager
def _sensor_listener_lifetime(adapter: PythonCarlaAdapter, errors: list[str]) -> Iterator[None]:
    """Release listener storage without hiding an original script failure."""
    try:
        yield
    finally:
        try:
            adapter.close_sensor_subscriptions()
        except CarlaAdapterError as exc:
            errors.append(str(exc))


def _listener_cleanup_evidence(
    cleanup: dict[str, object], listener_errors: list[str]
) -> dict[str, object]:
    """Include cleanup failures only when a listener could not be stopped."""
    if listener_errors:
        cleanup["sensor_failures"] = listener_errors
    return cleanup


def _finished_script(
    globals_after_run: dict[str, object],
    stream: _BoundedWriter,
    snapshots: RunSnapshots,
    listener_errors: list[str],
) -> dict[str, object]:
    """Reject a nominal success if listener cleanup failed."""
    if listener_errors:
        return _error("sensor_cleanup_failed", "; ".join(listener_errors), stdout=stream.getvalue())
    return _script_outcome(globals_after_run.get(RESULT_NAME), stream.getvalue(), snapshots)


def _script_outcome(result: object, stdout: str, snapshots: RunSnapshots) -> dict[str, object]:
    """Serialize a successful execution or return its conversion failure."""
    try:
        result_value = _jsonable(result, _state=_JsonState(), _depth=0)
    except Exception as exc:
        return _error(
            "result_not_serializable",
            f"{type(exc).__name__}: {exc}",
            stdout=stdout,
        )
    return {
        "ok": True,
        "result": result_value,
        "stdout": stdout,
        "error": None,
        "error_type": None,
        "snapshots": _snapshots(snapshots),
        "cleanup": cleanup_report(),
    }


def _parse_args() -> argparse.Namespace:
    """Parse runner arguments."""
    parser = argparse.ArgumentParser(description="Run one sandboxed CARLA script.")
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=2000, type=int)
    parser.add_argument("--timeout-seconds", default=30.0, type=float)
    parser.add_argument("--ownership-file", type=Path)
    return parser.parse_args()


def _validate_script(code: str) -> str | None:
    """Reject script features that are unnecessary for CARLA scenarios."""
    try:
        tree = ast.parse(code, filename="<carla-script>", mode="exec")
    except SyntaxError as exc:
        return f"Script has a syntax error: {exc.msg}."
    for node in ast.walk(tree):
        violation = _node_violation(node)
        if violation is not None:
            return violation
    return None


def _node_violation(node: ast.AST) -> str | None:
    """Return a rejection message for one unsafe AST node."""
    for check in (_import_violation, _unsafe_attribute_violation, _forbidden_name_violation):
        violation = check(node)
        if violation is not None:
            return violation
    return None


def _import_violation(node: ast.AST) -> str | None:
    """Return a rejection message for import nodes."""
    if isinstance(node, ast.Import | ast.ImportFrom):
        return "Imports are not allowed; use the injected `api` object."
    return None


def _unsafe_attribute_violation(node: ast.AST) -> str | None:
    """Return a rejection message for private or indirect attribute access."""
    if not isinstance(node, ast.Attribute):
        return None
    if node.attr.startswith("_"):
        return f"Access to private attribute '{node.attr}' is not allowed."
    if node.attr in ("format", "format_map"):
        return "String format attribute traversal is not allowed; use an f-string."
    return None


def _forbidden_name_violation(node: ast.AST) -> str | None:
    """Return a rejection message for forbidden builtins and introspection names."""
    if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
        return f"Use of '{node.id}' is not allowed."
    return None


def _snapshots(snapshots: RunSnapshots) -> dict[str, object]:
    """Return run-local snapshots created by the script."""
    return {uri: snapshots.read_snapshot(uri) for uri in snapshots.snapshot_uris()}


def _error(
    error_type: str,
    message: str,
    *,
    stdout: str,
    cleanup: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return a JSON-compatible error outcome."""
    return {
        "ok": False,
        "result": None,
        "stdout": stdout,
        "error": message,
        "error_type": error_type,
        "snapshots": {},
        "cleanup": cleanup or cleanup_report(),
    }


def _safe_builtins() -> dict[str, object]:
    """Return builtins available to scenario scripts."""
    return {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}


MAX_JSON_DEPTH = 50
MAX_JSON_ITEMS = 10_000


@dataclass
class _JsonState:
    """Share a conversion budget and active-container IDs across the result."""

    seen: set[int] = field(default_factory=set)
    item_count: int = 0

    def claim_items(self, count: int) -> None:
        """Reject oversized results before traversing the next container."""
        self.item_count += count
        if self.item_count > MAX_JSON_ITEMS:
            msg = f"Result exceeded maximum item count of {MAX_JSON_ITEMS}."
            raise ValueError(msg)


def _jsonable(value: object, *, _state: _JsonState, _depth: int) -> object:
    """Coerce a script result into a JSON-compatible value.

    Detects cycles, enforces depth and item limits, rejects NaN/Infinity,
    and sorts unordered collections for deterministic output.
    """
    if value is None or isinstance(value, bool | int | float | str):
        return _jsonable_scalar(value)
    if isinstance(value, Mapping):
        return _jsonable_mapping(
            cast("Mapping[object, object]", value), _state=_state, _depth=_depth
        )
    if isinstance(value, list | tuple | set | frozenset):
        return _jsonable_iterable(value, _state=_state, _depth=_depth)
    return repr(value)


def _jsonable_scalar(value: object) -> object:
    """Reject floating-point values that JSON cannot represent."""
    if isinstance(value, float) and not math.isfinite(value):
        msg = "Result cannot contain NaN or Infinity."
        raise ValueError(msg)
    return value


@contextlib.contextmanager
def _json_container(
    value: Collection[object], *, _state: _JsonState, _depth: int
) -> Iterator[None]:
    """Enforce common collection bounds and track cycles only on the active path."""
    if _depth > MAX_JSON_DEPTH:
        msg = f"Result exceeded maximum nesting depth of {MAX_JSON_DEPTH}."
        raise RecursionError(msg)
    key = id(value)
    if key in _state.seen:
        msg = "Result contains a cycle."
        raise ValueError(msg)
    _state.claim_items(len(value))
    _state.seen.add(key)
    try:
        yield
    finally:
        _state.seen.remove(key)


def _jsonable_mapping(
    value: Mapping[object, object], *, _state: _JsonState, _depth: int
) -> dict[str, object]:
    """Coerce a mapping into a JSON-compatible dict."""
    with _json_container(value, _state=_state, _depth=_depth):
        return {str(k): _jsonable(v, _state=_state, _depth=_depth + 1) for k, v in value.items()}


def _jsonable_iterable(
    value: Collection[object], *, _state: _JsonState, _depth: int
) -> list[object]:
    """Coerce an iterable into a JSON-compatible list.

    Unordered collections (sets) are sorted for deterministic output.
    """
    with _json_container(value, _state=_state, _depth=_depth):
        items = sorted(value, key=repr) if isinstance(value, set | frozenset) else value
        return [_jsonable(item, _state=_state, _depth=_depth + 1) for item in items]


if __name__ == "__main__":
    main()
