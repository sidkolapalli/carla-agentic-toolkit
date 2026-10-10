# /// script
# requires-python = ">=3.12"
# dependencies = ["Pillow==12.3.0"]
# ///
"""Compose an evidence-linked recorded CARLA camera demo; requires a local ffmpeg binary."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.replicate_index import normalize_replicate_index

if TYPE_CHECKING:
    from PIL import Image

WIDTH, HEIGHT, FPS = 1280, 720, 10
CASE_SECONDS, TITLE_SECONDS, END_SECONDS = 15, 4, 6
BACKGROUND, PANEL = "#101923", "#1b2937"
WHITE, MUTED, ACCENT = "#edf3f8", "#b4c4d1", "#70d5cf"
PROJECTION_LIMIT = 32 * 1024 * 1024
REASON_LINES = 3


@dataclass(frozen=True)
class JoinedFrame:
    """One observation and the camera image with exactly its recorded frame ID."""

    sample: dict[str, Any]
    image: Path
    capture_image_sha256: str


@dataclass(frozen=True)
class DemoCase:
    """Recorded evidence plus explicitly counted image gaps; no synthesized observations."""

    projection: dict[str, Any]
    projection_sha256: str
    capture_sha256: str
    joined: tuple[JoinedFrame, ...]
    missing_frames: tuple[int, ...]
    unused_camera_frames: tuple[int, ...]


def load_case(projection: Path, frames: Path, capture: Path) -> DemoCase:
    """Bind observations to their original capture run and verified exact-frame camera bytes."""
    raw = _bounded_bytes(projection)
    value = cast("dict[str, Any]", normalize_replicate_index(json.loads(raw)))
    _validate_projection(value)
    images = _camera_frames(frames)
    capture_raw = _bounded_bytes(capture)
    hashes = _capture_hashes(json.loads(capture_raw), value["run_id"])
    _verify_camera_images(images, hashes)
    samples = {sample["frame"]: sample for sample in value["samples"]}
    joined = tuple(
        JoinedFrame(sample, images[frame], hashes[frame])
        for frame, sample in samples.items()
        if frame in images
    )
    if not joined:
        message = "No exact camera and observation frame matches exist."
        raise ValueError(message)
    return DemoCase(
        value,
        hashlib.sha256(raw).hexdigest(),
        hashlib.sha256(capture_raw).hexdigest(),
        joined,
        tuple(sorted(samples.keys() - images.keys())),
        tuple(sorted(images.keys() - samples.keys())),
    )


def _bounded_bytes(path: Path) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(PROJECTION_LIMIT + 1)
    if len(raw) > PROJECTION_LIMIT:
        message = "Input exceeds the bounded demo file size."
        raise ValueError(message)
    return raw


def _capture_hashes(capture: dict[str, Any], run_id: str) -> dict[int, str]:
    if capture["result"]["run_id"] != run_id:
        message = "Capture run identity does not match the projection."
        raise ValueError(message)
    hashes: dict[int, str] = {}
    for record in capture["camera"]["images"]:
        frame = record["frame"]
        if frame in hashes:
            message = f"Duplicate capture frame identity: {frame}"
            raise ValueError(message)
        hashes[frame] = record["sha256"]
    return hashes


def _verify_camera_images(images: dict[int, Path], hashes: dict[int, str]) -> None:
    for frame, path in images.items():
        if frame not in hashes:
            message = f"Camera frame is absent from the capture record: {frame}"
            raise ValueError(message)
        camera_bytes(JoinedFrame({}, path, hashes[frame]))


def camera_bytes(item: JoinedFrame) -> bytes:
    """Read bounded image bytes and recheck provenance immediately before image decoding."""
    raw = _bounded_bytes(item.image)
    if hashlib.sha256(raw).hexdigest() != item.capture_image_sha256:
        message = f"Camera image hash does not match the capture record: {item.image.name}"
        raise ValueError(message)
    return raw


def _validate_projection(value: dict[str, Any]) -> None:
    if value.get("schema_version") != 1:
        message = "Unsupported demo projection schema."
        raise ValueError(message)
    for key in ("run_id", "trace_sha256", "code_sha256", "replicate_index", "outcome", "cleanup"):
        if key not in value:
            message = f"Missing demo projection field: {key}"
            raise ValueError(message)
    previous = -1
    for sample in value["samples"]:
        _validate_sample(sample, previous)
        previous = sample["frame"]


def _validate_sample(sample: dict[str, Any], previous: int) -> None:
    frame = sample["frame"]
    valid = (type(frame) is int, frame > previous, math.isfinite(sample["simulation_seconds"]))
    if not all(valid):
        message = "Invalid or non-increasing observation frame identity."
        raise ValueError(message)
    decision = sample.get("decision")
    if decision is not None:
        _validate_decision(sample, decision)


def _validate_decision(sample: dict[str, Any], decision: dict[str, Any]) -> None:
    if decision["frame"] > sample["frame"] or decision["sequence"] > sample["observation_sequence"]:
        message = "Decision identity belongs to a future observation."
        raise ValueError(message)


def _camera_frames(directory: Path) -> dict[int, Path]:
    images: dict[int, Path] = {}
    for path in sorted(directory.glob("*.png")):
        frame = int(path.stem)
        if frame in images:
            message = f"Duplicate camera frame identity: {frame}"
            raise ValueError(message)
        images[frame] = path
    return images


def _schedule(case: DemoCase) -> tuple[JoinedFrame, ...]:
    """Sample recorded matches over a declared fixed playback duration, including endpoints."""
    count = CASE_SECONDS * FPS
    return tuple(
        case.joined[round(index * (len(case.joined) - 1) / (count - 1))] for index in range(count)
    )


def _font_path(requested: Path | None) -> Path:
    if requested is not None:
        return requested
    candidates = (
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return path
    message = "Pass --font with a readable TrueType font."
    raise FileNotFoundError(message)


class Canvas:
    """Readable fixed-layout presentation, keeping camera pixels separate from evidence text."""

    def __init__(self, font: Path) -> None:
        """Load Pillow lazily so identity tests need no optional rendering dependency."""
        from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

        self.image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = {
            size: ImageFont.truetype(str(font), size) for size in (18, 20, 22, 26, 30, 40, 48)
        }

    def text(
        self, position: tuple[int, int], content: str, size: int = 22, color: str = WHITE
    ) -> None:
        """Draw one legible line without overlaying a camera measurement label."""
        self.draw.text(position, content, font=self.fonts[size], fill=color)

    def paragraph(self, position: tuple[int, int], content: str, width: int = 350) -> None:
        """Wrap long reasons at measured font widths, preserving an explicit truncation mark."""
        words, lines, current = content.split(), [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if self.draw.textlength(candidate, font=self.fonts[20]) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
        for index, line in enumerate(lines[:REASON_LINES]):
            suffix = " …" if index == REASON_LINES - 1 and len(lines) > REASON_LINES else ""
            self.text((position[0], position[1] + index * 27), line + suffix, 20, MUTED)

    def camera(self, item: JoinedFrame) -> None:
        """Letterbox the exact recorded image without replacing or generating scene pixels."""
        from PIL import Image, ImageOps  # noqa: PLC0415

        with Image.open(io.BytesIO(camera_bytes(item))) as source:
            shot = ImageOps.contain(source.convert("RGB"), (840, 472))
        self.draw.rectangle((24, 98, 864, 570), fill="#000000")
        self.image.paste(shot, (24 + (840 - shot.width) // 2, 98 + (472 - shot.height) // 2))


def _case_image(case: DemoCase, item: JoinedFrame, label: str, font: Path) -> Image.Image:
    canvas = Canvas(font)
    canvas.text((24, 18), "CARLA / MANAGED MERGE", 30)
    canvas.text((884, 27), "EXPERIMENTAL ALPHA", 22, ACCENT)
    canvas.text((24, 66), label, 22, ACCENT)
    canvas.camera(item)
    canvas.draw.rectangle((884, 98, 1256, 570), fill=PANEL)
    _case_labels(canvas, case, item)
    _case_footer(canvas, case)
    return canvas.image


def _case_labels(canvas: Canvas, case: DemoCase, item: JoinedFrame) -> None:
    sample = item.sample
    elapsed = sample["simulation_seconds"] - case.joined[0].sample["simulation_seconds"]
    canvas.text((900, 108), f"FRAME {sample['frame']}  /  +{elapsed:.2f}s", 22)
    canvas.text((900, 144), f"Phase: {sample['phase']}", 26, ACCENT)
    canvas.text((900, 187), "Latest decision before observation", 18, MUTED)
    decision = sample.get("decision")
    _decision_labels(canvas, decision)
    execution = sample.get("execution") or {}
    canvas.text((900, 377), "EXECUTED BY CONTROLLER", 18, MUTED)
    canvas.text((900, 408), str(execution.get("executed_choice", "No recorded execution")), 26)
    canvas.text((900, 466), "RUN ID", 18, MUTED)
    canvas.text((900, 494), str(case.projection["run_id"]), 18)
    canvas.text(
        (900, 532),
        f"Policy {case.projection['policy']} / replicate {case.projection['replicate_index']}",
        20,
    )


def _decision_labels(canvas: Canvas, decision: dict[str, Any] | None) -> None:
    if decision is None:
        canvas.text((900, 222), "No earlier recorded decision", 22)
        return
    canvas.text((900, 219), f"{decision['source']} → {decision['choice_id']}", 26)
    canvas.text((900, 254), f"Decision frame {decision['frame']}", 18, MUTED)
    canvas.paragraph((900, 282), str(decision["reason"]))


def _case_footer(canvas: Canvas, case: DemoCase) -> None:
    canvas.text((24, 593), "Recorded CARLA camera replay · exact frame match", 22)
    pace = _simulation_span(case) / CASE_SECONDS
    kind = "Slowed" if pace < 1 else "Sampled"
    canvas.text(
        (24, 628), f"{kind} recorded playback: average {pace:.2f}x simulation speed.", 20, MUTED
    )
    canvas.text(
        (24, 672),
        f"Missing camera frames: {len(case.missing_frames)} (skipped, never borrowed)",
        20,
        ACCENT,
    )
    canvas.text((884, 602), "Final recorded result", 20, MUTED)
    outcome = case.projection["outcome"]
    canvas.text((884, 631), str(outcome["status"]), 26)
    canvas.text((884, 672), f"Cleanup verified: {case.projection['cleanup']['ok']}", 20)


def _title(font: Path) -> Image.Image:
    canvas = Canvas(font)
    canvas.text((64, 62), "EXPERIMENTAL ALPHA", 26, ACCENT)
    canvas.text((64, 143), "A completed merge. A bounded fallback.", 48)
    canvas.text((64, 230), "Recorded CARLA camera replay with frame-linked decisions", 30)
    canvas.text((64, 327), "01  Jev selects a maneuver; code controls the vehicle.", 30)
    canvas.text((64, 389), "02  A deliberate request limit triggers the recorded fallback.", 30)
    canvas.text(
        (64, 517), "Separate demo cases, outside the six-trial comparison cohort.", 26, MUTED
    )
    canvas.text((64, 573), "40-second sampled replay. No safety or realism claim.", 26, MUTED)
    return canvas.image


def _ending(cases: tuple[DemoCase, DemoCase], font: Path) -> Image.Image:
    canvas = Canvas(font)
    canvas.text((48, 36), "What actually finished", 40)
    for index, case in enumerate(cases):
        _end_case(canvas, case, 128 + index * 208)
    canvas.text(
        (48, 588), "Experimental alpha · recorded camera replay · separate demo cases", 26, ACCENT
    )
    canvas.text(
        (48, 642), "Incomplete outcomes remain incomplete. Cleanup is a separate result.", 26, MUTED
    )
    return canvas.image


def _end_case(canvas: Canvas, case: DemoCase, top: int) -> None:
    value = case.projection
    canvas.text((48, top), f"Run {value['run_id']}", 26)
    canvas.text(
        (48, top + 44),
        f"Outcome: {value['outcome']['status']} / completed: {value['outcome']['completed']}",
        26,
    )
    cleanup = value["cleanup"]
    canvas.text(
        (48, top + 88),
        f"Cleanup: {cleanup['ok']}  |  settings restored: {cleanup['settings_restored']}",
        26,
    )
    canvas.text(
        (48, top + 132),
        f"Missing camera frames skipped: {len(case.missing_frames)} / "
        f"{len(value['samples'])} observations",
        22,
        MUTED,
    )


def _verify_demo_cases(cases: tuple[DemoCase, DemoCase]) -> None:
    if cases[0].projection["outcome"]["completed"] is not True:
        message = "Success input has no verified completed outcome."
        raise ValueError(message)
    if not any(
        (item.sample.get("decision") or {}).get("reason") == "budget_exhausted"
        for item in cases[1].joined
    ):
        message = "Fallback input has no camera-matched budget_exhausted decision."
        raise ValueError(message)


def _write_images(
    cases: tuple[DemoCase, DemoCase], directory: Path, font: Path
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    index = _repeat_image(_title(font), directory, 0, TITLE_SECONDS * FPS)
    labels = ("01 / Completed maneuver", "02 / Deliberate request-budget fallback")
    for case, label in zip(cases, labels, strict=True):
        frames = []
        for item in _schedule(case):
            _case_image(case, item, label, font).save(directory / f"{index:05d}.png")
            frames.append(item.sample["frame"])
            index += 1
        records.append(_manifest_case(case, frames))
    _repeat_image(_ending(cases, font), directory, index, END_SECONDS * FPS)
    return records


def _repeat_image(image: Image.Image, directory: Path, start: int, count: int) -> int:
    for index in range(start, start + count):
        image.save(directory / f"{index:05d}.png")
    return start + count


def _manifest_case(case: DemoCase, frames: list[int]) -> dict[str, Any]:
    value = case.projection
    return {
        "run_id": value["run_id"],
        "projection_sha256": case.projection_sha256,
        "capture_sha256": case.capture_sha256,
        "trace_sha256": value["trace_sha256"],
        "code_sha256": value["code_sha256"],
        "outcome": value["outcome"],
        "cleanup": value["cleanup"],
        "observation_count": len(value["samples"]),
        "matched_count": len(case.joined),
        "missing_camera_frames": case.missing_frames,
        "unused_camera_frames": case.unused_camera_frames,
        "displayed_frames": frames,
        "playback_seconds": CASE_SECONDS,
        "simulation_span_seconds": _simulation_span(case),
        "mean_simulation_seconds_per_video_second": _simulation_span(case) / CASE_SECONDS,
        "camera_sha256_by_frame": {
            str(item.sample["frame"]): item.capture_image_sha256 for item in case.joined
        },
    }


def _simulation_span(case: DemoCase) -> float:
    return (
        case.joined[-1].sample["simulation_seconds"] - case.joined[0].sample["simulation_seconds"]
    )


def render(cases: tuple[DemoCase, DemoCase], output: Path, font: Path, ffmpeg: str) -> None:
    """Render a deterministic composition and an auditable exact-frame playback manifest."""
    _verify_demo_cases(cases)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="carla-demo-render-") as temp:
        directory = Path(temp)
        records = _write_images(cases, directory, font)
        subprocess.run(  # noqa: S603 - explicit local renderer executable, no shell.
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-framerate",
                str(FPS),
                "-i",
                str(directory / "%05d.png"),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(output),
            ],
            check=True,
            timeout=180,
        )
    manifest = {
        "schema_version": 1,
        "width": WIDTH,
        "height": HEIGHT,
        "fps": FPS,
        "duration_seconds": TITLE_SECONDS + 2 * CASE_SECONDS + END_SECONDS,
        "kind": "recorded_carla_camera_replay",
        "experimental_alpha": True,
        "separate_demo_cases": True,
        "safety_claim": False,
        "cases": records,
        "video_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }
    output.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main() -> None:
    """Read explicitly provided recorded cases; never run a simulator or provider."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "success",
        "fallback",
        "success-frames",
        "fallback-frames",
        "success-capture",
        "fallback-capture",
        "output",
    ):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--font", type=Path)
    parser.add_argument("--ffmpeg", default=shutil.which("ffmpeg"))
    options = parser.parse_args()
    if options.ffmpeg is None:
        parser.error("ffmpeg is required; install it or pass --ffmpeg.")
    cases = (
        load_case(options.success, options.success_frames, options.success_capture),
        load_case(options.fallback, options.fallback_frames, options.fallback_capture),
    )
    render(cases, options.output, _font_path(options.font), options.ffmpeg)


if __name__ == "__main__":
    main()
