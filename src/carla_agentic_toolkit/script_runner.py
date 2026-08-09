"""Child-process runner for sandboxed CARLA scripts."""

from __future__ import annotations

import argparse
import ast
import builtins
import contextlib
import io
import json
import runpy
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import cast

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.ownership import RunOwnership, cleanup_owned_actors, cleanup_report
from carla_agentic_toolkit.script_api import CarlaScriptApi
from carla_agentic_toolkit.snapshots import RunSnapshots

RESULT_NAME = "result"
API_NAME = "api"
# Half the Rust 1 MiB pipe cap — generous for script output while
# ensuring output_too_large fires before Rust-side truncation.
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
        self.truncated: bool = False

    def write(self, s: str) -> int:
        if self.truncated:
            return len(s)
        current = self.tell()
        if current + len(s) > self._max_bytes:
            self.truncated = True
            remaining = max(0, self._max_bytes - current)
            if remaining:
                super().write(s[:remaining])
            return len(s)
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
    )
    sys.stdout.write(json.dumps(outcome, sort_keys=True, allow_nan=False) + "\n")


def run_script_file(
    *,
    script_path: Path,
    host: str,
    port: int,
    timeout_seconds: float,
    ownership_path: Path | None = None,
) -> dict[str, object]:
    """Execute one script file with a curated CARLA API object."""
    code = script_path.read_text(encoding="utf-8")
    rejection = _validate_script(code)
    if rejection is not None:
        return _error("script_rejected", rejection, stdout="")
    snapshots = RunSnapshots()
    adapter = PythonCarlaAdapter(host=host, port=port, timeout=timeout_seconds)
    ownership = RunOwnership(ownership_path) if ownership_path is not None else None
    api = CarlaScriptApi(adapter=adapter, snapshots=snapshots, ownership=ownership)
    stream = _BoundedWriter(MAX_SCRIPT_STDOUT_BYTES)
    try:
        with contextlib.redirect_stdout(stream):
            globals_after_run = runpy.run_path(
                str(script_path),
                init_globals={
                    "__builtins__": _safe_builtins(),
                    API_NAME: api,
                    RESULT_NAME: None,
                },
            )
    except Exception as exc:
        cleanup = cleanup_owned_actors(adapter, ownership)
        return _error(
            type(exc).__name__,
            str(exc),
            stdout=stream.getvalue(),
            cleanup=cleanup,
        )
    if stream.truncated:
        return _error(
            "output_too_large",
            f"Script stdout exceeded {MAX_SCRIPT_STDOUT_BYTES} bytes.",
            stdout=stream.getvalue(),
        )
    result_value = None
    json_error = None
    try:
        result_value = _jsonable(
            globals_after_run.get(RESULT_NAME),
            _seen=set(),
            _depth=0,
        )
    except Exception as exc:
        json_error = f"{type(exc).__name__}: {exc}"
    if json_error:
        return _error(
            "result_not_serializable",
            json_error,
            stdout=stream.getvalue(),
        )
    return {
        "ok": True,
        "result": result_value,
        "stdout": stream.getvalue(),
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


def _jsonable(value: object, *, _seen: set[int], _depth: int) -> object:
    """Coerce a script result into a JSON-compatible value.

    Detects cycles, enforces depth and item limits, rejects NaN/Infinity,
    and sorts unordered collections for deterministic output.
    """
    if value is None or isinstance(value, bool | int | float | str):
        if isinstance(value, float) and not __import__("math").isfinite(value):
            msg = "Result cannot contain NaN or Infinity."
            raise ValueError(msg)
        return value
    if _depth > MAX_JSON_DEPTH:
        msg = f"Result exceeded maximum nesting depth of {MAX_JSON_DEPTH}."
        raise RecursionError(msg)
    if isinstance(value, Mapping):
        return _jsonable_mapping(cast("Mapping[object, object]", value), _seen=_seen, _depth=_depth)
    if isinstance(value, list | tuple | set | frozenset):
        return _jsonable_iterable(value, _seen=_seen, _depth=_depth)
    return repr(value)


def _jsonable_mapping(
    value: Mapping[object, object], *, _seen: set[int], _depth: int
) -> dict[str, object]:
    """Coerce a mapping into a JSON-compatible dict."""
    key = id(value)
    if key in _seen:
        msg = "Result contains a cycle."
        raise ValueError(msg)
    _seen.add(key)
    try:
        return {str(k): _jsonable(v, _seen=_seen, _depth=_depth + 1) for k, v in value.items()}
    finally:
        _seen.discard(key)


def _jsonable_iterable(value: Iterable[object], *, _seen: set[int], _depth: int) -> list[object]:
    """Coerce an iterable into a JSON-compatible list.

    Unordered collections (sets) are sorted for deterministic output.
    """
    items: Iterable[object] = value
    if isinstance(value, set | frozenset):
        # Sort sets by their string representation for determinism.
        items = sorted(value, key=repr)
    result: list[object] = []
    for item in items:
        if len(result) >= MAX_JSON_ITEMS:
            msg = f"Result exceeded maximum item count of {MAX_JSON_ITEMS}."
            raise ValueError(msg)
        result.append(_jsonable(item, _seen=_seen, _depth=_depth + 1))
    return result


if __name__ == "__main__":
    main()
