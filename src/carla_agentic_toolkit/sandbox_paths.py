"""One authoritative filesystem allowlist for sandbox and trusted-state separation."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def read_only_paths() -> tuple[Path, ...]:
    """Return host paths the child Python process may read/execute."""
    # The package source and the active environment, not the whole checkout: files such
    # as .env, .git and earlier outputs stay out of the child's view.
    package_root = Path(__file__).resolve().parents[1]
    candidates = [
        package_root,
        Path(sys.prefix).resolve(),
        Path(sys.executable).resolve().parent.parent,
        Path("/usr"),
        Path("/lib"),
        Path("/lib64"),
        Path("/etc/nsswitch.conf"),
        Path("/etc/host.conf"),
        Path("/etc/hosts"),
        Path("/etc/resolv.conf"),
        Path("/etc/gai.conf"),
    ]
    return tuple(path for path in candidates if path.exists())


def output_dir_path() -> Path:
    """Return the persistent directory exposed for script outputs."""
    configured = os.environ.get("CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR")
    path = (
        Path(configured).expanduser() if configured else Path.cwd() / "carla-agentic-toolkit-output"
    )
    return path.resolve()
