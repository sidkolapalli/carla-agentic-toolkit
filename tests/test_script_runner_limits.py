"""Regression coverage for bounded script output and result conversion."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from carla_agentic_toolkit.script_runner import (
    MAX_JSON_DEPTH,
    MAX_JSON_ITEMS,
    MAX_SCRIPT_STDOUT_BYTES,
    run_script_file,
)

if TYPE_CHECKING:
    from pathlib import Path


def _run(tmp_path: Path, code: str) -> dict[str, object]:
    script = tmp_path / "script.py"
    script.write_text(code, encoding="utf-8")
    return run_script_file(
        script_path=script,
        host="127.0.0.1",
        port=2000,
        timeout_seconds=1.0,
    )


@pytest.mark.parametrize("character", ["é", "€", "🐍"])
def test_stdout_limit_counts_utf8_bytes(tmp_path: Path, character: str) -> None:
    """Multibyte prints must fit the byte budget without splitting a character."""
    repetitions = MAX_SCRIPT_STDOUT_BYTES // len(character.encode("utf-8")) + 1
    outcome = _run(tmp_path, f"print({character!r} * {repetitions}, end='')")

    stdout = outcome["stdout"]
    assert isinstance(stdout, str)
    assert outcome["error_type"] == "output_too_large"
    assert stdout == character * (repetitions - 1)
    assert len(stdout.encode("utf-8")) <= MAX_SCRIPT_STDOUT_BYTES


def test_stdout_exact_byte_limit_can_succeed(tmp_path: Path) -> None:
    """Several writes can fill the budget exactly without a false truncation."""
    repetitions = MAX_SCRIPT_STDOUT_BYTES // len("🐍".encode())
    outcome = _run(
        tmp_path,
        f"print('🐍' * {repetitions - 1}, end='')\nprint('🐍', end='')\nresult = 1",
    )

    assert outcome["ok"] is True
    assert outcome["stdout"] == "🐍" * repetitions


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("result = []\nresult.append(result)", "cycle"),
        ("result = {}\nresult['self'] = result", "cycle"),
        ("result = []\nresult.append({'back': result})", "cycle"),
        (f"result = list(range({MAX_JSON_ITEMS + 1}))", "item count"),
        (f"result = {{i: i for i in range({MAX_JSON_ITEMS + 1})}}", "item count"),
        (f"result = [[0] * {MAX_JSON_ITEMS // 2}] * 2", "item count"),
        (f"result = []\nfor _ in range({MAX_JSON_DEPTH + 1}): result = [result]", "depth"),
        ("result = {'value': float('nan')}", "NaN or Infinity"),
        ("result = [float('inf')]", "NaN or Infinity"),
        ("result = float('-inf')", "NaN or Infinity"),
    ],
)
def test_invalid_results_fail_with_a_structured_error(
    tmp_path: Path, code: str, message: str
) -> None:
    """Cycles and oversized or nonfinite results must fail deterministically."""
    outcome = _run(tmp_path, code)

    assert outcome["ok"] is False
    assert outcome["error_type"] == "result_not_serializable"
    assert message in str(outcome["error"])


def test_shared_containers_and_sets_remain_serializable(tmp_path: Path) -> None:
    """Repeated references are not cycles; unordered values have stable order."""
    outcome = _run(
        tmp_path,
        "shared = {'numbers': (1, 2)}\n"
        "result = {'shared': [shared, shared], 'letters': {'z', 'b', 'a'}}",
    )

    assert outcome["ok"] is True
    assert outcome["result"] == {
        "shared": [{"numbers": [1, 2]}, {"numbers": [1, 2]}],
        "letters": ["a", "b", "z"],
    }


@pytest.mark.parametrize(
    "expression", [f"list(range({MAX_JSON_ITEMS}))", f"{{i: i for i in range({MAX_JSON_ITEMS})}}"]
)
def test_result_item_limit_is_inclusive(tmp_path: Path, expression: str) -> None:
    """A list or mapping exactly at the documented bound should still succeed."""
    outcome = _run(tmp_path, f"result = {expression}")

    assert outcome["ok"] is True


def test_result_depth_limit_is_inclusive(tmp_path: Path) -> None:
    """A result exactly at the nesting bound should still succeed."""
    outcome = _run(
        tmp_path,
        f"result = []\nfor _ in range({MAX_JSON_DEPTH}): result = [result]",
    )

    assert outcome["ok"] is True
