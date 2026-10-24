"""Package code models real shapes instead of falling back to typing.Any."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TypeGuard

SOURCE = Path(__file__).resolve().parents[1] / "src" / "carla_agentic_toolkit"
# Each remaining use carries a comment at the use site explaining why it stays.
ALLOWED = {"experiment_trace.py", "session_protocol.py"}


def _imports_from_typing(node: ast.AST) -> TypeGuard[ast.ImportFrom]:
    return isinstance(node, ast.ImportFrom) and node.module == "typing"


def _typing_imports(tree: ast.Module) -> set[str]:
    imports = [node for node in ast.walk(tree) if _imports_from_typing(node)]
    return {alias.name for node in imports for alias in node.names}


def _qualified_attributes(tree: ast.Module) -> set[str]:
    attributes = (node for node in ast.walk(tree) if isinstance(node, ast.Attribute))
    return {
        f"{node.value.id}.{node.attr}" for node in attributes if isinstance(node.value, ast.Name)
    }


def _uses_any(tree: ast.Module) -> bool:
    return "Any" in _typing_imports(tree) or "typing.Any" in _qualified_attributes(tree)


def test_only_documented_modules_use_typing_any() -> None:
    """Use a Protocol, TypedDict or JsonObject; extend ALLOWED only with a use-site reason."""
    users = {
        path.name
        for path in sorted(SOURCE.glob("*.py"))
        if _uses_any(ast.parse(path.read_text(encoding="utf-8")))
    }
    assert users <= ALLOWED, sorted(users - ALLOWED)
