"""Require Radon A grades for complexity and maintainability."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from radon.complexity import cc_rank, cc_visit
from radon.metrics import mi_rank, mi_visit

if TYPE_CHECKING:
    from collections.abc import Iterable


def main() -> int:
    """Run all Radon A-grade checks."""
    paths = [Path(arg) for arg in sys.argv[1:]]
    python_files = sorted(_python_files(paths))
    failures = [*_complexity_failures(python_files), *_maintainability_failures(python_files)]
    for failure in failures:
        sys.stderr.write(f"{failure}\n")
    return 1 if failures else 0


def _python_files(paths: Iterable[Path]) -> Iterable[Path]:
    """Yield Python files under the requested paths."""
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            yield path
        if path.is_dir():
            yield from path.rglob("*.py")


def _complexity_failures(paths: Iterable[Path]) -> list[str]:
    """Return complexity failures for non-A blocks."""
    failures: list[str] = []
    for path in paths:
        blocks = cc_visit(path.read_text(encoding="utf-8"))
        failures.extend(_block_failures(path, _iter_blocks(blocks)))
    return failures


def _block_failures(path: Path, blocks: Iterable[object]) -> list[str]:
    """Return non-A complexity failures for blocks in one file."""
    return [
        f"{path}: {_block_name(block)} complexity {_block_complexity(block)} "
        f"is {cc_rank(_block_complexity(block))}"
        for block in blocks
        if cc_rank(_block_complexity(block)) != "A"
    ]


def _iter_blocks(blocks: Iterable[object]) -> Iterable[object]:
    """Yield Radon blocks recursively."""
    for block in blocks:
        yield block
        yield from _iter_blocks(_block_children(block))


def _block_children(block: object) -> list[object]:
    """Return nested Radon blocks for a block."""
    children: list[object] = []
    for attribute in ("methods", "closures", "inner_classes"):
        value = getattr(block, attribute, [])
        if isinstance(value, list):
            children.extend(value)
    return children


def _block_name(block: object) -> str:
    """Return a Radon block name."""
    value = getattr(block, "name", "<unknown>")
    return str(value)


def _block_complexity(block: object) -> int:
    """Return a Radon block complexity."""
    value = getattr(block, "complexity", 0)
    if isinstance(value, int):
        return value
    msg = f"Radon block {_block_name(block)} has no integer complexity."
    raise TypeError(msg)


def _maintainability_failures(paths: Iterable[Path]) -> list[str]:
    """Return maintainability failures for non-A files."""
    failures: list[str] = []
    for path in paths:
        score = mi_visit(path.read_text(encoding="utf-8"), multi=True)
        rank = mi_rank(score)
        if rank != "A":
            failures.append(f"{path}: maintainability {score:.2f} is {rank}")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
