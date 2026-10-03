# Managed merge experiments

The managed runtime accepts a validated `ExperimentSpec` and runs the reviewed
`town10-merge-v1` fixture in a trusted local worker. It is a separate execution mode
from finite generated scripts. Specifications cannot contain code, imports, shell
commands, provider URLs, credentials, or unknown fields.

Use Linux or WSL2, the repository environment, and a matching CARLA Python API.
Start a dedicated CARLA instance with Town10HD loaded. The runtime rejects worlds
containing existing `vehicle.*`, `walker.*`, `sensor.*`, or `controller.*` actors.
Start the world in asynchronous mode: preflight waits for a fresh actor snapshot
before changing settings. An already paused synchronous world can time out during
that check; preflight does not tick an unverified world to make it appear ready.
It verifies a finite straight corridor with adjacent driving lanes before spawning
the two vehicles. It does not reload a different map, admit background density, or
enable Traffic Manager. Use the existing source setup and sandbox preflight in the
[client guide](client-setup.md) before enabling generated-script tools alongside it.

## No-key baseline and lifecycle

The checked-in rules configuration uses the default local endpoint. Change `host`
and `port` in a copy when CARLA is elsewhere. WSL NAT normally needs the Windows
host address, not the WSL loopback address.

```bash
uv run --no-sync carla-agentic-toolkit-experiment run \
  --spec docs/examples/merge-rules-v1.json
```

The checked-in [Python example](../scripts/managed_baseline.py) uses the public
`ManagedController` start/status/stop API and returns the same numerical JSON result:

```bash
uv run --no-sync python scripts/managed_baseline.py \
  --spec docs/examples/merge-rules-v1.json
```

Import `run_baseline` and pass an `ExperimentSpec(policy="rules", host=..., port=...)`
to use it from Python. It rejects provider policies before launching, requests stop
on Ctrl-C or a caller-side failure, and bounds polling by the run deadline plus a
45-second cleanup allowance. If that allowance expires, `example_wait_expired: true`
reports the last observed status without claiming termination or cleanup; use the
returned run ID with `status` or `recover` below. The detached supervisor retains
ownership until it verifies termination and cleanup.

`run` waits for the supervisor's terminal result; Ctrl-C requests cancellation and
continues waiting for termination. For responsive interactive control:

```bash
uv run --no-sync carla-agentic-toolkit-experiment start \
  --spec docs/examples/merge-rules-v1.json

# Replace RUN_ID with the opaque 32-character ID returned by start.
uv run --no-sync carla-agentic-toolkit-experiment status RUN_ID
uv run --no-sync carla-agentic-toolkit-experiment stop RUN_ID
uv run --no-sync carla-agentic-toolkit-experiment result RUN_ID
```

`start` returns `state: starting` and `terminated: false` promptly. Status/result
read bounded local files; they do not wait on inference, simulator RPCs, or cleanup.
Repeated `stop` requests are safe. `cancellation_requested` does not mean the worker
has died. The lifecycle may pass through `stopping` and `recovering` before reaching
`completed`, `failed`, or `cancelled`. Check `terminated`, `cleanup.ok`, and the
physical `outcome.completed` independently. The final response includes `trace_path`
and `reports.summary`/`reports.report` paths when evidence generation succeeded.

A detached supervisor owns the worker process group, grants a bounded cleanup grace,
then terminates the group if necessary. It confirms termination before recovery.
The simulator lease covers mutations and cleanup. Failed or unverified recovery
retains dirty evidence and blocks another cooperating owner. Inspect the failure;
do not delete dirty lease evidence merely to bypass it.

If the detached supervisor dies, the next `status` or `result` call starts a
separate recovery coordinator. It checks recorded process identity, verifies the
worker group has stopped, and then performs bounded cleanup under the original
simulator lease. Status remains responsive throughout. Automatic coordinator
launch retries stop after three attempts. Missing or changed process identity
fails closed with `supervisor_lost: true` and `recovery_required: true`; an
unverified process death keeps `terminated: false`. The foreground command returns
that explicit failure instead of waiting indefinitely.

After restoring an unavailable CARLA server, retry cleanup of the same saved job:

```bash
uv run --no-sync carla-agentic-toolkit-experiment recover RUN_ID
uv run --no-sync carla-agentic-toolkit-experiment status RUN_ID
```

An explicit retry starts a fresh bounded recovery attempt without rerunning the
experiment or changing its original failed outcome. It refuses a run with a live
supervisor. Keep the original private state directory and CARLA endpoint, and wait
for `cleanup.ok: true` and `recovery_required: false` before starting another run.
If process identity cannot be verified or the journal is corrupt, preserve the
evidence for local operator inspection; this command never deletes ambiguous
ownership evidence to force progress.

## Optional MCP lifecycle

Set `CARLA_AGENTIC_TOOLKIT_MANAGED_EXPERIMENTS=1` in the trusted stdio server
environment and restart it. The additional `managed_experiment` tool accepts:

| Action | Arguments |
| --- | --- |
| `start` | `spec`: the same numerical JSON object accepted by the CLI |
| `status` | `run_id` returned by start |
| `stop` | `run_id`; requests cancellation without claiming cleanup |
| `result` | `run_id`; returns bounded status and evidence paths |
| `recover` | `run_id`; retries cleanup of the saved run after its supervisor stopped |

The default `execute_carla_script` surface remains available. This opt-in does not
enable remote transport or arbitrary persistent code. Configure the same private
state root in the CLI and every local MCP process so their ownership checks agree.

## Optional Jev selection

The optional dependency is pinned to `typesafe-sdk==0.7.2`. Reviewed code pins model
`jev-1.13.0`, question version `carla-merge-choice-v1`, and the provider endpoint.
The specification selects `policy: jev`; it cannot override those provider settings.

```bash
uv sync --locked --extra jev --python 3.12
uv pip install --python .venv "carla==0.9.16"  # Match your simulator version.

# Supply through your trusted secret environment; never put the value in a spec.
read -rsp "TypeSafe API key: " TYPESAFE_API_KEY
export TYPESAFE_API_KEY

uv run --no-sync carla-agentic-toolkit-experiment run \
  --spec docs/examples/merge-jev-v1.json
```

The rules policy requires neither the SDK nor a key. The Jev adapter keeps one async
client for the run and makes Choice requests over code-generated candidate IDs.
Geometry, reachability, speed units, trajectory tracking, and actuation stay in code.
All request context is revalidated before application: run/world/actor, revision,
observation frame, candidate set, maneuver generation, phase, deadline, and current
applicability. Rejected or unavailable replies select a phase-appropriate fallback
and retain the reason. Choice probabilities are semantic preferences, not collision
probabilities or safety guarantees.

The default is one HTTP attempt per decision. Each attempt reserves 65,536 input
and 65,536 output tokens conservatively before networking; reservations are never
refunded from absent usage. Shared private account counters start with 200 attempts
and their corresponding reservation budget and persist across runs. They do not
reset automatically. Changing state roots does not constitute account-wide provider
coordination; all local workers must share one root. Recorded actual usage remains
separate and is null when unavailable.

With a configured key, the independent provider smoke check is explicitly opt-in:

```bash
CARLA_AGENTIC_TOOLKIT_JEV_LIVE=1 uv run --no-sync pytest tests/test_managed_jev_live.py
```

It makes a bounded synthetic request. Passing offline fakes or this single provider
check alone does not establish a successful closed-loop CARLA/Jev comparison.

## Observation, timing, and control

The observation mode is **range-filtered simulator ground truth**, using one
WorldSnapshot's frame/time and actor kinematics, bounds, lane geometry, markings,
connectivity, maneuver phase, and recent history. It is not an occlusion-aware sensor
perception stack and does not infer hidden driver intentions. Collision, lane-invasion,
and GNSS subscriptions preserve measurement frames, drops, delay, and trailing data.

One session owns every scheduled tick. The fixture/controller and provider never
tick. Each controlled vehicle has one assigned tracker; protected actors cannot be
adopted or reset by background traffic. World replacement or unexpected advancement
invalidates the run rather than silently accepting stale observations.

- `simulation_time` pauses at decision boundaries while awaiting bounded inference.
- `paced` continues scheduled owner steps while inference is pending and records
  current fallback controls. It targets the configured wall pacing but makes no
  hard real-time promise. Reports include achieved real-time factor and frame age.

Mode setup may advance CARLA frames inside `apply_settings`; setup is distinct from
the scheduled experiment budget. Scripts that manage their own sensors should follow
the [explicit sensor timing contract](sensor-timing.md).

## Limits, storage, and evidence

| Setting | Default | Supported bound |
| --- | --- | --- |
| Fixed simulation step | 0.05 s | 0.01–0.1 s; must fit the physics substep budget |
| Scheduled steps | 600 | 1–3600 |
| Wall deadline | 180 s | 1–3600 s |
| Simulator RPC deadline | 5 s | 0.1–10 s |
| Decision deadline | 5 s | 0.1–30 s |
| Decision interval | 10 steps | 1–100 steps |
| Provider requests per run | 40 | 1–200 |
| Trace size | 16 MiB | 64 KiB–64 MiB; 256 KiB per event |
| Local active/retained jobs | 4 / 100 | New starts fail at capacity; evidence is not overwritten |
| Lifecycle JSON input/output | 64 KiB | Oversized or non-object controls fail |

Run IDs and control files belong to the local OS user. The default state root is
`~/.local/state/carla-agentic-toolkit`; override it with
`CARLA_AGENTIC_TOOLKIT_STATE_DIR` only to another private directory outside the
project, runtime, and script-output allowlists. Directories require owner-only
permissions. All cooperating processes must use the same root and unambiguous
simulator address. The lease cannot coordinate unrelated CARLA clients.

Runs write append-only `runs/RUN_ID/events.jsonl`, then derive `summary.json` and
`report.html`. Generated scripts cannot read or modify these private traces.
Sensor errors, simulator failures, interrupted traces, and failed cleanup remain
visible; infrastructure-invalid evidence must not be counted as successful policy
behavior. Archive completed jobs and evidence deliberately when retention is full;
there is no automatic history deletion. Docker's default trusted state is ephemeral
under `/tmp`; managed runs that must survive container removal need a separate,
owner-only persistent state mount outside `/output` and `/app`.

The default headless Docker image contains no shell or package manager and does
not include the optional Jev SDK. Use the source installation above for Jev.

Live managed starts support `policy: rules` and `policy: jev`. `policy: replay`
is rejected before a job is created, a supervisor is launched, or the direct engine
acquires a simulator lease or connects to CARLA. Every fresh session creates a new
world generation, actor IDs, and frames, so it cannot satisfy exact saved-context
replay identity. The specification still parses historical replay records and their
required `replay_run_id` for offline compatibility; this does not enable live replay.

See [experiment evidence](experiment-evidence.md#offline-replay-identity) for schema,
units, gap/TTC assumptions, and the supported offline `RecordedPolicy` API. It
replays decisions against saved observations and candidate contexts without starting
a scene. Playing fixed actions into a different scene is a different experiment.

## Comparing policies and recording a demo

The two example specs differ only in policy. Keep fixture, initial poses, seeds,
controller/planner versions, observation/timing mode, and constraints matched.
Retain each run's exact spec, code hash, environment, model/question metadata, and
summary/report. Repeat a declared seed set and report sample counts, completion and
collision definitions, invalid runs, interventions/fallbacks, decision latency,
staleness, and achieved real-time factor. Repeated seeds do not guarantee bitwise
simulator determinism. Attribute controller interventions to the controller.

Generate the [matched static comparison](experiment-evidence.md#matched-static-comparison)
from every saved trial. It checks exact fixture/specification/version matching and
retains invalid and partial runs in the sample counts; mismatches refuse comparative
metrics rather than producing a policy ranking.

For a demo, show the run ID and live CARLA view, then the terminal cleanup state and
saved report. Also show a bounded stop or documented provider fallback. A video is
supporting material; retain the machine-readable evidence and do not claim improved
realism or safety without a completed matched evaluation.
