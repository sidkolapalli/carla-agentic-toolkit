# Jev drives a route through three traffic hazards

[Watch the 80-second recording](demo.mp4). The camera follows the controlled
car while the panel shows the selected tactic and the controls issued after
that frame. The video includes each hazard and the later route end; time jumps
are labeled. It uses original CARLA frames at five playback frames per second.
Inference pauses and most traffic-light waiting are omitted.

All three camera-instrumented Jev runs completed the reviewed 135m Town10HD_Opt
route, including a junction turn, with zero delivered collision events. Actors,
sensors and world settings were restored in every run. These are three
exploratory trials, with one seed and fixed weather, not production or safety
validation.

| Scenario | Accepted Jev choices | Observed outcome | Local/provider intervention |
| --- | --- | --- | --- |
| Braking lead vehicle | 5 cruise, 19 caution, 2 yield | Route completed | No emergency or signal-braking override recorded |
| Vehicle cutting in | 6 cruise, 18 caution | Route completed | No emergency or signal-braking override recorded |
| Pedestrian crossing | 10 cruise, 12 caution | Route completed | One invalid provider reply caused a yield fallback; 683 frames of local traffic-light braking |

There were **72 accepted Jev selections from 73 attempts**. The pedestrian
fallback is visible in the video and is not credited to Jev as a successful
selection. Its sanitized metadata records the expected model and 149ms response
latency, but does not retain which selection/probability validation failed.
No provider retry or discarded Jev trial was used to replace it.

In the cut-in run, Jev selected `caution` at the first decision boundary after
the scripted lane change began, 0.5 simulation seconds after its trigger. In the
lead-brake run, Jev was already selecting `caution` before the brake trigger and
continued doing so. In the pedestrian run, the first post-trigger reply was
rejected; the fallback selected `yield`, followed by Jev `caution` choices and
later progress. Those timings describe a simulator that pauses for inference.

## Evidence

[summary.json](summary.json) retains specs, run IDs, trace and implementation
hashes, versions, physical metrics, camera counts, restoration results and
decision excerpts. The allowlisted [lead-brake](lead_brake-projection.json),
[cut-in](cut_in-projection.json) and [pedestrian](pedestrian_crossing-projection.json)
projections preserve every recorded camera frame's observations and controls.
[demo.manifest.json](demo.manifest.json) maps displayed video frames to source
frames and pins the compressed video, renderer and projections by SHA-256.

All three Jev runs used the same package source digest:
`ce471671ddad8b897ade9e28c7aa24086254bcc3072cf738ac36a7eaeaddc5bc`.
They used `route-tactics-v1`, `route-pure-pursuit-v1`,
`carla-route-choice-v1`, Jev `jev-1.13.0`, SDK 0.7.2, and the official
CARLA 0.10.0 Windows server with its matching Linux Python client in WSL2.
The temporary local streaming relay used for the earlier UE5 tests was reused;
native Windows-to-WSL connectivity without it is not claimed.

The runs retained 259, 231 and 393 original camera images respectively, with
zero recorder queue drops. Every retained camera image matched an observation
and execution with the same frame ID and verified image hash. The first image
interval in each capture was 0.25s; subsequent intervals were 0.2s. Playback
uses uniform 5fps, so it is approximately simulation speed within selected
windows. Initial/final sensor latency and the deliberate windows mean the video
does not contain every simulation frame. Raw camera images and complete traces
remain local.

The scenario generator controls the hazard, while Jev selects `cruise`, `caution`
or `yield` from current numerical traffic observations. Jev receives no future
hazard trigger, camera perception, hidden driver intention, steering command or
general route-planning ability. Local code owns geometry, per-frame braking,
signal handling, timing and cleanup. Route completion uses a one-metre position
tolerance and speed below 0.4m/s, then commands full braking before cleanup.

Minimum observed box clearance includes the adjacent background vehicle and is
a separating-axis lower bound, not the minimum distance to the hazard alone.
Constant-velocity crossing predictions do not model occlusion or future turns
and acceleration. Traffic-light phases were observed rather than reset. These
runs must not be pooled with earlier merge cohorts or treated as a matched
rules-versus-Jev benchmark.

## Fixes and retained failures

Nine setup/calibration runs are retained separately in the summary. Early tests
found understeer at the junction, background traffic driving past the end of its
route during a long signal wait, a too-short simulation bound for that wait,
and insufficient pedestrian spawn clearance. The corresponding fixes were
checked with regressions before repeating the physical scenarios. The failed
pedestrian diagnostic's pre-frame cleanup needed explicit journal recovery;
the recovery succeeded and is retained. No failed calibration run is relabeled
as one of the three final Jev successes.

The no-key baseline completed all three scenarios after these fixes. A separate
no-key pedestrian camera check also completed with restored state. The full
local quality gate passed 630 Python tests, with two expected skips, and nine
Rust tests, plus lint, typing, complexity, formatting and build checks. The
route suite has 31 cases. The independent cleanup fix in PR #92 also passed
[CI 37370705768](https://github.com/sidkolapalli/carla-agentic-toolkit/actions/runs/37370705768).

## Reproduce the recording

Follow the [route setup and specifications](../../route-experiments.md). Capture
each scenario into a new directory with `scripts.capture_route_demo`. The
capture process needs the live CARLA client and optional Jev SDK; the renderer
needs Pillow 12.3.0, a local FFmpeg executable and a TrueType font.

```bash
# Run where the saved private trace paths are accessible.
python -m scripts.render_route_demo --project-only \
  capture-jev-lead_brake capture-jev-cut_in capture-jev-pedestrian_crossing

# The resulting allowlisted projections and original camera folders are portable.
python -m scripts.render_route_demo \
  --output route-video --font /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
  capture-jev-lead_brake capture-jev-cut_in capture-jev-pedestrian_crossing
```

On Windows, an installed font such as `C:/Windows/Fonts/segoeui.ttf` can be used
for the second command. Projection regenerates only derived JSON; original
camera images and private traces are not changed.

Scene credit: CARLA Simulator contributors, CARLA 0.10.0, Town10HD_Opt. The
[upstream license notice](https://github.com/carla-simulator/carla/blob/0.10.0/README.md#licenses)
identifies CARLA-specific assets as CC-BY. Frame selection, letterboxing and
explanatory overlays were produced for this project; underlying scene assets
retain their upstream terms independently of the toolkit's MIT license. No
CARLA or TypeSafe endorsement is implied.
