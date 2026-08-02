"""Behavior specs for the promotional live demo runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.hero_demo import HERO_FRAME, HERO_REPORT, _parse_args, _timeline


def test_hero_demo_requires_explicit_live_confirmation() -> None:
    """The promotional demo must never mutate CARLA accidentally."""
    with pytest.raises(SystemExit):
        _parse_args([])


def test_hero_demo_defaults_to_reusable_readme_frame() -> None:
    """A confirmed run should save the frame used by project promotion."""
    args = _parse_args(["--confirm-live"])

    assert args.save_image == HERO_FRAME == Path("docs/assets/hero-frame.png")
    assert args.save_terminal_svg == HERO_REPORT


def test_hero_timeline_highlights_visible_result_and_cleanup() -> None:
    """Narration should explain the value rather than dump raw protocol data."""
    lines = _timeline(
        {
            "health": {"current_map": "Carla/Maps/Town10HD_Opt"},
            "movement": {"before_mps": 0.3, "after_mps": 9.0},
            "landlock_enforced": True,
            "image_content": True,
            "weather_restored": True,
            "leftovers": [],
        },
        HERO_FRAME,
    )

    assert all(
        marker in "\n".join(lines)
        for marker in ("Town10HD_Opt", "0.30 -> 9.00 m/s", "Landlock", "hero-frame.png", "0")
    )
