# From a Jev choice to a measured CARLA maneuver

**Editorial draft.** The original integration article has not been located or
read. This draft must be compared with that article before it is described as a
finished follow-up. The results and trace excerpt below come from the saved
six-trial evaluation, not an illustrative or reconstructed response.

The integration now has an executable boundary: Jev selects a candidate generated
by local numerical planning, and a trusted CARLA session decides whether that
selection is still applicable before the shared controller acts on it. A provider
response alone is not a completed maneuver. The evidence must connect the scene,
request, accepted choice, executed controls and physical outcome.

This is a narrow experiment: one independently controlled ego and one merging
vehicle in the versioned `town10-merge-v1` fixture. It uses a dedicated CARLA
instance and a verified straight corridor with adjacent driving lanes. It is not
a general driving policy, an arbitrary-map planner, or a safety evaluation.

## Who owns each decision

| Responsibility | Implementation boundary |
| --- | --- |
| World and time | One trusted session holds the simulator lease, retains its client/world handles and owns every scheduled tick. |
| Observation | A frame-consistent snapshot supplies numerical kinematics, bounds, lane geometry, maneuver phase and recent history. |
| Candidate generation | Local code checks the corridor, lane legality, gaps, relative motion, applicability and expiry. |
| Policy selection | Rules or Jev selects among the same bounded candidate IDs, with a phase-appropriate fallback. |
| Application | Freshness and identity checks precede the same numerical trajectory tracker and controls. |
| Evidence and cleanup | Private append-only events preserve the request and execution separately; the supervisor verifies termination and recovery. |

The observation is **range-filtered simulator ground truth**. It is not an
occlusion-aware perception stack, and it does not reveal another driver's intent.
Collision, lane-invasion and GNSS subscriptions add frame-tagged sensor evidence.
An event sensor can correctly have no sample on a frame; the runtime does not wait
for a collision merely to finish a tick.

The optional provider adapter pins `typesafe-sdk==0.7.2`, requests `jev-1.13.0`,
and uses the reviewed question version `carla-merge-choice-v1`. Requested versions
and returned provider metadata are recorded separately. The model receives
bounded numerical evidence and candidate descriptions. It does not return steering
commands, invent a trajectory, or acquire tick ownership. Choice probabilities
describe the provider's selection, not calibrated collision probabilities.

The trusted worker holds the provider credential. Generated scripts still run
behind the Rust/Landlock boundary and receive neither that credential nor general
provider networking. Managed specifications accept reviewed data fields, not
arbitrary Python, shell commands or provider URLs.

## Timing changes what a reply means

The experiment has two explicit modes. In `simulation_time`, the owner pauses at
a decision boundary while bounded inference completes. In `paced`, scheduled steps
continue while inference is pending, using current phase-appropriate fallback
controls. Pacing is a target, not a hard real-time guarantee.

Before accepting a reply, the runtime checks the run, world generation, actor,
request revision, observation frame, candidate set, maneuver generation, phase,
deadline and current applicability. A response can be recent in wall time and
still refer to a scene that is no longer valid. Missing, stale, malformed,
cancelled or failed provider responses retain their reason and follow the fallback
path. Continuing a committed crossing and deferring before commitment are different
control decisions; a generic stop is not substituted for both.

Exactly one frame must follow each scheduled owner tick. An external tick, world
replacement or simulator failure invalidates the experiment rather than silently
changing its timing assumptions. Setup and cleanup are separate from the scheduled
experiment budget. A successful cleanup does not turn an interrupted run into a
successful experiment.

## What has been observed

The separately configured live provider smoke check passed. It validates one
bounded synthetic Choice request through the pinned SDK/provider path; it does
not by itself validate closed-loop CARLA behavior or establish a policy comparison.

The [saved static comparison](evidence/merge-comparison-2026-10-02/comparison.html)
contains one rules run and one Jev run for each of seeds 7, 19 and 31. The plan
declared six trials and no retries. All six physically completed, passed cleanup,
and restored their actors and world settings. None were partial, invalid or
excluded, and the comparison reported `comparable: true` with no blockers.

The implementation was commit
[`49a739e`](https://github.com/sidkolapalli/carla-agentic-toolkit/commit/49a739ebab377f04161afd2201d93fab1a4bd8d7),
with package-source fingerprint
`85f1aa3aae493397edb61b242e4cc7a0cf2b57619a372837d3fe5d1ed0ff4d68`.
The environment used CARLA server/API 0.9.16, Python 3.12.14 and WSL2 kernel
6.18.33.2. These were `simulation_time` runs with a 0.05-second fixed step, a
6 m/s policy target and a 5 m/s ego target. Jev made 36 recorded requests across
its three runs.

| Saved measure | Rules | Jev |
| --- | --- | --- |
| Completed / eligible trials | 3 / 3 | 3 / 3 |
| Runs with delivered collision evidence / eligible trials | 0 / 3 | 0 / 3 |
| Partial, invalid or excluded trials | 0 | 0 |
| Mean interventions / fallbacks per run | 0 / 0 | 0 / 0 |
| Mean lane-invasion events per run | 4 | 4 |
| Mean sensor drops / reversals per run | 0 / 0 | 0 / 0 |
| Mean per-run tracking RMSE | 0.846222 m | 0.844090 m |
| Mean of per-run provider decision latencies | Not recorded; denominator 0 | 0.155717 s; denominator 3 |
| Mean maximum decision age | 0 frames | 0 frames |
| Mean real-time factor | 3.012089 | 0.820965 |

Each numerical mean above has three contributing runs unless stated otherwise.
The report retains full precision, per-run values, sensor coverage and trailing
events. Real-time factor is simulation duration divided by full-trace monotonic
duration; its reported aggregate is an unweighted mean of per-run ratios. Rules
varied from about 1.15 to 6.73 across these runs, so this is not a controlled
performance benchmark. The shared lane-crossing maneuver generated lane-invasion
events; these counts are not collision measurements.

Requested and returned models were recorded as `jev-1.13.0`. The local question
version was `carla-merge-choice-v1`; the provider did not supply a returned question
version, so that field remains null. Actual returned usage is preserved in the
report. Rules usage and its provider-latency mean are null, not invented zeros.
This small fixture evaluation does not establish improved safety, realism or
general policy quality.

The comparison tool requires the same fixture and initial poses, specification
apart from policy/replay identity, seeds, environment, code, planner and controller.
Repeated seeds are an evaluation procedure, not a promise of bitwise CARLA
determinism. Equal sample counts are required within each matched cohort.

## One decision, followed through execution

The seed-7 Jev run `34845c7950f6411f88d2989404dc19e3` contains the following sequence
for actor 41 at frame 201389, world generation
`c3da49dc6b3b4a2792c7fb62c45dd4f1`. The
[reviewed excerpt](evidence/merge-comparison-2026-10-02/decision-trace-excerpt.json)
preserves the event envelopes and retained numerical values. This shorter view
omits timestamps, repeated identity, geometry arrays and provider metadata:

```json
[
  {"sequence": 209, "kind": "observation", "frame": 201389,
   "phase": "preparing", "speed_mps": 3.260416455628212},
  {"sequence": 220, "kind": "decision_requested", "frame": 201389,
   "revision": 3, "candidate_ids": ["defer", "merge"],
   "merge_target_lane_id": -2, "merge_target_speed_mps": 6.0,
   "expires_frame": 201409},
  {"sequence": 221, "kind": "decision_received", "frame": 201389,
   "choice_id": "merge", "source": "jev",
   "latency_seconds": 0.1348699209993356, "staleness_frames": 0},
  {"sequence": 222, "kind": "validation", "frame": 201389,
   "accepted": true, "reason": null},
  {"sequence": 223, "kind": "execution", "frame": 201389,
   "executed_choice": "merge", "phase": "committed",
   "maneuver_generation": 1,
   "policy_control": {"throttle": 0.65, "steer": 4.684550542746164e-06, "brake": 0.0},
   "completed": false}
]
```

The display flattens selected nested fields; `candidate_ids`, the prefixed merge
fields and `policy_control` are presentation names, not additional trace-schema
fields. Sequence gaps correspond to omitted sensor events. The complete selected
candidate set and its digest remain in the linked excerpt. Both candidates were
generated locally; the response selected one, validation accepted revision 3, and
the controller recorded its first committed action. `completed: false` correctly
means the maneuver had started, not finished, at this frame. Completion came later
and is recorded separately in the final outcome.

The response metadata recorded selection probabilities of 0.38 for `defer` and
0.62 for `merge`, with 3,838 input and 33 output tokens. Those numbers are retained
as provider metadata. A 0.62 selection probability is not a 62% chance that a merge
is safe. The control values came from the shared tracker, not the provider.

The authoritative traces remain private. A report is a derived view: observations
and executed controls remain numerical, provider metadata retains its provenance,
and interruption/cleanup failures remain visible. Exact recorded-response replay
operates offline against the saved observation and candidate identity. Fresh live
replay is rejected before mutation; running old actions against a new scene is a
different experiment.

## A separate budget-fallback demonstration

A subsequent seed-7 probe changed only `max_requests` to 1. It is outside the
predeclared six-trial comparison. Run `919533e9c8074854872e4fb78b42e90c` recorded
one `selected` decision and 17 `budget_exhausted` decisions. Its
[saved result](evidence/merge-budget-fallback-2026-10-02.json) has
`terminated: true`, `cleanup.ok: true`, and verified actor/settings restoration.

The physical outcome was **not a completed merge**: `result.ok` was false and
`outcome` was `{"completed": false, "status": "corridor_invalid"}`. The lifecycle
state `completed` records that the worker finished; it does not override that
physical result. This probe verifies recorded budget fallback and cleanup, not
successful maneuver completion under an exhausted budget.

To reproduce the bounded failure path, use the same trusted provider setup and
create a separate configuration:

```bash
uv run --no-sync python - <<'PY'
import json
from pathlib import Path

spec = json.loads(Path("docs/examples/merge-jev-v1.json").read_text())
spec["max_requests"] = 1
# Set host/port to your dedicated CARLA instance as in the successful run.
path = Path("target/merge-budget-fallback.json")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(spec, indent=2) + "\n")
PY
uv run --no-sync carla-agentic-toolkit-experiment run \
  --spec target/merge-budget-fallback.json
```

Retain the returned result even when the command exits nonzero. Check termination,
cleanup and the physical outcome separately; do not expect every repeat to have
the same decision count. No video was recorded for either this probe or the six
matched trials. A demonstration recording can show a successful rules run followed
by this explicitly unsuccessful maneuver and its verified cleanup.

## Independent reproduction and a recorded view

On 2026-10-03, an independent automated operator followed the checked-in
instructions in a new native WSL checkout, virtual environment and private state
directory. It reproduced all six declared trials without retries: three eligible
completed runs per policy, no comparison blockers, verified cleanup, and 36
actual Jev selections. The [replication evidence](evidence/independent-reproduction-2026-10-03/README.md)
retains the setup deviation needed to clone the private repository, exact specs,
trace hashes and final simulator state. This was a separate operator on the same
host, not an external human or second-machine portability test. Its results form
a separate cohort rather than enlarging the sample behind the table above.

The [40-second camera demonstration](evidence/managed-demo-2026-10-03/README.md)
uses two additional instrumented runs. One completed the merge after 12 Jev
selections. The other allowed one request, then recorded 17 budget-exhausted
fallbacks before ending with an incomplete `corridor_invalid` maneuver. Both
restored actors and settings. The original CARLA birdseye images are joined to
their exact observation frames, with prior provider decisions and current
controller execution shown separately. Missing images are counted and skipped;
the slowed video does not stand in for wall-clock performance measurements.

The recording makes the distinction visible: finishing the process and cleaning
up successfully do not turn an incomplete maneuver into a completed one.

## Reproduce the experiment

Use the [managed experiment setup](managed-experiments.md), matching CARLA and
Python API versions, and a dedicated initially asynchronous Town10HD instance.
Begin with the [versioned rules configuration](examples/merge-rules-v1.json):

```bash
uv run --no-sync python scripts/managed_baseline.py \
  --spec docs/examples/merge-rules-v1.json
```

After configuring the optional SDK and credential in the trusted environment, use
the [Jev configuration](examples/merge-jev-v1.json):

```bash
uv run --no-sync carla-agentic-toolkit-experiment run \
  --spec docs/examples/merge-jev-v1.json
```

Set the same simulator endpoint and evaluation parameters in both configurations;
change only the policy within a pair. The returned JSON gives the run ID,
termination state, cleanup status, physical outcome and evidence/report paths.
`outcome.completed`, `terminated` and `cleanup.ok` answer different questions.
Preserve every attempted trial, including failures, and pass all declared traces
to the static comparison:

```bash
uv run --no-sync python -m carla_agentic_toolkit.experiment_comparison \
  --output "$HOME/carla-comparison" \
  /absolute/path/to/rules/events.jsonl /absolute/path/to/jev/events.jsonl
```

Repeat the trace arguments for the declared seeds and replicates. Exit code zero
means the recorded matching checks passed, not that one policy performed better.
Exit code two means comparative metrics were withheld; the report explains why.
The [evidence guide](experiment-evidence.md) defines each metric and denominator.

## What this evidence cannot establish

This fixture does not include background traffic, realistic occlusion, human
driver sampling or arbitrary road networks. Controller interventions belong to the
shared controller and must not be credited to provider reasoning. No delivered
collision events is a statement about recorded sensor evidence, not a proof that
the system is safe. Latency and real-time factor depend on the declared timing
mode and environment; semantic confidence is not a physical risk measure.

The cooperative simulator lease cannot isolate an unrelated hostile CARLA client.
Provider budgets and traces are local bounded resources, and cleanup can fail when
the simulator is unreachable. These limitations remain explicit in the
[architecture](managed-architecture.md) and [security policy](../SECURITY.md).
Publication still requires the original-article review and the corresponding
final candidate validation described in
[release readiness](release-readiness.md).
