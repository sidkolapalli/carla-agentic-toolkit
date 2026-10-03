# Recorded managed-experiment demonstration

[Watch the 40-second demonstration](demo.mp4). It shows original CARLA camera
frames alongside decisions and controller execution from the same recorded frame.
Playback is slowed and sampled for readability; it is not a real-time latency
demonstration. No scene pixels, decisions or outcomes are synthesized.

These are two new camera-instrumented runs on 2026-10-03. They are separate from
both the [original comparison](../merge-comparison-2026-10-02/README.md) and the
[independent reproduction](../independent-reproduction-2026-10-03/README.md).
The experimental alpha makes no production, safety or improved-realism claim.

| Case | Recorded behavior | Physical outcome | Cleanup |
| --- | --- | --- | --- |
| Normal budget | 12 Jev selections | Merge completed | Actors and settings restored |
| One-request budget | One Jev selection, then 17 `budget_exhausted` fallbacks | Incomplete, `corridor_invalid` | Actors and settings restored |

The lifecycle finished in both cases. That does not make the second maneuver
successful. The movie and its final card preserve this distinction.

## Evidence and camera coverage

[camera-evidence.json](camera-evidence.json) records the exact specs, run IDs,
original trace hashes, environment, original capture-harness hash, camera-frame
hashes, provider/fallback counts and restoration checks. [success.json](success.json)
and [fallback.json](fallback.json) contain allowlisted numerical projections;
they exclude private paths and provider payloads. The
[playback manifest](demo.manifest.json) identifies every displayed source frame,
including repeated frames, and preserves the outcome and cleanup results.

The success case has 115 observations and 113 camera images. Frames 105223 and
105336 have no camera image. The fallback case has 177 observations and 175
images; frames 105480 and 105655 have no image. The renderer skips missing images
and never borrows a neighboring image while labelling it as the missing frame.
No image belongs to an unobserved frame. The movie is a sampled presentation;
the numerical projections retain every observation.

The fixed birdseye view was rendered by CARLA 0.9.16 in Town10HD_Opt at 960×540,
then letterboxed into a 1280×720 video. The two moving blue vehicles are the
controlled actors; other parked vehicles are static map assets. The camera was
registered as a protected session-owned sensor before listening. It never ticked
the world, selected a maneuver or changed the controller. The normal session
cleanup stopped and destroyed it before restoring the world. Queue drops were
zero in both captures. Raw camera images and complete traces remain local.

Decision text shows the latest received decision **before** that observation's
event sequence. A reply produced later on the same frame appears on the next
observation. The execution text is independently joined to the current frame.
Neither field is used to reconstruct the other.

## Repeat the workflow

First follow [managed setup](../../managed-experiments.md), including the optional
Jev dependency, matching CARLA API and trusted credential environment. Start a
dedicated asynchronous Town10HD_Opt server with rendering enabled and no existing
vehicle, walker, sensor or controller actors. Keep other clients idle. The
capture performs two bounded Jev runs, with at most 41 provider requests combined;
it must not be pointed at a shared simulator.

The portable capture command follows the same camera-instrumentation protocol
as the original local recording harness. A separate
[live CLI validation](capture-cli-validation.json) passed both cases and verified
actor/settings restoration; its runs are not included in this video or either
comparison cohort. The final lifecycle-success guard was also checked against
the retained results and a failing-worker regression test.
Use your actual host/port and a private
native Linux state directory outside the checkout/output allowlists:

```bash
uv run --no-sync python scripts/capture_experiment_demo.py \
  --confirm-live --host 127.0.0.1 --port 2000 \
  --state-dir "$HOME/.local/state/carla-demo-capture" \
  --output target/managed-demo-capture
```

Preserve every result, including incomplete outcomes; do not retry until the
video looks successful. The original files are evidence of these particular
runs, not a promise that another run produces identical trajectories or choices.
Check actor/settings restoration before another use of the simulator.

Project the new private traces after validating their hashes:

```bash
uv run --no-sync python - <<'PY'
import hashlib
import json
from pathlib import Path
from scripts.project_demo_trace import project_trace

root = Path("target/managed-demo-capture")
for case, name in (("normal-budget", "success"), ("restricted-budget", "fallback")):
    saved = json.loads((root / case / "result.json").read_text())
    trace = Path(saved["result"]["trace_path"])
    digest = hashlib.sha256(trace.read_bytes()).hexdigest()
    data = project_trace(trace, digest)
    (root / f"{name}.json").write_text(json.dumps(data, indent=2) + "\n")
PY
```

Rendering requires a local `ffmpeg` executable and an available TrueType font.
The script's isolated dependency declaration installs Pillow for rendering only;
it does not add a server/runtime dependency. If automatic font discovery fails,
pass `--font /absolute/path/to/font.ttf`.

```bash
uv run --script scripts/render_experiment_demo.py \
  --success target/managed-demo-capture/success.json \
  --fallback target/managed-demo-capture/fallback.json \
  --success-frames target/managed-demo-capture/normal-budget/frames \
  --fallback-frames target/managed-demo-capture/restricted-budget/frames \
  --success-capture target/managed-demo-capture/normal-budget/result.json \
  --fallback-capture target/managed-demo-capture/restricted-budget/result.json \
  --output target/managed-demo-capture/demo.mp4
```

The renderer verifies the original capture run ID and image hashes before joining
frames. It refuses a success input without a recorded completed outcome or a
fallback input without a camera-matched budget-exhaustion decision. Its purpose
is to explain retained evidence, not repair an unsuccessful experiment.
