# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib==3.11.2", "numpy==2.5.3"]
# ///
"""Render scientific PNG/SVG graphs from the portable, numerical route study."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any

import matplotlib as mpl
import numpy as np
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

NAMES = {
    "lead_brake": "Braking lead car",
    "cut_in": "Vehicle cutting in",
    "pedestrian_crossing": "Pedestrian attempt",
}
COLORS = {"lead_brake": "#1769AA", "cut_in": "#008575", "pedestrian_crossing": "#B85B18"}
TACTICS = {"yield": 0, "caution": 1, "cruise": 2}
INK, MUTED, SHADE, FALLBACK = "#142431", "#566776", "#DCE8EE", "#BB2864"


def _style() -> None:
    plt.switch_backend("Agg")
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.titlesize": 12,
            "axes.titleweight": "bold",
            "axes.labelcolor": INK,
            "text.color": INK,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#9BA9B2",
            "axes.grid": True,
            "grid.color": "#E6ECEF",
            "grid.linewidth": 0.7,
            "axes.axisbelow": True,
            "legend.frameon": False,
            "savefig.facecolor": "white",
            "svg.hashsalt": "carla-route-study-v1",
        }
    )


def _decorate(fig: Figure, title: str, subtitle: str, footer: str) -> None:
    height = fig.get_figheight()
    heading = fig.suptitle(
        title, x=0.055, y=1 - 0.16 / height, ha="left", fontsize=21, fontweight="bold", va="top"
    )
    heading.set_in_layout(False)
    fig.text(0.055, 1 - 0.64 / height, subtitle, color=MUTED, fontsize=11, va="top")
    fig.text(0.055, 0.018, footer, color=MUTED, fontsize=9, va="bottom")
    fig.tight_layout(rect=(0.015, 0.105, 0.99, 1 - 1.07 / height), h_pad=1.7, w_pad=2.5)


def _values(case: dict[str, Any], key: str) -> list[float]:
    return [math.nan if row[key] is None else float(row[key]) for row in case["samples"]]


def _hazard_axes(axes: list[Axes], duration: float) -> None:
    for axis in axes:
        axis.axvspan(0, duration, color=SHADE, alpha=0.75, zorder=0)
        axis.axvline(0, color=MUTED, linestyle="--", linewidth=0.9)
        axis.set_xlim(-3, 12)


def hazard_response(data: dict[str, Any], analysis: dict[str, Any]) -> Figure:
    """Align physical states and issued controls to the scripted trigger, retaining fallbacks."""
    fig, axes = plt.subplots(3, 3, figsize=(15, 10), sharex="col", sharey="row")
    for col, (case, result) in enumerate(zip(data["trials"], analysis["trials"], strict=True)):
        color = COLORS[case["scenario"]]
        seconds = np.array(_values(case, "time_s")) - case["trigger_s"]
        axes[0, col].set_title(NAMES[case["scenario"]], pad=12)
        _hazard_axes(list(axes[:, col]), result["hazard_active_s"])
        axes[0, col].plot(
            seconds, _values(case, "speed_mps"), color=color, linewidth=2, label="Controlled car"
        )
        axes[0, col].plot(
            seconds,
            _values(case, "hazard_speed_mps"),
            color=MUTED,
            linestyle="--",
            linewidth=1.5,
            label="Hazard actor",
        )
        axes[0, col].set_ylim(-0.15, 8.0)
        if case["scenario"] == "pedestrian_crossing":
            axes[0, col].text(
                0.98,
                0.93,
                "Crossing was not achieved",
                transform=axes[0, col].transAxes,
                ha="right",
                va="top",
                color=FALLBACK,
                fontsize=10,
            )
        axes[1, col].step(seconds, _values(case, "brake"), where="post", color=color, linewidth=1.8)
        axes[1, col].set_ylim(-0.05, 1.1)
        _tactic_line(axes[2, col], case, seconds, color)
        axes[2, col].set_xlabel("Simulation seconds from hazard trigger")
    axes[0, 0].set_ylabel("Observed speed (m/s)")
    axes[1, 0].set_ylabel("Issued brake (0-1)")
    axes[2, 0].set_ylabel("Requested tactic")
    axes[0, 0].legend(loc="upper right", fontsize=9)
    axes[2, 0].legend(
        handles=[
            Line2D(
                [], [], marker="o", color=MUTED, linestyle="none", label="Accepted Jev selection"
            ),
            Line2D([], [], marker="X", color=FALLBACK, linestyle="none", label="Provider fallback"),
        ],
        loc="upper left",
        fontsize=8,
    )
    _decorate(
        fig,
        "How the vehicle responded to each hazard",
        "Full 20 Hz state and control traces · shading marks the scripted hazard interval",
        "One run per scenario, seed 7. Controls follow the displayed state; "
        "lines retain held tactics.\n"
        "A fallback is a local action, not a Jev selection. The simulator pauses during inference.",
    )
    return fig


def _tactic_line(axis: Axes, case: dict[str, Any], seconds: object, color: str) -> None:
    levels = [TACTICS[row["requested_choice"]] for row in case["samples"]]
    axis.step(seconds, levels, where="post", color=color, linewidth=1.8)
    for row in case["decisions"]:
        fallback = row["source"] != "jev" or not row["accepted"]
        axis.scatter(
            row["time_s"] - case["trigger_s"],
            TACTICS[row["choice"]],
            marker="X" if fallback else "o",
            s=60 if fallback else 25,
            color=FALLBACK if fallback else color,
            zorder=4,
        )
    axis.set_yticks([0, 1, 2], ["Yield", "Caution", "Cruise"])
    axis.set_ylim(-0.4, 2.65)


def route_progress(data: dict[str, Any], analysis: dict[str, Any]) -> Figure:
    """Show complete elapsed trajectories and measured signal-control exposure."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6.5), gridspec_kw={"width_ratios": [1.15, 1]})
    for case in data["trials"]:
        axes[0].plot(
            _values(case, "time_s"),
            _values(case, "progress_m"),
            color=COLORS[case["scenario"]],
            linewidth=2,
            label=NAMES[case["scenario"]],
        )
    axes[0].axhline(135, color=MUTED, linestyle="--", linewidth=0.9)
    axes[0].set(
        xlabel="Elapsed simulation time (s)",
        ylabel="Route progress (m)",
        title="Destination reached in each recorded run",
        xlim=(0, 85),
        ylim=(0, 145),
    )
    axes[0].legend(loc="lower right", fontsize=9)
    for row, result in enumerate(analysis["trials"]):
        other = result["elapsed_s"] - result["signal_guard_s"]
        axes[1].barh(row, other, color="#A8B9C4", height=0.52)
        axes[1].barh(
            row,
            result["signal_guard_s"],
            left=other,
            color=COLORS["pedestrian_crossing"],
            height=0.52,
        )
        axes[1].text(
            result["elapsed_s"] + 1, row, f"{result['elapsed_s']:.2f}s", va="center", fontsize=10
        )
        if result["signal_guard_s"]:
            axes[1].text(
                other + result["signal_guard_s"] / 2,
                row,
                f"{result['signal_guard_s']:.2f}s\n{result['signal_guard_fraction']:.1%}",
                ha="center",
                va="center",
                color="white",
                fontsize=10,
                fontweight="bold",
            )
    axes[1].set_yticks(range(3), [NAMES[row["scenario"]] for row in analysis["trials"]])
    axes[1].set_ylim(3.1, -0.6)
    axes[1].set(
        xlabel="Observed elapsed time (s)",
        xlim=(0, 92),
        title="Time with local traffic-light braking",
    )
    axes[1].legend(
        handles=[
            Patch(color="#A8B9C4", label="Other observed time"),
            Patch(color=COLORS["pedestrian_crossing"], label="Traffic-light braking"),
        ],
        loc="lower left",
        fontsize=9,
    )
    _decorate(
        fig,
        "Completion time includes a substantial signal wait",
        "Measured durations describe three individual runs; they do not rank policy performance",
        "Signal-braking time sums override intervals; it is not a causal delay estimate.\n"
        "Signal phases were not reset. Terminal control contributes no extra unobserved timestep.",
    )
    return fig


def decision_timing(data: dict[str, Any], analysis: dict[str, Any]) -> Figure:
    """Keep accepted choices and provider failure counts distinct from the latency workload."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6.5), gridspec_kw={"width_ratios": [1.05, 1]})
    for case in data["trials"]:
        values = sorted(
            row["latency_ms"] for row in case["decisions"] if row["latency_ms"] is not None
        )
        axes[0].step(
            [values[0], *values],
            [0, *np.arange(1, len(values) + 1) / len(values)],
            where="post",
            color=COLORS[case["scenario"]],
            linewidth=2,
            label=f"{NAMES[case['scenario']]} (n={len(values)})",
        )
    axes[0].set(
        xlabel="Recorded adapter latency (wall-clock ms)",
        ylabel="Fraction of recorded attempts",
        title="Empirical latency distribution",
        xlim=(100, 330),
        ylim=(0, 1.03),
    )
    axes[0].legend(loc="lower right", fontsize=9)
    left = np.zeros(3)
    for choice, color in [
        ("cruise", "#1769AA"),
        ("caution", "#008575"),
        ("yield", "#AC7E17"),
        ("fallback", FALLBACK),
    ]:
        values = [
            sum(row["provider_fallbacks"].values())
            if choice == "fallback"
            else row["accepted_jev_choices"].get(choice, 0)
            for row in analysis["trials"]
        ]
        axes[1].barh(
            range(3),
            values,
            left=left,
            color=color,
            height=0.52,
            label="Invalid reply → fallback" if choice == "fallback" else choice.capitalize(),
        )
        for index, count in enumerate(values):
            if count:
                axes[1].text(
                    left[index] + count / 2,
                    index,
                    str(count),
                    ha="center",
                    va="center",
                    color="white",
                    fontsize=10,
                )
        left += values
    axes[1].set_yticks(range(3), [NAMES[row["scenario"]] for row in analysis["trials"]])
    axes[1].set_ylim(3.1, -0.6)
    axes[1].set(
        xlabel="Recorded provider decisions (count)",
        title="72 Jev choices; one local fallback",
        xlim=(0, 29),
    )
    axes[1].legend(loc="lower left", fontsize=9, ncol=2)
    workload = analysis["workload"]
    _decorate(
        fig,
        "Decision behavior and adapter timing",
        f"73 recorded attempts · pooled median {workload['latency_p50_ms']:.0f} ms · "
        f"pooled 95th percentile {workload['latency_p95_ms']:.0f} ms",
        "Latency includes adapter/transport work and the invalid reply. "
        "Calls are correlated within runs.\n"
        "Descriptive workload quantiles do not establish latency or safety guarantees.",
    )
    return fig


def tracking_clearance(data: dict[str, Any], analysis: dict[str, Any]) -> Figure:
    """Plot measured tracking error and hazard-specific geometric estimates with named limits."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6.5))
    for case, result in zip(data["trials"], analysis["trials"], strict=True):
        color = COLORS[case["scenario"]]
        axes[0].plot(
            _values(case, "progress_m"),
            _values(case, "tracking_error_m"),
            color=color,
            linewidth=1.6,
            label=NAMES[case["scenario"]],
        )
        seconds = np.array(_values(case, "time_s")) - case["trigger_s"]
        axes[1].plot(
            seconds,
            _values(case, "hazard_clearance_m"),
            color=color,
            linewidth=1.8,
            label=f"{NAMES[case['scenario']]} (run min {result['hazard_min_clearance_m']:.2f}m)",
        )
    axes[0].axhline(
        2, color=FALLBACK, linestyle="--", linewidth=1.1, label="2m off-route stop criterion"
    )
    axes[0].set(
        xlabel="Route progress (m)",
        ylabel="Distance to route centreline (m)",
        title="Tracking error remains below the stop criterion",
        ylim=(0, 2.15),
        xlim=(0, 135),
    )
    axes[0].legend(loc="upper left", fontsize=8, frameon=True, framealpha=0.95)
    axes[1].axvline(0, color=MUTED, linestyle="--", linewidth=0.9)
    axes[1].set(
        xlabel="Simulation seconds from hazard trigger",
        ylabel="SAT separation lower bound (m)",
        title="Clearance to the identified hazard actor",
        xlim=(-3, 12),
        ylim=(0, 35),
    )
    axes[1].legend(loc="upper right", fontsize=8)
    _decorate(
        fig,
        "Physical tracking and observed geometric margins",
        "All recorded state frames · clearance uses each scenario's lead car or pedestrian",
        "SAT clearance is an approximate 2D separation lower bound, "
        "not exact distance or a safety guarantee.\n"
        "Minima use the full run; the right panel shows the trigger window. "
        "No collision events were delivered.",
    )
    return fig


def _route_until_goal(case: dict[str, Any]) -> list[dict[str, float]]:
    points = [case["route"][0]]
    distance = 0.0
    for first, second in zip(case["route"], case["route"][1:], strict=False):
        segment = math.hypot(second["x"] - first["x"], second["y"] - first["y"])
        fraction = min(1.0, (case["route_goal_m"] - distance) / segment)
        points.append(
            {key: first[key] + (second[key] - first[key]) * fraction for key in ("x", "y")}
        )
        distance += segment
        if distance >= case["route_goal_m"]:
            break
    return points


def route_paths(data: dict[str, Any], _analysis: dict[str, Any]) -> Figure:
    """Show actual simulator-coordinate paths, without inventing a road basemap."""
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharex=True, sharey=True)
    for axis, case in zip(axes, data["trials"], strict=True):
        reference = _route_until_goal(case)
        axis.plot(
            [p["x"] for p in reference],
            [p["y"] for p in reference],
            color="#92A4B0",
            linestyle="--",
            linewidth=3,
        )
        axis.plot(
            _values(case, "x"), _values(case, "y"), color=COLORS[case["scenario"]], linewidth=1.8
        )
        axis.plot(
            _values(case, "hazard_x"),
            _values(case, "hazard_y"),
            color=MUTED,
            linewidth=1.2,
            alpha=0.75,
        )
        axis.scatter(
            case["samples"][0]["x"], case["samples"][0]["y"], marker="o", color=INK, s=35, zorder=5
        )
        axis.scatter(
            reference[-1]["x"], reference[-1]["y"], marker="*", color=FALLBACK, s=100, zorder=5
        )
        axis.set(title=NAMES[case["scenario"]], xlabel="CARLA world X (m)", aspect="equal")
    axes[0].set_ylabel("CARLA world Y (m)")
    fig.legend(
        handles=[
            Line2D([], [], color="#92A4B0", linestyle="--", label="Reviewed route"),
            Line2D([], [], color=COLORS["lead_brake"], label="Controlled car"),
            Line2D([], [], color=MUTED, label="Observed hazard actor"),
            Line2D([], [], marker="o", linestyle="none", color=INK, label="Start"),
            Line2D([], [], marker="*", linestyle="none", color=FALLBACK, label="135m goal"),
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, 0.075),
        ncol=5,
        fontsize=9,
    )
    _decorate(
        fig,
        "Recorded routes and traffic motion",
        "The same reviewed 135m route includes a junction turn; paths are measured positions",
        "CARLA world coordinates. Hazard paths contain only range-visible observations.\n"
        "The lead car continues past the goal. Arrival uses a one-metre position tolerance.",
    )
    return fig


def scenario_checks(data: dict[str, Any], analysis: dict[str, Any]) -> Figure:
    """Compare measured lateral motion with the fixture's commanded target trajectories."""
    fig, axes = plt.subplots(1, 2, figsize=(14, 6.5))
    cases = {case["scenario"]: case for case in data["trials"]}
    results = {case["scenario"]: case["scenario_fidelity"] for case in analysis["trials"]}
    for axis, scenario in zip(axes, ("pedestrian_crossing", "cut_in"), strict=True):
        case = cases[scenario]
        seconds = np.array(_values(case, "time_s")) - case["trigger_s"]
        lane_half_width = case["lane_width_m"] / 2
        axis.axhspan(-lane_half_width, lane_half_width, color=SHADE, alpha=0.8)
        axis.axhline(0, color=MUTED, linewidth=0.9, linestyle=":")
        axis.axvline(0, color=MUTED, linewidth=0.9, linestyle="--")
        expected = _nominal_lateral(scenario, seconds)
        axis.plot(
            seconds,
            expected,
            color=MUTED,
            linestyle="--",
            linewidth=1.8,
            label="Commanded nominal motion / target",
        )
        axis.plot(
            seconds,
            _values(case, "hazard_lateral_m"),
            color=COLORS[scenario],
            linewidth=2.5,
            label="Measured actor centre",
        )
        axis.set(
            xlim=(-1, 8),
            xlabel="Simulation seconds from trigger",
            ylabel="Actor lateral offset from route (m)",
        )
    axes[0].set(title="Pedestrian remained outside the driving lane", ylim=(-7, 7))
    pedestrian = results["pedestrian_crossing"]
    axes[0].text(
        0.04,
        0.07,
        f"Measured motion: {pedestrian['active_path_length_m']:.2f}m\n"
        f"Median speed: {pedestrian['active_position_speed_median_mps']:.3f}m/s",
        transform=axes[0].transAxes,
        fontsize=11,
        color=FALLBACK,
    )
    entry = results["cut_in"]["first_lane_entry_after_trigger_s"]
    axes[1].set(title=f"Cut-in centre entered the lane after {entry:.2f}s", ylim=(-1, 4.1))
    handles, _ = axes[1].get_legend_handles_labels()
    axes[1].legend(
        handles=[*handles, Patch(color=SHADE, label="Driving-lane corridor (±1.75m)")],
        loc="upper right",
        fontsize=8,
    )
    _decorate(
        fig,
        "Checking that the intended hazards actually happened",
        "The pedestrian crossing was not achieved; the cut-in's physical motion lagged its target",
        "Pedestrian nominal motion: 2m/s for 6s. Cut-in target: 3.5m to 0m over 2.5s.\n"
        "Lane entry means the actor centre enters the route's lane width; "
        "it is not a safety criterion.",
    )
    return fig


def _nominal_lateral(scenario: str, seconds: object) -> object:
    if scenario == "pedestrian_crossing":
        return 6.0 - 2.0 * np.clip(seconds, 0.0, 6.0)
    fraction = np.clip(np.array(seconds) / 2.5, 0.0, 1.0)
    return 3.5 * (1 - fraction * fraction * (3 - 2 * fraction))


def main() -> None:
    """Write portable vector and raster figures plus their input and output hashes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    data = json.loads((options.input / "trials.json").read_text(encoding="utf-8"))
    analysis = json.loads((options.input / "analysis.json").read_text(encoding="utf-8"))
    if data["cohort_sha256"] != analysis["cohort_sha256"]:
        message = "Analysis and graph data have different cohort identities."
        raise ValueError(message)
    fingerprint = hashlib.sha256(
        json.dumps(data, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    if fingerprint != analysis["data_fingerprint"]:
        message = "Analysis does not describe these exact numerical observations."
        raise ValueError(message)
    options.output.mkdir(parents=True, exist_ok=False)
    _style()
    for name, draw in [
        ("01_hazard_response", hazard_response),
        ("02_route_progress", route_progress),
        ("03_decisions_latency", decision_timing),
        ("04_tracking_clearance", tracking_clearance),
        ("05_route_paths", route_paths),
        ("06_scenario_fidelity", scenario_checks),
    ]:
        fig = draw(data, analysis)
        fig.savefig(options.output / f"{name}.png", dpi=160)
        vector = options.output / f"{name}.svg"
        fig.savefig(vector, metadata={"Date": None})
        vector.write_text(
            "\n".join(line.rstrip() for line in vector.read_text(encoding="utf-8").splitlines())
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        plt.close(fig)
    manifest = {
        "matplotlib": mpl.__version__,
        "numpy": np.__version__,
        "python": platform.python_version(),
        "platform": platform.system(),
        "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "analyzer_sha256": hashlib.sha256(
            Path(__file__).with_name("route_study.py").read_bytes()
        ).hexdigest(),
        "font_sha256": hashlib.sha256(
            (Path(mpl.get_data_path()) / "fonts/ttf/DejaVuSans.ttf").read_bytes()
        ).hexdigest(),
        "inputs": {
            name: hashlib.sha256((options.input / name).read_bytes()).hexdigest()
            for name in ("trials.json", "analysis.json")
        },
        "figures": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(options.output.iterdir())
        },
    }
    (options.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    main()
