# Script workflows

Use `execute_carla_script` to combine inspection, scene changes, vehicle control,
sensors and evidence in one finite Python workflow. Connect a matching CARLA
server and Python client using the [client setup guide](client-setup.md) first.

## Write a script and return a result

Scripts receive a curated `api` object. Assign a JSON-compatible value to
`result` to return it to the MCP client. For example, on a connected simulator:

```python
health = api.health_check()
result = {"health": health}
if health.get("connected") is True and not health.get("warnings"):
    result["world"] = api.get_world_state()
```

An incompatible or unverified release returns version-only health with no world
inspection. Keep that diagnostic result and fix client/server compatibility
before requesting world state or mutations; see [version matching](client-setup.md#version-matching).

Call `api.describe_api()` for the live method catalog, or
`api.list_capabilities()` for CARLA version and feature probes. A method being
present does not prove that every map or simulator build implements it correctly.
Map-layer streaming and OpenDRIVE generation change the world substantially;
use a dedicated simulator and verify health afterward.

The default tool starts a new script process for each call. Ordinary Python
variables do not survive the call. See [persistent script sessions](persistent-sessions.md)
when a workflow needs a retained namespace or asynchronous request handling.

## Check errors and snapshots

Recoverable CARLA operation failures return a value containing `ok: false`,
`error_type` and `error`. Check each operation's value before relying on its
fields or continuing a dependent operation. A handled operation failure does
not by itself fail the complete script.

Script rejection, uncaught exceptions, runner failures and timeouts fail the
complete MCP call with `isError: true`; structured diagnostics remain available.
Inspect cleanup results as well as the original error before continuing.

The tool result also contains inline `snapshots` keyed by `carla-snapshot://...`.
These are records from that script run, not live MCP Resources. The v0.1 field
name is `snapshots`; the former `resources` name has no compatibility alias.
Files saved under `CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR` can outlive the call.

## Return camera images

In an asynchronous world, attach a camera and use its returned sensor ID to
capture a frame. Set `publish=True` to include the saved image in the MCP result:

```python
capture = api.capture_sensor_frame(sensor_id, "captures/front.png", publish=True)
result = {"capture": capture}
```

Camera creation and new reads require `no_rendering_mode=False`; disabled or
unknown rendering is refused before spawning or listening. CPU sensors and
listener cleanup remain available. Off-screen rendering does not imply
no-rendering mode. See [rendering requirements](sensor-timing.md#rendering-requirements).

`save_screenshot` keeps the first delivered frame of its temporary spectator RGB
camera. It does not discard warmup frames or force manual exposure; supplied
blueprint attributes take precedence. The dedicated bright/dark
[first-frame check](first-frame-exposure-check.md) did not reproduce a material
brightness difference on the tested CARLA 0.9.16 setup. This is not a guarantee
that exposure has settled in other scenes or builds.

Published PNG/JPEG captures include native `ImageContent` for vision-capable
clients and a `carla-output://capture/...` Resource link for later reads. JSON
text remains first for clients that ignore images. The linked file must remain
in the configured output directory to be read later.

Publication permits at most four images, each no larger than 512 KiB, within a
1 MiB combined encoded result. Encoding and JSON also consume that combined
budget, so two images below the individual limit can still exceed it. Every
published path must resolve below `CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR`.

`api.save_screenshot("captures/view.png", publish=True)` defaults to a 640x360
RGB camera. This smaller default is not a size guarantee: scene content,
encoding, result text, and the number of images still affect both byte limits.
A publication-only failure retains the execution's original `ok`, `result`,
snapshots, and cleanup evidence, and adds
`publication_error={"error_type": ..., "error": ...}`. The image content is
omitted; this does not mean the script or its simulator mutations were rolled
back. Files remain available locally; MCP resource reads still enforce the same
publication limits.

## Preserve raw sensor evidence

With `output_dir` set, `read_sensor_stream` and `drain_sensor` save camera images
as `sensor-<id>-<frame>.png` and both LiDAR measurement types as
`sensor-<id>-<frame>.ply`. GNSS, IMU, radar, collision, and other measurements
without a callable native `save_to_disk` retain their numerical digests but
return no saved paths. A native writer that does not produce the requested file
is reported as an error, not a successful capture. Point clouds are durable
local files, not MCP image content.

Single `capture_sensor_frame` calls require `.ply` for LiDAR and a lossless
`.png` for encoded depth, semantic segmentation, and instance segmentation.
Lossy JPEG output would corrupt their encoded channels and is refused before
listening. RGB cameras can use PNG or JPEG; `mime_type` is detected from file
bytes rather than inferred from the extension. Numerical sensors without a
writer use stream or drain digests instead of single-file capture.

For a displayable depth or segmentation image, request an optional native
converter only with `publish=True`:

```python
capture = api.capture_sensor_frame(
    depth_sensor_id,
    "captures/depth-raw.png",
    publish=True,
    color_converter="LogarithmicDepth",
)
result = {"capture": capture}
```

Accepted names are `Raw`, `Depth`, `LogarithmicDepth`, and `CityScapesPalette`.
The raw lossless PNG remains at `capture["path"]` as ground truth; the converter
writes a separate `<raw-stem>-display.png` at `capture["publication_path"]`,
which is selected for MCP publication and its resource link. The converter does
not call in-place `image.convert` or overwrite the raw file. Use
`CityScapesPalette` for semantic segmentation. See CARLA's
[sensor encoding reference](https://carla.readthedocs.io/en/0.9.16/ref_sensors/)
and [native image writer](https://github.com/carla-simulator/carla/blob/0.9.16/PythonAPI/carla/source/libcarla/SensorData.cpp).

Synchronous worlds require the explicit sequence `subscribe_sensor` → owner
`tick` → `drain_sensor` → `close_sensor_subscription`. Bounded queues report
delayed and dropped frames; empty collision-event drains do not wait. The
`capture_sensor_frame` and `read_sensor_stream` convenience calls reject
synchronous worlds. Follow [sensor timing and advancement](sensor-timing.md)
for frame ownership, delivery and listener cleanup.

## Read frame-coherent vehicle telemetry

`api.get_vehicle_telemetry(actor_id)` reads one native
[world snapshot](https://carla.readthedocs.io/en/0.9.16/python_api/#carla.WorldSnapshot).
Its `frame` and `elapsed_seconds` identify the simulation frame and seconds since
the current episode began. `transform`, `velocity`, and `acceleration` come from
that frame's `ActorSnapshot`; `speed_mps` is the magnitude of the same velocity
returned in `velocity`. This applies in both synchronous and asynchronous worlds.

`control`, `speed_limit`, and `traffic_light_state` are separate actor reads:
`ActorSnapshot` does not contain them, so they are not guaranteed to describe the
reported frame. Their existing unavailable-value behavior is unchanged.

If the actor is absent from the selected snapshot, including immediately after
a spawn before frame publication, the call returns `ok=False` with
`error_type="get_vehicle_telemetry_failed"`. It never substitutes newer actor
getters, waits for a frame, or ticks implicitly. Check the error before using the
telemetry; advance the synchronous clock explicitly or observe an asynchronous
frame before retrying.

## Follow waypoint links

`api.follow_waypoints(start, end, step_meters=2.0, max_steps=200)` is a greedy
follower, not a topology-searching route planner. At each junction it selects
the successor nearest the destination in straight-line distance. A nearer
branch can lead away from the destination even when another branch reaches it.
The query does not tick, move an actor, or guarantee a drivable complete route.

The result includes the waypoint list, `reached_destination`, and
`remaining_distance_m`: the Euclidean distance from the final waypoint to the
requested end, including elevation. Arrival means that distance is at most
`step_meters`; it is not exact endpoint equality. A road end or step budget can
leave `reached_destination=False`. Check it before treating the path as complete.
An origin already within the arrival tolerance is not followed away from the end.

`generate_route` remains a deprecated compatibility alias with the same result
and `carla-snapshot://route/latest` snapshot. New scripts should use
`follow_waypoints`. Historical discovery receipts remain unchanged.

## Name actors across calls

After creating an actor and obtaining its ID, give it a conversational name:

```python
api.name_actor("ego", actor_id)
```

In a later call, resolve the name before using the actor:

```python
ego_id = api.resolve_actor("ego")["actor_id"]
```

Check these operations for recoverable errors before using their results.
Aliases live under `CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR`, are isolated by CARLA
host and port, and survive MCP server restarts that reuse that directory and
simulator episode. Each alias stores `actor_id`, `world_id`, `type_id`, and
`role_name`; an absent role is recorded as `null`, not guessed from the alias.

Both naming and resolution use the explicit-ID server actor lookup, not the
cached snapshot inventory. An actor can be named immediately after spawning,
without waiting for frame publication or sending a tick. Resolution checks the
stored episode, type, and role against the live server description and verifies
that the episode remained stable during the lookup. Confirmed absence or any
identity mismatch invalidates only that alias with a structured error. An
unavailable lookup or episode change during the check returns an error without
rebinding or erasing the prior record. No role-name search fallback is performed.

Legacy integer-only registry files and malformed identities are explicitly
rejected, not migrated by guessing an episode. Retire an old alias file and
recreate its names against known current actors rather than reusing its IDs.
`api.list_named_actors()` lists stored names without checking their liveness;
resolve each name before using its ID. Use `api.forget_actor()` to remove a name
without destroying the actor.
Names are a convenience and do not grant ownership or authorization.

## Own the lifecycle and cleanup

Successful finite scripts retain their created actors. Call
`api.cleanup_owned_actors()` to destroy actors created by the current execution,
or use explicit destroy methods when the scene no longer needs them. Actors
retained from an earlier call require explicit cleanup using their known IDs;
assigning an alias does not add them to the current execution's ownership journal.

Before a native spawn, the toolkit durably records a pending creation intent for
the originating simulator episode. Each returned actor ID is journaled
immediately, before validation or later setup. This includes each actor in a
spawn batch, each traffic vehicle, and each walker before its controller.
`api.save_screenshot()` journals its temporary camera before capture and removes
it from the journal only after authoritative destruction or confirmed absence.
The intent is resolved only after fresh same-episode verification and a durable
completion write. A definitive no-actor spawn response uses the same checks.

Traffic population and `spawn_actor_batch` intentionally spawn one actor at a
time. Each returned ID reaches the durable journal before autopilot or later
setup. CARLA's native `SpawnActor.then(SetAutopilot)` batch returns IDs only after
the batch request; a terminated worker cannot journal them before its chained
controls run. This retains the journaling-first alternative in
[#149](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/149) rather than
trading [#136](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/136)'s
ownership guarantee for fewer round trips. No native spawn/autopilot batch is
exposed; deterministic blueprint ordering and per-vehicle failures are unchanged.
Traffic controllers live in the script or persistent-session child, not the MCP
server or the trusted Traffic Manager sidecar. The live 30-vehicle ownership and
autopilot check remains pending.

A creation-journal, episode-verification, or completion-write failure remains
fatal for that execution even if script code ignores the API error. Later
creation is blocked. The toolkit attempts a non-ticking, authoritative rollback
of the known ID. Verified rollback can resolve that actor and its creation intent,
but the execution still fails. Unverified rollback retains the actor ID and error
in cleanup failures; a failed completion write also leaves the intent unresolved.
Both require recovery without releasing the dirty simulator lease.
Do not delete recovery markers or change the state directory or endpoint to bypass
that barrier; only verified recovery can establish cleanup.

If a worker is killed or a spawn reply is lost before a conclusive outcome is
recorded, the pending intent keeps the lease dirty even when the actor-ID list is
empty. Recovery can attempt cleanup of durably known IDs from the same episode
and restore journaled settings, but cannot establish a clean run while that
intent is unresolved. That uncertainty requires operator investigation. Unknown
IDs are not reconstructed: the toolkit cannot infer whether the server created
such an actor or claim that it was recovered.

The toolkit journals all six original world-settings fields before the first
settings change, then restores and verifies them when a finite script finishes.
Successful scripts retain their actors while their world settings are restored.
Uncaught exceptions and timeouts also trigger bounded, best-effort CARLA-side
cleanup of journaled actors without touching pre-existing actors. The separate
cleanup process can restore settings even when a killed script cannot run
`finally`. Inspect `cleanup.settings_restored` and `cleanup.failures`; a restoration
that cannot be verified leaves the simulator lease dirty and requires recovery.
Failed or unreachable cleanup remains visible; a failed script is not proof that
its actors are gone.

One MCP server serializes complete finite script executions against shared
simulator state. `timeout_seconds` starts after queueing and covers sandbox
execution; time spent waiting for the server's execution lock is outside it.
Allow for queueing when configuring the MCP client's tool timeout.

Cooperating scripts, persistent sessions and managed workers coordinate through
a simulator lease. Configure the same private state directory and unambiguous
endpoint for every local process. This coordination cannot stop unrelated CARLA
clients from changing the world. The [security policy](../SECURITY.md) explains
the process boundary and supported deployment assumptions.

## Bound RPC timeouts and map changes

CARLA's per-RPC timeout is separate from `timeout_seconds`. Ordinary calls use a
10-second cap, narrowed to the remaining finite execution budget. Native creation
refreshes that limit before each spawn; a long batch does not grant each actor a
new script budget.

The world and batch defaults are explicit below, alongside the
[CARLA 0.9.16 Python binding](https://github.com/carla-simulator/carla/blob/0.9.16/PythonAPI/carla/source/libcarla/Client.cpp).

| Script operation | Toolkit choice | CARLA 0.9.16 default |
| --- | --- | --- |
| `api.load_world(map_name, reset_settings=True)` | Optional keyword; `True` resets settings, `False` preserves them. | `Client.load_world`: `reset_settings=True` |
| `api.reload_world(reset_settings=False)` | Required keyword with no default; choose `False` to keep settings or `True` to reset them. | `Client.reload_world`: `reset_settings=True` |
| `api.generate_opendrive_world(opendrive, reset_settings=True)` | Optional keyword; unchanged default of `True`. | `Client.generate_opendrive_world`: `reset_settings=True` |
| `api.apply_batch(commands, do_tick=False)` | No tick by default; only explicit `do_tick=True` requests a tick. | `Client.apply_batch_sync`: `do_tick=False` |

Successful world replacements and batch results include top-level
`synchronous_mode` and `synchronous_mode_changed`. The former is the observed
post-call mode; the latter compares the observed pre-call and post-call modes,
not the requested flags. An unchanged mode does not mean every world setting
was preserved. Inspect these fields before choosing the next timing operation.
When the caller owns a synchronous clock, request a batch tick explicitly if
needed; ordinary cleanup batches do not implicitly advance it.

Loading, reloading, and generating a world temporarily raise the map-mutation
RPC cap to 120 seconds, still bounded by the remaining execution budget. The
ordinary limit is restored afterward; this does not extend the sandbox deadline.

A native map-operation failure returns `retryable: false`, a `hint`, and
`observed_world` with `world_id`, `map_name`, and `error`. The toolkit makes one
best-effort read-only observation within a combined two-second window, also
bounded by the remaining budget. Unavailable diagnostics retain null fields and
an error. A lost reply does not trigger another map mutation, automatic ownership
rebinding, or journal clearing: inspect the observation and recover the execution
before starting another mutation.

Reload closes this execution's sensor subscriptions before making the native
request. If it fails, cached sensor handles and the ownership journal remain
intact; the failure is returned as `reload_world_failed`, not a raw CARLA
exception. When the original episode is still current, later owned-actor cleanup
can destroy those sensors without restarting their listeners. An uncertain or
changed episode is not authorization to reuse old handles in the replacement
world, and does not make a failed run's lease clean.

Live CARLA 0.9.16 acceptance checks for interrupted traffic creation and large
persistent-session map loads remain pending; offline regressions do not establish
those simulator-specific outcomes.

## Keep traffic and simulation timing explicit

The `setup_synchronous_stepping` prompt uses a fixed timestep and restores the
previous settings in `finally`. The same pattern gives a script restoration
evidence before it returns:

```python
configured = api.set_sync_mode(enabled=True, fixed_delta_seconds=0.05)
if configured.get("ok") is False:
    raise RuntimeError(configured["error"])
previous_settings = configured["previous_settings"]
try:
    before = api.get_world_state()
    advanced = api.tick_n(count=5)
    if advanced.get("ok") is False:
        raise RuntimeError(advanced["error"])
    after = api.get_world_state()
    result = {"before": before, "after": after}
finally:
    restored = api.restore_world_settings(previous_settings)
    if restored.get("ok") is False:
        raise RuntimeError(restored["error"])
result["restoration"] = restored
```

Successful `health_check` and `get_world_state` reports, including their inline
snapshots, expose `synchronous_mode`, `fixed_delta_seconds`, `no_rendering_mode`,
`substepping`, `max_substeps`, and `max_substep_delta_time`. Substepping fields
unavailable in a legacy client are `null`, not assumed CARLA defaults. Unverified
version-only health still has `settings=null` and does not inspect the world.

Enabling synchronous mode requires a positive fixed timestep of at most 0.1
seconds that fits the world's substep budget when substepping is enabled.
`api.set_sync_mode(enabled=False)` defaults to a variable timestep. Synchronous
stepping does not by itself guarantee reproducible simulation results.
Both `api.set_sync_mode(enabled=True)` and restoration of
`synchronous_mode=True` through `api.restore_world_settings()` are refused while
the background traffic controller is active or stopping. Call
`api.stop_traffic_controller()` and confirm both `active` and `stopping` are false
before changing to synchronous settings.

`api.tick()` and `api.tick_n()` require synchronous mode. In asynchronous mode
they return a structured error without sending a tick cue. `api.watch_actor()`
requires asynchronous mode and rejects a synchronous world before changing the
spectator.

`api.wait(seconds)` is also asynchronous-only. It clamps the requested wall-clock
duration to 0-60 seconds and observes native frames instead of sleeping or
ticking. Each frame wait is capped at one second, the remaining requested
duration, and the remaining RPC budget. Finite execution, request, and absolute
session deadlines are not reset. Incompatible modes return structured errors;
use explicit ticks when the caller owns a synchronous clock.

For stepping across calls, use a [persistent session](persistent-sessions.md).
It retains the settings and owns ticking until close, when the toolkit restores
and verifies the original settings. A finite call restores its settings before
releasing ownership, even when the script leaves synchronous mode enabled.

Traffic Manager global setter attempts are journaled per port in
`attempted_settings` before the native call. Cleanup restores only the attempted
fields to these declared targets:

| Global setting | Cleanup target |
| --- | --- |
| `global_distance_to_leading_vehicle` | 2.0 meters |
| `global_percentage_speed_difference` | 0.0 |
| `seed` | 0 |
| `synchronous_mode` | `False` |

These are cleanup policy, not captured original values. The
[CARLA 0.9.16 binding](https://github.com/carla-simulator/carla/blob/0.9.16/PythonAPI/carla/source/libcarla/TrafficManager.cpp)
provides no getters for these global settings, so successful setter calls are not
readback verification and cannot restore unknown external configuration. A cleanup
setter failure keeps the lease dirty even when actor cleanup succeeds. Cleanup's
`traffic_manager_restore_targets` evidence lists each port and its attempted-field
targets; `traffic_manager_async_ports` includes only ports whose recorded fields
include synchronous mode. Legacy mode-only journal entries restore only their
recorded synchronous-mode field; they do not authorize resetting unrecorded
globals. Setting any seed, including cleanup's seed 0,
[resets all traffic lights](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/trafficmanager/TrafficManagerLocal.cpp#L449-L453).

Traffic controller state lasts for one script process. Keep that process alive
with asynchronous frame waits through `api.wait()` within its execution budget,
or use the documented sidecar. Autopilot and traffic tuning require a dedicated
toolkit-owned Traffic Manager server in a trusted client outside the sandbox;
follow [Traffic Manager setup](client-setup.md#traffic-manager).

Population, autopilot, global configuration, per-vehicle tuning, path changes,
behavior profiles, and background density maintenance are asynchronous-only.
They reject synchronous worlds before Traffic Manager access or mutation.
`configure_traffic_manager` also rejects `synchronous_mode=True` in either world
mode with guidance to [#26](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/26).
Seeds do not provide asynchronous traffic reproducibility. Population/autopilot
requests still accept `advance_world=False`, but that does not bypass the
asynchronous-only guard. Batch operations default to `do_tick=False`; non-ticking
recorder replay remains unsupported. See [sensor timing](sensor-timing.md) before
combining operations with an explicit tick owner. Live CARLA 0.9.16 sidecar checks
of these guards and cleanup targets remain pending.

### Traffic Manager presets and speed units

`normal`, `cautious`, `aggressive`, `impatient`, and `stalled` are toolkit
Traffic Manager presets. They set TM speed-difference percentages, following
distance, lane changes, and ignore percentages; they are not CARLA's
[BehaviorAgent classes](https://github.com/carla-simulator/carla/blob/0.9.16/PythonAPI/carla/agents/navigation/behavior_types.py).

Use `api.tune_traffic_vehicle(vehicle_id, {"desired_speed_kmh": 36.0})` for a
36 km/h target. The deprecated `desired_speed` input alias uses the same km/h
units, not m/s. Successful results use `desired_speed_kmh`. Both aliases must be
finite nonnegative numbers; if both are present they must agree. Booleans and
conflicting values are rejected before Traffic Manager access. Other toolkit
fields explicitly ending in `_mps`, including observed vehicle speeds, remain
in meters per second.

CARLA 0.9.16's
[target-speed lookup](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/trafficmanager/Parameters.cpp#L235-L244)
returns this exact target, and its
[motion planner divides it by 3.6](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/trafficmanager/MotionPlanStage.cpp#L123-L130).
The target is not a guarantee of measured speed; hazards and motion planning may
reduce it.

Applying or reapplying a preset
[clears the vehicle's exact desired-speed target](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/trafficmanager/Parameters.cpp#L39-L45)
when it sets `vehicle_percentage_speed_difference`. The density controller
reapplies registered presets on every maintenance pass, so a subsequent pass
overrides an explicit desired speed. There is no persistent per-vehicle speed
override cache. Stop the controller and confirm both `active` and `stopping` are
false before tuning an exact target; do not reapply a preset while relying on
that target. Mock-backed tests cover the actual maintenance path twice. A
dedicated CARLA 0.9.16 native check applied `cautious` and a 36 km/h target, then
read snapshot speeds of 4.9036 and 6.2627 m/s after two maintenance passes. These
are transient observations, not exact-target or steady-state-speed guarantees;
CARLA exposes no getter for its internal desired-speed map. The probe disabled
autopilot, deleted its owned vehicle, verified the original simulator baseline,
and confirmed its process-owned TM port closed.

With `safe_filter=True`, vehicle selection keeps only the native
`base_type="car"` classification when that attribute exists. Present blank or
other values are excluded. Older blueprints without the attribute use the
previous four-wheel and excluded-name policy. `safe_filter=False` retains all
vehicle blueprints; both selections stay sorted by blueprint ID before the
existing seed-based population ordering.

### Density frame-wait failures

A single failed density-controller `wait_for_tick(1.0)` stops its worker. It is
not replaced by a sleep or an automatic reconnect/retry. Status exposes
`error_type="frame_wait_failed"`, the original native error in `last_error`, and
`frame_wait_phase`: `reset_before_destroy`, `reset_after_destroy`, or
`maintenance_end`. Inspect the failure before explicitly restarting maintenance.

The final wait follows population maintenance and count observation. Those
completed-pass observations and confirmed spawn/destroy callbacks remain
recorded even when the wait fails. Reset-phase failures instead expose
`reset_progress` with the episode, acknowledged deleted IDs, and whether the
destruction phase was attempted to completion. They preserve previous counts
and applied-revision evidence rather than inventing a successful density pass.
Only acknowledged deletions remove ownership. A completed destruction phase
consumes the captured one-shot reset, not a newer desired revision; it does not
mean every requested deletion succeeded.

No further controller maintenance RPCs run after that failed wait. Existing
trusted lifecycle callbacks still validate the originating episode before
releasing durable ownership; a failed journal acknowledgement remains a fatal
ownership error. This policy does not change the asynchronous-only mode guard
or acquire timing ownership. An ordinary failure while publishing lifecycle
evidence retains the fatal frame-wait failure and reports the secondary error;
it does not authorize another maintenance pass or an unverified journal release.

## Put outputs on the correct host

Relative capture and evidence paths such as `captures/front.png` resolve under
`CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR` on the toolkit host. Requested durable output
stays there; per-run scratch space is deleted after script execution.

CARLA recorder paths are opened by the simulator host. Set
`CARLA_AGENTIC_TOOLKIT_RECORDER_DIR` to an absolute directory understood by that
host. The toolkit reports the exact path CARLA accepted and does not copy the
recording between hosts. This distinction matters when CARLA runs on Windows
and the toolkit runs in WSL2; see the [Windows setup](client-setup.md#windows-11-with-wsl2).

`record_episode("episode.log", additional_data=False)` uses CARLA's default
recorder format. Set `additional_data=True` to include vehicle and pedestrian
linear/angular velocities, traffic-light timings, execution time, actor trigger
and bounding boxes, and vehicle physics controls. These are simulator recorder
fields, not raw camera or lidar captures. See the [CARLA recorder reference](https://carla.readthedocs.io/en/0.9.16/adv_recorder/).
Managed experiments still write toolkit numerical traces and do not start the
native recorder automatically.

`query_recording_collisions` accepts category letters for each participant:
`h` is a hero actor (`role_name=hero`), `v` a vehicle, `w` a walker, `t` a traffic
light, `o` another actor, and `a` any actor. For example, query the accepted
recording path with `actor_type="v", other_type="w"` for vehicle/walker
collisions; `"a", "a"` leaves the category filter off. Collision evidence depends
on an attached collision detector, so an empty report does not prove absence of
collisions.

`replay_recording` supports only its default `do_tick=True`. CARLA's native
`replay_file` does not expose a non-ticking replay mode; `do_tick=False` is refused
before replay starts. Use the simulator-accepted recording path when replaying
or querying a log, rather than assuming the log exists on the toolkit host.

## Choose another execution mode when needed

[Persistent script sessions](persistent-sessions.md) retain Python variables and
the curated API, with bounded requests, telemetry, cancellation and cleanup.
Enable them with `CARLA_AGENTIC_TOOLKIT_ENABLE_SCRIPT_SESSIONS=1` in the trusted
server environment to expose the optional `carla_script_session` tool.

[Managed experiments](managed-experiments.md) run reviewed numerical
specifications in a separate trusted worker. They require dedicated-instance
ownership, accept no arbitrary code or provider URLs, and retain private traces.
The no-key rules baseline and optional Jev policy share the local controller.
Set `CARLA_AGENTIC_TOOLKIT_MANAGED_EXPERIMENTS=1` to expose the optional
`managed_experiment` MCP lifecycle. A stop request is not proof of termination;
check `terminated` and `cleanup.ok` before starting another owner.
