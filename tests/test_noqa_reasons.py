"""Source suppressions say why the rule does not apply."""

from __future__ import annotations

import re
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "carla_agentic_toolkit"
REASONLESS = re.compile(r"# noqa: [A-Z0-9, ]+$")


def test_every_source_noqa_gives_a_reason() -> None:
    """A bare rule code hides why it is suppressed; append ' - reason'."""
    bare = [
        f"{path.name}:{number}"
        for path in sorted(SOURCE.glob("*.py"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if REASONLESS.search(line)
    ]
    assert bare == []
