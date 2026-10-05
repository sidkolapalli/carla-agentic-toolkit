# Route driving with Jev and traffic

[Watch the recorded trials and read the results](evidence/route-hazards-2026-10-05/README.md).

The experimental `town10-route-ue5-v1` fixture takes a vehicle along a 135m
Town10HD route, including a junction turn, while interacting with owned traffic.
It supports three physical scenarios on CARLA 0.10.0 / UE5.5:

| Scenario | What happens | Rules / Jev specifications |
| --- | --- | --- |
| `lead_brake` | A leading vehicle brakes for three simulation seconds, then resumes. | [Rules](examples/route-lead-brake-rules-ue5-v1.json) / [Jev](examples/route-lead-brake-jev-ue5-v1.json) |
| `cut_in` | A vehicle in the adjacent lane physically steers into the route over 2.5 seconds. | [Rules](examples/route-cut-in-rules-ue5-v1.json) / [Jev](examples/route-cut-in-jev-ue5-v1.json) |
| `pedestrian_crossing` | A pedestrian walks across the road at 2m/s, with surrounding vehicle traffic. | [Rules](examples/route-pedestrian-crossing-rules-ue5-v1.json) / [Jev](examples/route-pedestrian-crossing-jev-ue5-v1.json) |

Each hazard starts after the policy car has travelled 12m. The fixture uses
`vehicle.lincoln.mkz` and `walker.pedestrian.0015`. The scenario controller owns
the background vehicles and walker. Their controls act through CARLA physics;
actors are not teleported through a hazard. Background vehicles obey traffic
lights and stop before exhausting their verified route.

## Jev's job

Jev repeatedly selects a typed tactical choice:

| Choice | Requested speed | Meaning |
| --- | --- | --- |
| `cruise` | 6m/s in the examples | Continue along the route when the way ahead is clear. |
| `caution` | 45% of the requested cruising speed | Slow for uncertain or developing traffic. |
| `yield` | 0m/s | Brake for a conflict and reassess at the next decision boundary. |

This uses model `jev-1.13.0`, the pinned TypeSafe SDK, and question version
`carla-route-choice-v1`. The merge experiment retains its own question and
maneuver semantics. Probabilities describe the choice; they are not estimates
of crash probability.

Local code selects the reviewed lane-successor route and computes steering,
throttle and braking. It rechecks current traffic every frame. An imminent
obstacle or red/yellow light can override the selected tactic with braking.
The trace records the requested choice, its source, its observation frame,
executed choice, physical controls and local intervention reason separately.
An accepted tactic lasts only through its bounded simulation-frame horizon.
An invalid, pending or exhausted provider request falls back to `yield`.

Jev sees range-filtered, frame-consistent simulator ground truth: positions,
velocities, body dimensions, route progress, light state, recent history and
numerical conflict estimates. It does not receive the scenario name, its future
trigger or the background controller's intentions. The camera is recording
instrumentation; this integration does not perform visual perception.

## Run a scenario

Use a dedicated, initially asynchronous Town10HD or Town10HD_Opt server with no
existing vehicles, walkers or sensors, and a matching CARLA 0.10.0 Python client.
The same [managed ownership and recovery rules](managed-experiments.md) apply.
Copy a specification and set its reachable `host` and `port`.

```bash
carla-agentic-toolkit-experiment run \
  --spec docs/examples/route-lead-brake-rules-ue5-v1.json
```

For Jev, install the optional `jev` extra and provide `TYPESAFE_API_KEY` through
the trusted worker process environment. On Windows/WSL2, it must reach the
WSL process. Then use the corresponding `-jev-` specification. Do not put a credential in
the specification, trace, command history or generated script.

The examples request a decision every 40 frames (two simulation seconds) and
allow at most 28 provider attempts per run. The persistent account budget is
shared with other experiments and is not reset by starting a new run. The
world pauses during inference in `simulation_time` mode. These recordings do
not measure real-time driving response. `paced` mode uses the same asynchronous
scheduler and local guard, but is a separate, unvalidated route timing cohort.

The run ends on near-stationary arrival at the destination, a delivered collision,
excessive route tracking error, cancellation, or the frame/wall deadline.
Long signal waits are included in the 120 simulation-second bound. Traffic-light
phase is observed rather than reset; separate trials can encounter different
signal timing. Treat initial single runs as exploratory evidence, not a matched
performance benchmark.

## Inspect the response

Saved `events.jsonl`, `summary.json` and `report.html` include physical outcome,
collision delivery, tracking error, usage and cleanup evidence. Route reports add
measured progress, the fixed goal, accepted choices by source, local guard frame
counts, and minimum observed separating-axis body-box clearance.

The crossing estimate projects current world velocity and yaw over four seconds
at 0.1s intervals using padded 2D boxes. It ignores future acceleration, turns,
occlusion, perception error and intent. Its clearance is a separating-axis lower
bound, not exact closest-body distance. Delivered collision events remain a
separate measurement. Zero delivered events do not establish safety.

Record a trial from a source checkout with its exact frame identities:

```bash
python -m scripts.capture_route_demo --confirm-live \
  --spec docs/examples/route-pedestrian-crossing-jev-ue5-v1.json \
  --state-dir "$CARLA_AGENTIC_TOOLKIT_STATE_DIR" \
  --output ./route-capture
```

Use a new output directory outside private state. This adds a session-owned
960x540 elevated chase camera at five images per simulation second, preserving
original PNGs and hashes. Capture and restoration checks are reported separately
from whether the route completed. The simulator, camera and native client must
remain available until cleanup finishes.

This is a fixed-route test harness with three controlled hazards. Arbitrary
destinations, dense general traffic, occlusion-aware perception and unrestricted
autonomous driving are outside its validated scope.
