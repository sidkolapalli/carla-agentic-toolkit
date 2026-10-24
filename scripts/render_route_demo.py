# /// script
# requires-python = ">=3.12"
# dependencies = ["Pillow==12.3.0"]
# ///
"""Render exact-frame route trial highlights with separate model and controller labels."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from carla_agentic_toolkit.experiment_trace import load_trace, summarize_trace
from scripts.render_experiment_demo import (
    ACCENT,
    MUTED,
    PANEL,
    Canvas,
    JoinedFrame,
    camera_bytes,
    camera_hash_representation,
)

if TYPE_CHECKING:
    from PIL import Image

FPS = 5
SCENARIOS = {
    "lead_brake": "A vehicle ahead brakes suddenly",
    "cut_in": "A car cuts into the driving lane",
    "pedestrian_crossing": "A pedestrian crosses the road",
}
HAZARDS = {
    "lead_brake": "Lead vehicle is braking",
    "cut_in": "Vehicle is changing lanes",
    "pedestrian_crossing": "Pedestrian is crossing",
}


def project_case(folder: Path) -> dict[str, Any]:
    """Allowlist frame data and verify the capture's explicitly named camera hash bytes."""
    capture = json.loads((folder / "result.json").read_text(encoding="utf-8"))
    trace = Path(capture["result"]["trace_path"])
    read = load_trace(trace)
    if not read.complete or read.errors:
        message = "A complete, valid saved trace is required for route rendering."
        raise ValueError(message)
    identity = capture["result"]["run_id"]
    if any(event["run_id"] != identity for event in read.events):
        message = "Camera and trace run identities differ."
        raise ValueError(message)
    observations = {
        event["frame"]: event["data"] for event in read.events if event["kind"] == "observation"
    }
    executions = {
        event["frame"]: event["data"] for event in read.events if event["kind"] == "execution"
    }
    representation = camera_hash_representation(capture["camera"])
    samples = [
        _join(folder, record, observations, executions, representation)
        for record in capture["camera"]["images"]
    ]
    return {
        "run_id": identity,
        "scenario": capture["spec"]["scenario"],
        "policy": capture["spec"]["policy"],
        "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
        "capture_ok": capture["capture_ok"],
        "outcome": capture["result"]["outcome"],
        "metrics": summarize_trace(read)["metrics"],
        "provider_fallbacks": dict(
            Counter(
                event["data"]["reason"]
                for event in read.events
                if event["kind"] == "decision_received" and event["data"]["source"] == "fallback"
            )
        ),
        "samples": samples,
    }


def _join(
    folder: Path,
    record: dict[str, Any],
    observations: dict[int, Any],
    executions: dict[int, Any],
    representation: str | None = None,
) -> dict[str, Any]:
    frame = record["frame"]
    if type(frame) is not int or frame not in observations or frame not in executions:
        message = "Every camera frame must have an exact observation and execution match."
        raise ValueError(message)
    path = folder / "frames" / f"{frame}.png"
    camera_bytes(JoinedFrame({}, path, record["sha256"], representation))
    value, execution = observations[frame], executions[frame]
    return {
        "frame": frame,
        "simulation_seconds": value["simulation_seconds"],
        "speed_mps": value["speed_mps"],
        "progress_m": value["route_progress_m"],
        "collision": value["collision"],
        "hazard": execution["hazard"],
        "requested_choice": execution["requested_choice"],
        "source": execution["choice_source"],
        "executed_choice": execution["executed_choice"],
        "intervention": execution["intervention"],
        "control": execution["controls"]["policy"],
        "sha256": record["sha256"],
        "sha256_representation": representation,
    }


def schedule(samples: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Keep hazard and route-end windows at original simulation speed; label the time jump."""
    trigger = next(
        (
            s["hazard"]["started_seconds"]
            for s in samples
            if s["hazard"]["started_seconds"] is not None
        ),
        samples[0]["simulation_seconds"],
    )
    hazard = [
        ("HAZARD RESPONSE", s)
        for s in samples
        if trigger - 3 <= s["simulation_seconds"] <= trigger + 11
    ]
    last = hazard[-1][1]["simulation_seconds"] if hazard else -1.0
    ending = [
        ("LATER: ROUTE END", s)
        for s in samples
        if s["simulation_seconds"] > max(last, samples[-1]["simulation_seconds"] - 10)
    ]
    return hazard + ending


def _frame_image(
    folder: Path, case: dict[str, Any], segment: str, sample: dict[str, Any], font: Path
) -> Image.Image:
    canvas = Canvas(font)
    canvas.text((24, 18), f"{case['policy'].upper()} / ROUTE DRIVING WITH TRAFFIC", 30)
    canvas.text((24, 66), SCENARIOS[case["scenario"]], 22, ACCENT)
    item = JoinedFrame(
        sample,
        folder / "frames" / f"{sample['frame']}.png",
        sample["sha256"],
        sample.get("sha256_representation"),
    )
    canvas.camera(item)
    canvas.draw.rectangle((884, 98, 1256, 570), fill=PANEL)
    _labels(canvas, case, sample, segment)
    canvas.text(
        (24, 594), "Camera follows the controlled car. Jev chooses cruise, caution or yield.", 22
    )
    canvas.text(
        (24, 632),
        "Actual CARLA 0.10.0 / UE5.5 frames · 5 fps replay · API waits omitted",
        20,
        MUTED,
    )
    canvas.text(
        (24, 666),
        "Selected windows; labeled time jump. Exploratory trial, not a safety benchmark.",
        20,
        MUTED,
    )
    return canvas.image


def _labels(canvas: Canvas, case: dict[str, Any], sample: dict[str, Any], segment: str) -> None:
    elapsed = sample["simulation_seconds"] - case["samples"][0]["simulation_seconds"]
    canvas.text((900, 110), segment, 20, ACCENT)
    canvas.text((900, 145), f"{elapsed:.1f}s  |  {sample['progress_m']:.0f} / 135m", 22)
    canvas.text((900, 182), f"Speed: {sample['speed_mps']:.1f} m/s", 22)
    canvas.text((900, 235), "SELECTED TACTIC", 18, MUTED)
    canvas.text((900, 265), f"{sample['source'].upper()}: {sample['requested_choice'].upper()}", 26)
    canvas.text((900, 315), "LOCAL CONTROL AFTER THIS FRAME", 18, MUTED)
    reason = sample["intervention"].get("reason")
    label = {
        "imminent_obstacle": "Emergency brake",
        "traffic_light_stop": "Stop for traffic light",
    }.get(reason)
    canvas.text(
        (900, 345), label or sample["executed_choice"].replace("_", " ").capitalize(), 26, ACCENT
    )
    control = sample["control"]
    canvas.text(
        (900, 385), f"Throttle {control['throttle']:.2f} / brake {control['brake']:.2f}", 20
    )
    canvas.paragraph(
        (900, 440),
        HAZARDS[case["scenario"]] if sample["hazard"]["active"] else "Controlled hazard inactive.",
        width=340,
    )
    canvas.text((900, 530), f"Recorded frame {sample['frame']}", 18, MUTED)


def render(folders: list[Path], output: Path, font: Path, ffmpeg: str) -> None:
    """Compose a reviewable video plus pinned, allowlisted evidence and original frame mapping."""
    cases = [
        json.loads((folder / "route-projection.json").read_text(encoding="utf-8"))
        for folder in folders
    ]
    output.mkdir(parents=True, exist_ok=False)
    images = output / "rendered"
    images.mkdir()
    mappings = []
    index = _card(
        images,
        font,
        0,
        [
            "One route. Three hazards.",
            "Jev chooses when to cruise, slow down or yield.",
            "Local code follows the lane and can brake every frame.",
            "Watch the car, its selected tactic, and the actual controls.",
        ],
        seconds=3,
    )
    for folder, case in zip(folders, cases, strict=True):
        for segment, sample in schedule(case["samples"]):
            frame = _frame_image(folder, case, segment, sample, font)
            frame.save(images / f"{index:05d}.png")
            mappings.append(
                {
                    "video_frame": index,
                    "run_id": case["run_id"],
                    "source_frame": sample["frame"],
                    "segment": segment,
                }
            )
            index += 1
    completed = sum(case["outcome"]["completed"] for case in cases)
    collisions = sum(case["metrics"]["collision_events"] for case in cases)
    fallbacks = sum(sum(case["provider_fallbacks"].values()) for case in cases)
    _card(
        images,
        font,
        index,
        [
            f"{completed} / {len(cases)} routes completed",
            f"Delivered collision events across these trials: {collisions}",
            f"Provider replies that fell back to local stopping: {fallbacks}",
            "Exploratory trials, not a safety or production benchmark.",
        ],
        seconds=5,
    )
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-framerate",
        str(FPS),
        "-i",
        str(images / "%05d.png"),
        "-c:v",
        "libx264",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output / "route-hazards.mp4"),
    ]
    subprocess.run(command, check=True, timeout=180)  # noqa: S603
    (output / "evidence.json").write_text(
        json.dumps({"cases": cases, "video_frames": mappings}, indent=2) + "\n", encoding="utf-8"
    )


def _card(directory: Path, font: Path, index: int, lines: list[str], *, seconds: int) -> int:
    canvas = Canvas(font)
    canvas.text((70, 160), lines[0], 48, ACCENT)
    for row, line in enumerate(lines[1:]):
        canvas.text((70, 290 + row * 65), line, 26)
    for frame in range(index, index + seconds * FPS):
        canvas.image.save(directory / f"{frame:05d}.png")
    return index + seconds * FPS


def main() -> None:
    """Use local verified captures; never download or synthesize simulation footage."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", type=Path, nargs="+")
    parser.add_argument("--project-only", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--font", type=Path)
    options = parser.parse_args()
    if options.project_only:
        for folder in options.captures:
            projection = project_case(folder)
            with (folder / "route-projection.json").open("w", encoding="utf-8") as stream:
                json.dump(projection, stream, indent=2, allow_nan=False)
        return
    if options.output is None or options.font is None:
        parser.error("Rendering requires --output and --font.")
    binary = shutil.which("ffmpeg")
    if binary is None:
        parser.error("A local ffmpeg executable is required.")
    render(options.captures, options.output, options.font, binary)


if __name__ == "__main__":
    main()
