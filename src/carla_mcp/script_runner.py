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
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from carla_mcp.adapter import PythonCarlaAdapter
from carla_mcp.script_api import CarlaScriptApi
from carla_mcp.session import CarlaSession

RESULT_NAME = "result"
API_NAME = "api"

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
    )
    sys.stdout.write(json.dumps(outcome, sort_keys=True) + "\n")


def run_script_file(
    *,
    script_path: Path,
    host: str,
    port: int,
    timeout_seconds: float,
) -> dict[str, object]:
    """Execute one script file with a curated CARLA API object."""
    code = script_path.read_text(encoding="utf-8")
    rejection = _validate_script(code)
    if rejection is not None:
        return _error("script_rejected", rejection, stdout="")
    session = CarlaSession()
    adapter = PythonCarlaAdapter(host=host, port=port, timeout=timeout_seconds)
    api = CarlaScriptApi(adapter=adapter, session=session)
    stream = io.StringIO()
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
        return _error(type(exc).__name__, str(exc), stdout=stream.getvalue())
    return {
        "ok": True,
        "result": _jsonable(globals_after_run.get(RESULT_NAME)),
        "stdout": stream.getvalue(),
        "error": None,
        "error_type": None,
        "resources": _resources(session),
    }


def _parse_args() -> argparse.Namespace:
    """Parse runner arguments."""
    parser = argparse.ArgumentParser(description="Run one sandboxed CARLA script.")
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=2000, type=int)
    parser.add_argument("--timeout-seconds", default=30.0, type=float)
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


def _resources(session: CarlaSession) -> dict[str, object]:
    """Return session resources created by the script."""
    return {uri: session.read_resource(uri) for uri in session.resource_uris()}


def _error(error_type: str, message: str, *, stdout: str) -> dict[str, object]:
    """Return a JSON-compatible error outcome."""
    return {
        "ok": False,
        "result": None,
        "stdout": stdout,
        "error": message,
        "error_type": error_type,
        "resources": {},
    }


def _safe_builtins() -> dict[str, object]:
    """Return builtins available to scenario scripts."""
    return {name: getattr(builtins, name) for name in _SAFE_BUILTIN_NAMES}


def _jsonable(value: object) -> object:
    """Coerce a script result into a JSON-compatible value."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Mapping):
        return _jsonable_mapping(cast("Mapping[object, object]", value))
    if isinstance(value, list | tuple | set | frozenset):
        return _jsonable_iterable(value)
    return repr(value)


def _jsonable_mapping(value: Mapping[object, object]) -> dict[str, object]:
    """Coerce a mapping into a JSON-compatible dict."""
    return {str(key): _jsonable(item) for key, item in value.items()}


def _jsonable_iterable(value: object) -> list[object]:
    """Coerce a simple iterable container into a JSON-compatible list."""
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(item) for item in value]
    return [repr(value)]


if __name__ == "__main__":
    main()
