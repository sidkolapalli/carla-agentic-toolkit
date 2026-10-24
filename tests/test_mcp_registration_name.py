"""Client setup registers the server under its own name, not the bare ``carla``."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# CARLA's upstream AgenticTools registers ``carla``; a second ``carla`` entry
# silently replaces or shadows it, so ours is always ``carla-agentic-toolkit``.
BARE_NAME = re.compile(r"mcp add\b.*\scarla(\s|\\|$)|mcp_servers\.carla[\].]|\"carla\":\s*\{")


def test_docs_never_register_the_server_as_bare_carla() -> None:
    """Every documented client registration uses ``carla-agentic-toolkit``."""
    documents = sorted([*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")])
    bare = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in documents
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if BARE_NAME.search(line)
    ]
    assert bare == []
