"""A headless image build fails closed on incomplete native dependencies."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from scripts import assemble_runtime

if TYPE_CHECKING:
    from collections.abc import Sequence


@pytest.mark.parametrize(
    ("stdout", "stderr", "returncode"),
    [("", "ldd failed unexpectedly", 2), ("", "librequired.so => not found", 0)],
)
def test_assembly_rejects_failed_dependency_discovery(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
    stderr: str,
    returncode: int,
) -> None:
    """A tool failure or unresolved stderr dependency cannot produce a partial runtime."""

    def run(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    monkeypatch.setattr(assemble_runtime.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="native dependenc"):
        assemble_runtime._libraries(Path("/trusted/extension.so"))  # noqa: SLF001


def test_assembly_accepts_static_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuinely static executable needs no system-library copies."""

    def run(command: Sequence[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "not a dynamic executable")

    monkeypatch.setattr(assemble_runtime.subprocess, "run", run)
    assert assemble_runtime._libraries(Path("/trusted/static")) == set()  # noqa: SLF001
