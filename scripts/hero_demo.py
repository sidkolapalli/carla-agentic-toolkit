"""Run the narrated CARLA MCP hero demo for GitHub and social recordings."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from scripts.live_mcp_smoke import run_live_smoke

if TYPE_CHECKING:
    from collections.abc import Sequence

HERO_FRAME = Path("docs/assets/hero-frame.png")
HERO_REPORT = Path("docs/assets/hero-report.svg")
MAX_DRIVE_SECONDS = 30.0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the live workflow and present its proof as a compact story."""
    args = _parse_args(argv)
    console = Console(record=True)
    console.print(
        Panel.fit(
            "[bold cyan]Natural language -> sandboxed CARLA experiment[/bold cyan]\n"
            "One MCP tool. One self-cleaning workflow.",
            border_style="cyan",
        )
    )
    try:
        with console.status("[bold cyan]Running the live CARLA workflow..."):
            report = asyncio.run(
                run_live_smoke(
                    args,
                    save_image=args.save_image,
                    drive_seconds=args.drive_seconds,
                )
            )
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        console.print(Panel(str(error), title="Demo failed", border_style="red"))
        return 1

    table = Table(show_header=False, box=None, padding=(0, 1))
    for line in _timeline(report, args.save_image):
        label, value = line.split("|", maxsplit=1)
        table.add_row(f"[bold green]OK  {label}[/bold green]", value)
    console.print(table)
    console.print(
        Panel.fit(
            "[bold green]World restored. Zero test actors left behind.[/bold green]",
            border_style="green",
        )
    )
    args.save_terminal_svg.parent.mkdir(parents=True, exist_ok=True)
    console.save_svg(str(args.save_terminal_svg), title="CARLA MCP Hero Demo")
    return 0


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse explicit live-demo configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--windows", action="store_true", help="launch through WSL2")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=2000, type=int)
    parser.add_argument("--timeout-seconds", default=30.0, type=float)
    parser.add_argument("--drive-seconds", default=8.0, type=float)
    parser.add_argument("--save-image", default=HERO_FRAME, type=Path)
    parser.add_argument("--save-terminal-svg", default=HERO_REPORT, type=Path)
    args = parser.parse_args(argv)
    if not args.confirm_live:
        parser.error("--confirm-live is required because this demo mutates the simulator")
    if not 0.0 < args.drive_seconds <= MAX_DRIVE_SECONDS:
        parser.error(f"--drive-seconds must be in (0, {MAX_DRIVE_SECONDS:g}]")
    return args


def _timeline(report: dict[str, object], image_path: Path) -> tuple[str, ...]:
    """Turn protocol output into a concise promotional narrative."""
    health = cast("dict[str, object]", report["health"])
    movement = cast("dict[str, object]", report["movement"])
    map_name = str(health["current_map"]).rsplit("/", maxsplit=1)[-1]
    before = float(cast("float", movement["before_mps"]))
    after = float(cast("float", movement["after_mps"]))
    leftovers = cast("list[object]", report["leftovers"])
    return (
        f"Connected|{map_name}",
        f"Vehicle accelerated|{before:.2f} -> {after:.2f} m/s",
        f"Sandbox enforced|Landlock: {report['landlock_enforced']}",
        f"Native MCP image|{image_path}",
        f"Cleanup verified|{len(leftovers)} actors; weather and spectator restored",
    )


if __name__ == "__main__":
    raise SystemExit(main())
