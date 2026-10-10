# Managed merge experiments

For repeated route driving with lead braking, cut-in traffic and an intended
pedestrian crossing, see [Route driving with Jev and traffic](route-experiments.md).
The recorded crossing was not achieved; the [analytical study](evidence/route-study-2026-10-05/README.md)
documents measured hazard motion and the remaining validation gap.
Those scenarios use the same managed owner and a distinct fixture, controller,
question version and route-specific metrics.

The managed runtime accepts a validated `ExperimentSpec` in a trusted local worker.
This guide covers the reviewed `town10-merge-v1` fixture and the explicit
experimental UE5 merge variant described below. It is a separate execution mode
from finite generated scripts. Specifications cannot contain code, imports, shell
commands, provider URLs, credentials, or unknown fields.

Use Linux or WSL2, the repository environment, and a matching CARLA Python API.
Startup records available full client/server versions and rejects mismatched or
unknown release compatibility before policy/session construction or simulator
mutation; see [version matching](client-setup.md#version-matching).
Start a dedicated CARLA instance with Town10HD loaded. The runtime rejects worlds
containing existing `vehicle.*`, `walker.*`, `sensor.*`, or `controller.*` actors.
Start the world in asynchronous mode: preflight waits for a fresh actor snapshot
before changing settings. An already paused synchronous world can time out during
that check; preflight does not tick an unverified world to make it appear ready.
It verifies a finite straight corridor with adjacent driving lanes before spawning
the two vehicles. It does not reload a different map. By default it admits no
background density and never constructs or enables Traffic Manager; the optional
managed-density path below is separate. Use the existing source setup and sandbox
preflight in the [client guide](client-setup.md) before enabling generated-script
tools alongside it.

## Optional managed density

Only the trusted managed worker can opt into background vehicles. Supply
`background_density` in the numerical specification:

```json
{
  "background_density": {
    "vehicle_count": 12,
    "traffic_manager_port": 8100,
    "maintenance_interval_steps": 20
  }
}
```

`vehicle_count` is required and accepts integers from 1 through 100;
`traffic_manager_port` is required and accepts integers from 1 through 65535.
`maintenance_interval_steps` defaults to 20 and accepts integers from 1 through
200. The object is strict: unknown fields and non-integer values are rejected.
Absent or `null` density stays disabled and is omitted from serialized specs.
The operator must provide a fresh dedicated simulator and an unused local TM
port; this path does not adopt an existing or remote Traffic Manager.

Before changing world settings, the worker retains a private shared port lock
and durably proves that the newly created TM listener belongs to its current
PID, process start time and boot ID. It retains the original LISTEN socket inode.
The host stays asynchronous through the acknowledged settings-preserving reload
and fixture preparation. Only then can fresh same-episode and host proofs
authorize TM synchronous mode. Those proofs are checked again before every TM
mode or autopilot mutation. No TM random seed or global settings are changed.
Fixture actors stay protected under their original local controllers; existing
scene actors and resembling roles are never adopted.

Initial population attempts at most four native spawns. Later maintenance runs
at the configured owner-step interval, before that boundary's single scheduled
tick, with independent limits of four spawn attempts and four authoritative
missing-owned removals. Snapshot absence can trigger removal only after a later
owner frame than creation. There are no maintenance ticks, waits or background
threads. Background kinematics come from the same supplied owner snapshot as
the fixture and retain its observation-range filter. The `background_density`
event distinguishes the configured target from acknowledged registrations;
registration count is not a measured live population or a deterministic result.

Cleanup unregisters known background handles, then uses guarded, non-ticking
authoritative deletion. Six-setting restoration requires acknowledged shutdown
of the proven local TM and confirmation that its listener closed. Unknown host
construction, registration, deregistration or reload outcomes remain dirty;
worker death alone is not shutdown proof. Recovery never constructs a new TM or
shuts down a potentially remote one. Missing or invalid density ownership
evidence refuses recovery ticks, waits, actor discovery and settings writes.
Preserve the dirty evidence for reviewed operator resolution.
A prior density error may be resolved only when the journal already proves
complete local-host closure and authoritative cleanup resolves every known
actor, with no unresolved creation or reload intent; the original failed run
remains failed.

This opt-in does not relax generated-script or persistent-session asynchronous
TM guards, or the Rust sandbox's TCP-bind denial. It is a separate trusted-worker
path, not a new script permission. A provider-free native UE5 lifecycle check
passed with five acknowledged registrations over 20 owner frames, verified
owned-only cleanup, closed local TM and restored baseline. All backgrounds were
outside the 150-metre range in that first run; it did not exercise positive
in-range telemetry or missing-owned removal. A separate diagnostic reordered
actual native spawn transforms near the prepared route start, without changing
transforms, teleporting/adopting actors or expanding the range limit. Its fresh
local TM lifecycle passed with 99 same-owner-snapshot in-range observations over
20 frames and verified cleanup; the session's unwrapped native map reference was
restored before fixture cleanup. This is explicitly
arranged telemetry evidence, not the default spawn ordering or a missing-removal
check. Neither run is a completed route/hazard, provider, physical-motion or
repeatability guarantee. See the
[issue ledger](issue-resolution-status.md) for the explicit RPC/wall profile and
remaining acceptance limits.

## Vehicle roles and names

The vehicle under test has `role_name="hero"` by default in both merge and route
fixtures. Set `controlled_vehicle_role` to a nonempty label of at most 64
characters, without whitespace or control characters, to override it. Other
vehicles, walkers, sensors and recording cameras retain distinct, non-hero
labels. CARLA's recorder
[identifies hero vehicles by this role](https://carla.readthedocs.io/en/0.9.16/adv_recorder/#collisions);
a role is descriptive, not proof of ownership or permission to delete an actor.

New merge observations call the independently controlled other car `target`;
the tested car remains `policy`. `target_speed_mps` is the tested car's requested
speed, while `target_vehicle_speed_mps` is the other car's speed (default 5 m/s).
The legacy `ego_speed_mps` input alias is accepted; both names must contain valid,
equal values when supplied together. New specs, metadata and traces emit only
the canonical name.

Historical merge records are not rewritten. Readers map observation `ego` to
`target`, fixture `ego_start` to `target_start`, and the old destination-lane
anchor `target_start` to `target_lane_start`. Conflicting dual names are rejected.
A complete legacy spec without a recorded controlled role remains readable, but
cannot be matched to a new spec explicitly recording `hero`: readers do not
invent historical role provenance.

## UE5 fixture

CARLA 0.10.0 removes `vehicle.tesla.model3` and changes to Chaos vehicle physics.
Use **`town10-merge-ue5-v1`** with the matching simulator/client. It selects
`vehicle.lincoln.mkz`, retains the same legal 50 m corridor checks, and uses a
heading gain of 1.8 for the local steering controller. The original fixture keeps
its Tesla and heading gain of 0.9. The stronger UE5 heading correction prevents
the overshoot observed during initial live testing; Jev does not control that gain
or write steering commands.

The fixture metadata records its identity, exact vehicle, poses and controller
settings. The comparison tool refuses mismatched fixtures/settings. Missing
vehicles produce an explicit unsupported-fixture diagnostic instead of silently
substituting another vehicle or mixing results with the UE4 cohort.

Copy the [rules](examples/merge-rules-ue5-v1.json) or
[Jev](examples/merge-jev-ue5-v1.json) specification and set the reachable `host`
and `port`, then use the same lifecycle commands below:

```bash
uv run --no-sync carla-agentic-toolkit-experiment run \
  --spec docs/examples/merge-rules-ue5-v1.json
```

The [2026-10-05 validation](evidence/ue5-validation-2026-10-05/README.md) retains
initial failures, corrected runs, a bounded provider fallback and cleanup checks.
It covers one Town10 corridor on one host; it is not arbitrary-map, Traffic
Manager, production, or road-safety validation.

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

Every managed vehicle, sensor and demo camera has a version-1 spawn journal.
The owner saves an episode-bound plan before the native call, then the returned
actor ID before metadata reads, listener setup or other post-spawn work. A
separate completion write follows origin verification. Journal-write failures
invalidate execution; known returned handles remain available for conservative
cleanup in the current worker.

Fresh recovery may delete only durably recorded IDs in the original episode,
with the recorded type/role guard and authoritative deletion acknowledgement.
An ID recorded before an interrupted completion write can be recovered. A lost
reply with no durable ID cannot: matching role, blueprint, pose, or appearance
outside an earlier inventory does not prove who created an actor. Recovery does
not guess or adopt those actors. It can clean known IDs and restore same-episode
settings, but unresolved outcomes retain the dirty lease until reviewed manual
resolution. Trusted compatibility registration with `own()` does not resolve an
unrelated unknown spawn intent.

This deliberately differs from uncertain-spawn adoption proposed in
[#122](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/122): without an
authoritative creation ID, adopting a resembling actor could delete another
client's actor. Historical managed dirty markers without version-1 intent
coverage also remain quarantined after best-effort known-ID cleanup. Their
original marker bytes are preserved, not upgraded to manufacture coverage. Do
not erase them to bypass recovery. Live role and interrupted-spawn acceptance
for this revision remains pending.

Same-episode recovery verifies a fresh actor snapshot before looking up or
destroying recorded actors. It requests one tick only in synchronous mode; in asynchronous
mode it waits up to five seconds for a frame. The delivered frame must be newer
than the previous snapshot, and the published snapshot must have reached that
frame or a later one. This covers interruption before synchronous settings were
applied or after asynchronous settings were restored but before the clean marker
was saved. A timeout, stale publication, or episode change retains quarantine
without subsequent actor deletion or settings restoration. Normal experiment
steps still require exactly one frame; this recovery freshness check does not
relax runtime frame ownership.

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

### Replaced-world cleanup

If another client replaces the world before managed cleanup or dead-worker
recovery begins, the toolkit reads the replacement's current settings without
mutating it. It compares all six journaled fields: `synchronous_mode`,
`fixed_delta_seconds`, `no_rendering_mode`, `substepping`, `max_substeps`, and
`max_substep_delta_time`. Values and native types must match; integer 0 or 1
cannot substitute for a boolean. The replacement episode must remain unchanged
across this check.

A stable full match reports `world_replaced: true`, `settings_checked: true`,
and `settings_restored: true`. Here, restoration means the original baseline was
verified without a settings write. The lease can become clean only if the other
cleanup checks also succeed. A mismatch, unreadable settings, or another episode
change records a failure and leaves the lease dirty. A settings-read failure
reports `world_replaced: true`, `settings_checked: false`, and
`settings_restored: false`.

This deliberately differs from the restore-on-mismatch suggestion in
[#139](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/139): old-episode
ownership does not authorize writing settings, ticking, or deleting actors in an
unknown replacement. The toolkit refuses those mutations rather than overwriting
another client's world. This preserves the same-episode cleanup boundary in
[#127](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/127), including
replacement during actor destruction, which remains a failed cleanup.

Managed settings writes recheck the expected episode after fetching settings and
immediately before applying them. A replacement during that lookup refuses the
write and retains the dirty journal. A generic cleanup exception reports
`world_replaced: null`, `world_identity_checked: false`, and
`settings_restored: false`; it does not claim an unchanged episode or make another
native query merely to fill in the diagnostic.

For the required asynchronous starting baseline, this policy meets the live
acceptance outcome: either the asynchronous baseline is verified or the lease
stays dirty. Live CARLA 0.9.16 acceptance with a second client calling
`reload_world(False)` remains pending. Such a reload can retain the managed
synchronous settings; see CARLA's
[settings-preserving reload example](https://github.com/carla-simulator/carla/blob/0.9.16/Docs/adv_synchrony_timestep.md#physics-determinism).

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

Jev is the optional **System One decision model** in this loop. TypeSafe uses
[System One](https://typesafe.ai/blog/introducing-system-one-models-and-jev) for
models that return typed decisions and probabilities inside software workflows.
Here, the integration uses the official **Choice** primitive: reviewed state and
candidate descriptions go in; one candidate ID and its choice distribution come
back. The host coding agent can start an experiment through MCP, while the
trusted worker owns every recurring model call and simulator step.

| Boundary | What happens here |
| --- | --- |
| Input to Jev | Bounded, range-filtered numerical simulator state, current phase, recent history and only the locally feasible candidate IDs. Camera images are not supplied to this policy. |
| Jev output | One of the supplied choices. Before commitment this can be `defer` or `merge`; committed phases use applicable `continue` or `abort` choices. |
| Local acceptance | Validate returned model, candidate identity and probabilities, then recheck request freshness, phase and applicability before execution. |
| Control and recovery | Local code computes trajectories and controls, applies a phase-appropriate fallback, enforces budgets, and verifies cleanup. |

This implementation records choice probabilities for inspection, with no
probability/confidence cutoff controlling actuation. Valid output types alone do
not establish a correct driving decision. It uses one Choice question per
request; it does not implement a chain of model reasoning or combine multiple
System One questions into a larger probabilistic workflow. The shared rules
baseline isolates the policy selection for comparison. Follow the
[recorded decision through execution](jev-integration-follow-up.md#one-decision-followed-through-execution)
to see an actual Jev response, its acceptance and the controller's first action.

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

Merge observations keep actor-origin `position_m`, `longitudinal_m`, `lateral_m`
and `yaw_error_degrees` for trajectory and local control. A separate `box`
records `geometry` (world `center_m`, composed `yaw_degrees`, projected
`length_m`/`width_m`) and its `longitudinal_m`/`longitudinal_radius_m` along the
verified corridor. Bumper-gap/TTC evidence uses that box projection, not the
control origin. The planar geometry conservatively encloses all eight corners
after full actor and native box roll/pitch/yaw rotations, including projected
vertical extent. It is not an exact body-distance or collision test.

`actor_bounding_boxes` fixture metadata records every vehicle's native local
location, rotation and half-extents, allowing the actor-relative offset to be
audited. Native missing or invalid bounds fail explicitly. CARLA defines those
bounds [relative to the actor](https://carla.readthedocs.io/en/0.9.16/python_api/#carla.Actor).
Historical trace/media bytes stay unchanged: legacy observations without `box`
retain their old origin-based gap approximation, not a fabricated corrected
measurement.

Matching-client Windows-native measurements on dedicated servers on 2026-10-09
recorded the following fixture bounds. Each owned probe actor was authoritatively
deleted and the exact settings baseline verified afterward. Both local box
rotations were zero.

| Simulator / fixture vehicle | Native local box center (x, y, z), metres | Half-extents (x, y, z), metres | Horizontal local center offset |
| --- | --- | --- | --- |
| CARLA 0.9.16 / `vehicle.tesla.model3` | (0.029218862, -0.000000619, 0.735860407) | (2.395889759, 1.081725001, 0.743830025) | 0.029218862 m |
| CARLA 0.10.0 / `vehicle.lincoln.mkz` | (-0.006215515, approximately 0, 0.763245225) | (2.445985079, 0.917823017, 0.762059450) | 0.006215515 m |

These measure the omitted local center offsets, not a guaranteed clearance error
or a closed-loop safety outcome. Rotation can project vertical offsets into the
horizontal plane. The probes are Windows-native evidence, not Linux sandbox
acceptance; fake-corner regressions cover the conservative tilted-box projection.

The pure helper was cross-checked against those saved native world corners and
`Transform.transform` centers with a 0.00005 m comparison tolerance for CARLA's
float32 output. Three synthetic full-tilt configurations evaluated locally by
LibCarla also agreed within that tolerance; they are native-library checks, not
live tilted-vehicle motion or closed-loop acceptance.

One session owns every scheduled tick. The fixture/controller and provider never
tick. Each controlled vehicle has one assigned tracker; protected actors cannot be
adopted or reset by background traffic. World replacement or unexpected advancement
invalidates the run rather than silently accepting stale observations.
Invalidating a run does not establish a clean lease; see
[replaced-world cleanup](#replaced-world-cleanup) for the separate settings check.

- `simulation_time` pauses at decision boundaries while awaiting bounded inference.
- `paced` continues scheduled owner steps while inference is pending and records
  current fallback controls. It targets the configured wall pacing but makes no
  hard real-time promise. Reports include achieved real-time factor and frame age.

Mode setup may advance CARLA frames inside `apply_settings`; setup is distinct from
the scheduled experiment budget. Scripts that manage their own sensors should follow
the [explicit sensor timing contract](sensor-timing.md).

### Repetition setup

Before preflight, the session refuses any retained actor ownership or creation
intents; their original-episode evidence stays intact and quarantined. Each managed
repetition then verifies a dedicated asynchronous world and journals its original
six settings. It records a `reload.phase` of `prepared`, applies the
requested synchronous/fixed/substep settings, then records `pending` immediately
before one native `client.reload_world(False)` call. `False` keeps those settings;
the same map is reloaded with a new episode, following CARLA's
[synchronous-before-reload guidance](https://github.com/carla-simulator/carla/blob/0.9.16/Docs/adv_synchrony_timestep.md#physics-determinism).
The native returned world ID is durably recorded as `acknowledged` before any map
query or setup RPC, with the empty creation journal bound to this new episode.
The verified map name is then journaled. Runtime guards, actor cleanup and recovery
use that acknowledged ID; the pre-setup settings remain the restoration target.

The setup-frame barrier runs again in the new world. The session then calls
`reset_all_traffic_lights()` and publishes exactly one separately accounted setup
frame before reading native states. The `setup_frames` event records
`traffic_light_reset_frame` alongside the quiet barrier's `settled_frame`.
Fixture metadata records `initial_traffic_lights`: its publication frame and each
light's actor ID, OpenDRIVE ID, pole index, world location and actual state. Native
[traffic-light getters use the last delivered tick](https://carla.readthedocs.io/en/0.9.16/python_api/#carla.TrafficLight),
so reset acknowledgement alone is not state evidence. Missing capabilities,
unavailable states, stale publication or episode/frame drift refuse runtime setup.
This metadata is not added to selector inputs.

Native settings/reload internals may also advance setup frames. None of these are
scheduled experiment steps, and no frame-zero or bitwise-repeatability guarantee
is made. The existing client deadline applies to internal reload RPCs, not to the
aggregate native call: CARLA can make several RPCs and publication waits inside
[LoadEpisode](https://github.com/carla-simulator/carla/blob/0.10.0/LibCarla/source/carla/client/detail/Simulator.cpp#L87-L112).
The separate supervisor wall deadline still bounds the worker. Two provider-free
consecutive `lead_brake` route repetitions passed on dedicated CARLA 0.10.0 with
an explicit `rpc_timeout_seconds: 10.0` and a separate 180-second external wall
deadline. Both recorded the same initial native light states and verified cleanup
in their acknowledged replacement episodes. This is setup/lifecycle evidence,
not a completed hazard, provider or all-scenario validation. Historical trace and
media bytes are unchanged.

For new native UE5 specifications on that tested host, the verified RPC profile is:

```json
{
  "fixture": "town10-route-ue5-v1",
  "scenario": "lead_brake",
  "rpc_timeout_seconds": 10.0
}
```

Configure the dedicated endpoint separately. The global default remains five
seconds and the supported maximum remains ten; this explicit profile does not
change script defaults, saved specifications or provider deadlines. Five-second
native attempts on the tested server failed before reload acknowledgement and
retained their pending journals. The ten-second result does not identify the
precise native failure phase or promise that every server will reload in that
time. Never retry a reload against a dirty lease; the failed evidence required
verified operator decommission/replacement before the new successful attempt.
See the [issue ledger](issue-resolution-status.md) for acceptance limits.

Recovery distinguishes these phases. `prepared` means no reload call was attempted,
so a still-matching original episode can be restored and verified. An acknowledged
returned ID allows same-episode restoration even if a later map read failed.
A lost reply, invalid returned identity or failed returned-ID journal write keeps
unresolved reload evidence and a dirty lease. The toolkit never guesses the new
episode from its map or settings, retries reload automatically, or writes old
settings into an unknown replacement. An unresolved or invalid reload blocks
cleanup frame waits, ticks, actor destruction and settings writes even if the
client still reports the old episode: publication can follow a timed-out call.
A read-only baseline match under
[replaced-world cleanup](#replaced-world-cleanup) cannot clear an unresolved reload.
Subscription close callbacks remain separate and cannot resolve that uncertainty.

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

The two example specs differ only in policy. Keep fixture, initial poses,
replicate index, controller/planner versions, observation/timing mode, and
constraints matched.
Retain each run's exact spec, code hash, environment, model/question metadata, and
summary/report. Repeat a declared replicate set and report sample counts,
completion and collision definitions, invalid runs, interventions/fallbacks, decision latency,
staleness, and achieved real-time factor. Repeated runs do not guarantee bitwise
simulator determinism. Attribute controller interventions to the controller.

`replicate_index` is a label only: it changes no initial pose, speed, random
generator, Traffic Manager, or pedestrian state. It defaults to 7 and accepts
integers from 0 through 2**31-1. Legacy `seed` input is accepted as an alias;
providing both names requires identical valid integer values. New specs, traces,
fixture metadata, and demo projections use `replicate_index`. Historical evidence
keeps its original `seed` labels and bytes. Comparison accepts either name and
warns, without withholding otherwise matched metrics, when distinct replicate
indices have identical recorded initial conditions. That warning does not claim
identical physics, outcomes, or provider responses.

Generate the [matched static comparison](experiment-evidence.md#matched-static-comparison)
from every saved trial. It checks exact fixture/specification/version matching and
retains invalid and partial runs in the sample counts; mismatches refuse comparative
metrics rather than producing a policy ranking.

For a demo, show the run ID and live CARLA view, then the terminal cleanup state and
saved report. Also show a bounded stop or documented provider fallback. A video is
supporting material; retain the machine-readable evidence and do not claim improved
realism or safety without a completed matched evaluation.

The opt-in `scripts/capture_experiment_demo.py` and `scripts/capture_route_demo.py`
use the shared `SensorSubscription` with a bounded drop-oldest queue. Capture
receipts retain queue-drop counts and Stop/save failures; any drops make capture
verification fail. Existing arrivals are saved before Stop, and a successful
listener cutoff also preserves bounded trailing deliveries. PNG writing remains
on the owner thread; this is not a background encoding pipeline.

New `camera-manifest.json` receipts name `sha256_representation` as
`carla.Image.raw_data (32-bit BGRA)`: each image's SHA-256 is computed directly
from those in-memory pixels before the native PNG writer runs. It is not a digest
of the encoded PNG file. The demo renderers verify losslessly decoded BGRA pixels
using their existing Pillow dependency and refuse an unavailable decoder or an
unknown explicit representation. Historical receipts without this field retain
their original PNG-byte verification. Recorded receipts and media under
`docs/evidence/` are not rewritten or reinterpreted.
