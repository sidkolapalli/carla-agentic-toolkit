"""The sandbox reads the package and its environment, not the whole checkout."""

from __future__ import annotations

import sys
from pathlib import Path

import carla_agentic_toolkit
from carla_agentic_toolkit.sandbox_paths import read_only_paths

PACKAGE_ROOT = Path(carla_agentic_toolkit.__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_checkout_files_outside_the_package_are_not_readable() -> None:
    """Files such as .env, .git and earlier output stay outside the child's view."""
    paths = read_only_paths()
    assert PROJECT_ROOT not in paths
    for private in (".env", ".git", "docs"):
        assert not any((PROJECT_ROOT / private).is_relative_to(path) for path in paths)


def test_package_and_active_environment_stay_readable() -> None:
    """The child still imports the package, carla and site-packages."""
    paths = read_only_paths()
    assert PACKAGE_ROOT in paths
    assert Path(sys.prefix).resolve() in paths
