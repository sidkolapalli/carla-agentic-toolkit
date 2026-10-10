# Bounded persistent script sessions

Set `CARLA_AGENTIC_TOOLKIT_ENABLE_SCRIPT_SESSIONS=1` before starting the Linux/WSL2
stdio server to expose the optional `carla_script_session` tool. The existing
finite `execute_carla_script` tool remains available. A session retains its curated
`api` object and ordinary Python variables across requests. This mode runs generated
code inside the Rust sandbox; managed experiment specifications use a separate
trusted worker.

Call `open` with a `config` object:

```json
{"host":"127.0.0.1","port":2000,"idle_timeout_seconds":60,"absolute_timeout_seconds":300,"request_timeout_seconds":30,"traffic_manager_ports":[8500]}
```

Omitted `host` and `port` fields use the trusted server environment's optional
`CARLA_AGENTIC_TOOLKIT_HOST` and `CARLA_AGENTIC_TOOLKIT_PORT`, otherwise
`127.0.0.1` and `2000`. Explicit values override each default independently;
explicit invalid values never fall back. A session freezes its selected endpoint
at open. See [default simulator endpoint](client-setup.md#default-simulator-endpoint)
for WSL forwarding and stable-address recovery requirements. Configuring a new
address or private state root does not resolve existing dirty evidence.

`traffic_manager_ports` is an optional array of at most 16 integer TCP ports in
1..65535. It adds connect permissions alongside RPC, the selected streaming and
secondary ports, and 8000. Omitting it or passing an empty array preserves that
policy. Booleans, strings,
floats, and out-of-range ports are rejected before a lease or worker is created.
Permissions are frozen at open and cannot be widened by a later script request.
Use the same explicit port in the API's `traffic_manager_port` arguments. This
does not derive a port from the RPC endpoint, select a different CARLA server,
or permit binding: the matching dedicated TM sidecar must already be running.

`streaming_port` and `secondary_port` are optional exact integer TCP ports in
1..65535. Missing or `null` values retain RPC+1 and RPC+2 respectively. An explicit
value replaces its adjacent default permission; it does not add both endpoints.
For a nonadjacent server layout, include these fields in the same open config:

```json
{"host":"127.0.0.1","port":3000,"streaming_port":3100,"secondary_port":3200}
```

They are validated before lease or worker setup, preserved in the worker config,
and frozen for the session. These permissions neither reconfigure CARLA nor allow
binding a TCP server. See [CARLA port layout](client-setup.md#carla-port-layout)
for native streaming-timeout diagnostic limits.

The returned unpredictable `session_id` belongs to this stdio server instance.
Another client cannot read, execute, or cancel it, even if it knows that ID. At most
32 sessions, including their terminal results, are retained per server instance.
Only one session or other cooperating mutator can own a configured simulator at
a time. Use the same private state directory and unambiguous endpoint configuration
for cooperating clients. This lock does not control unrelated external CARLA clients.

Pass the ID and `code` to `execute`. The call enqueues one request and returns
immediately; poll `read` for `pending: false` and `latest_result`. A second request
while one is outstanding returns `session_busy`. For example:

```python
# First execute: retain an owned vehicle and ordinary local state.
points = api.get_spawn_points()["spawn_points"]
spawned = api.spawn_actor_batch([{
    "blueprint_id": "vehicle.tesla.model3",
    "transform": points[0],
    "attributes": {"role_name": "session-demo"},
}])
vehicle = spawned["results"][0]["actor_id"]
steps = []
result = vehicle
```

On CARLA 0.10.0 / UE5, use `vehicle.lincoln.mkz` in place of the Tesla blueprint;
see [release compatibility](client-setup.md#carla-release-compatibility).

```python
# Follow-up execute in the same session.
steps.append("brake")
api.apply_vehicle_control(vehicle, throttle=0.0, brake=1.0)
result = {"steps": steps, "telemetry": api.get_vehicle_telemetry(vehicle)}
```

Vehicle telemetry includes `frame` and `elapsed_seconds` for its single-snapshot
motion values. Control, speed limit, and traffic-light state are separate actor
reads; check for an error if the actor has not reached the snapshot yet. See
[frame-coherent telemetry](script-workflows.md#read-frame-coherent-vehicle-telemetry).

Each request receives the same trusted API and safe builtins; the `result` variable
is reset before execution. Validation runs on every request. A script exception
is returned explicitly and may leave earlier variable assignments in place.

Native creation starts with a durable pending intent bound to its originating
episode. Returned actor IDs are journaled immediately, not after a complete batch
finishes. Each walker is recorded before its controller, and a screenshot's
temporary camera is recorded before capture and released only after confirmed
cleanup. Resolving an intent requires fresh same-episode verification and a
durable completion write, including for a definitive no-actor spawn response.
A creation-journal, episode-verification, or completion-write failure permanently
blocks later creation and makes request results fail even if code ignores the
API error. Verified rollback can resolve the known actor and its intent without
making execution successful; unverified rollback retains the actor ID and error
for recovery. A lost reply or interrupted completion leaves the lease dirty even
if no actor IDs were recorded. Recovery can attempt cleanup of durably known
same-episode IDs and restore journaled settings, but cannot declare cleanup
complete while a creation intent is unresolved. That uncertainty requires
operator investigation; unknown IDs are not guessed or automatically reconciled.
Do not delete recovery markers or use another state directory or endpoint to
bypass the barrier; that does not verify actor cleanup.

AI walkers additionally require a fresh creating-client frame before controller
Start. `spawn_walkers` performs one owner Tick (synchronous) or WaitForTick
(asynchronous) per pair, bounded by five seconds and the remaining RPC budget.
Continue frame waits on this same persistent client for navigation updates;
ticks from another client do not keep its AI navigation alive. If the client
dies, leftover walkers can continue their last straight-line server control,
not navmesh planning. Cleanup Stops known AI controllers before deletion;
failed Stop acknowledgements keep their owned walker population dirty. A supplied
seed initializes native navigation before sampling, but is not a full deterministic
replay guarantee or a getter-backed restoration target. See
[AI walker lifecycle](script-workflows.md#ai-walker-lifecycle).

Ordinary CARLA RPCs use a 10-second cap, bounded by the remaining request and
absolute session lifetime. Map-changing calls temporarily use a 120-second cap,
but the same request and absolute deadlines still apply; a request's timeout can
therefore be shorter than the map cap. A native map failure is non-retryable and
includes one best-effort read-only world-ID/map observation. It does not
automatically repeat the mutation, rebind ownership, or clear the journal. See
[RPC timeouts and map changes](script-workflows.md#bound-rpc-timeouts-and-map-changes)
for the diagnostic fields and recovery requirement.

World-settings changes also persist across requests in the same session. A
session that enables synchronous mode owns ticking for its lifetime; advance
frames explicitly with `api.tick()` or `api.tick_n()`. The toolkit journals the
original settings before their first change and restores them when the session
closes. Use `api.restore_world_settings(previous_settings)` from a successful
`api.set_sync_mode()` result to restore them earlier within the session.
Enabling synchronous mode through `api.set_sync_mode()` or restoring
`synchronous_mode=True` through `api.restore_world_settings()` is refused while
the traffic controller is active or stopping. Stop it and confirm both status
fields are false before switching modes.

Traffic Manager workflows require an asynchronous world and a
[dedicated toolkit-owned sidecar](client-setup.md#traffic-manager).
`configure_traffic_manager` rejects `synchronous_mode=True` in either world mode;
synchronous Traffic Manager support remains tracked in
[#26](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/26). Seeds do not
make asynchronous traffic reproducible. Attempted global settings persist across
requests and are journaled per port for cleanup on close. Only attempted fields
are reset to the declared targets: following distance 2.0 meters, percentage
speed difference 0.0, seed 0, and synchronous mode `False`. CARLA has no getters
for these globals, so this is not original-value restoration or readback
verification. Legacy mode-only entries restore only their recorded sync field.
Setting seed 0 also resets all traffic lights; see
[Traffic Manager cleanup policy](script-workflows.md#keep-traffic-and-simulation-timing-explicit).

One failed density frame wait stops the controller with `frame_wait_failed`,
`frame_wait_phase`, and any `reset_progress`; it does not silently sleep or retry.
Confirmed actor changes remain available for cleanup, while an incomplete reset
does not replace previous count or applied-revision evidence. See the
[density failure policy](script-workflows.md#density-frame-wait-failures) before
explicitly restarting it.

`api.tick()` and `api.tick_n()` are synchronous-only and reject asynchronous
worlds with a structured error before sending a tick cue. `api.watch_actor()` and
`api.wait()` are asynchronous-only; a synchronous watch is rejected before the
spectator changes. Waiting clamps the requested wall-clock duration to 0-60
seconds and observes native frames instead of sleeping or ticking. Each frame
wait is bounded by one second, the remaining requested duration, and the
remaining request and absolute-session RPC budgets; it does not reset deadlines.

Use action `telemetry` with `config: {"actor_id": 123, "interval_seconds": 0.2}`
to sample one session-owned actor using the trusted, non-ticking vehicle telemetry
method. Intervals must be 0.1–10 seconds. `actor_id: null` disables sampling.
`read` returns the latest sample only; old samples are replaced, never queued.
Sampling occurs between script requests, so a running script or slow RPC delays
it. This interface does not promise real-time control or sensor-frame alignment.

Call `close` when finished. It terminates the complete sandbox process group and
restores and verifies the journaled world settings, then destroys session-created
actors, including actors left alive by a successful script.
`cancel` also terminates an active or blocked request and reports cancellation.
Client disconnect triggers the same cleanup for every owned session. `active`
stays true until the worker is dead and cleanup finishes. Cleanup failure leaves
durable recovery evidence and prevents another mutation from acquiring the lease.
Inspect `cleanup.settings_restored` as well as `cleanup.failures`; unverified world
settings restoration keeps the lease dirty even when actor cleanup succeeds.
Traffic Manager cleanup setter failures also leave the lease dirty. Its
`traffic_manager_restore_targets` evidence lists per-port attempted-field targets;
`traffic_manager_async_ports` lists only ports with a recorded sync field.
Successful declared Traffic Manager restoration does not mean its global values
were observed or verified through getters.

If a native cleanup worker exits abnormally or does not publish a readable result,
`cleanup.worker` records its `exit_code` and `result_available`. Negative exit codes
identify Linux signals; exit 0 without a readable report still requires recovery.
Raw worker stderr and environment are not included. The worker saves the cleanup
report before exiting without native client finalization, preventing teardown
from discarding a completed result. See the [validated lifetime fix](evidence/cleanup-worker-2026-10-05/README.md).

After the previous worker has terminated, run trusted recovery from the same
Linux/WSL2 environment and private state directory:

```bash
carla-agentic-toolkit-recover-scripts --host 127.0.0.1 --port 2000 --timeout-seconds 10
```

Recovery acquires the same lease, validates the private ownership journal and
simulator episode, and runs native cleanup in a separate process with a hard
deadline. It clears the recovery barrier only after verified cleanup. Missing,
corrupt, legacy nonempty journals without episode identity, and ambiguous evidence
remain quarantined; the command never accepts an arbitrary journal path. Actor IDs
from an old simulator episode cannot authorize destruction in a replacement world.

Idle and absolute timeouts are positive and at most 3600 seconds; per-request
timeouts are at most 60 seconds. Idle time starts after readiness and is refreshed
by client activity, including reads. Absolute lifetime starts at launch and cannot
be extended. Rust independently enforces the absolute deadline and polls the
trusted cancellation marker even when Python is stuck in a native call.
The parent fixes each request deadline when publishing it; worker pickup does not
reset that budget or extend the absolute lifetime.

The existing Landlock filesystem/network policy, 4 GiB address-space limit,
60-second cumulative CPU limit, process/file limits, and environment scrubbing
apply throughout the session. Sessions do not receive provider credentials. Source
is limited to 64 KiB per request, each IPC message to 1 MiB, script stdout to
512 KiB UTF-8, and results retain the existing depth/item limits. There is one
pending request, one latest result, one latest telemetry slot, and at most 32
snapshot URIs. CPU time is cumulative across requests, not reset by a new request.

Listening sensors consume CPU in the sandbox on every delivered frame, including
between requests. The cumulative 60-second CPU allowance may expire before the
wall-clock session timeout. Listener queues share the adapter's 512 MiB native
payload reservation policy; this is not a total-RSS guarantee. Default drains
return compact metadata even with `output_dir` set; saving additionally requires
`save_frames=True` and incurs encoding cost on the caller thread. See
[sensor queue memory and CPU limits](sensor-timing.md).

The focused tests cover persistent state, per-client isolation, malformed file
messages, request backpressure, deadlines, blocked-script cancellation, disconnect,
and cleanup failures. Live CARLA validation performed spawn, throttle, and brake
requests using one retained vehicle, sampled telemetry, then verified no actor leak
and unchanged asynchronous world settings. Dedicated simulator checks remain
necessary after changing the installed CARLA or sandbox runtime.
Those historical checks do not validate interrupted multi-create recovery or
large-map timeout behavior, or the dedicated Traffic Manager sidecar guards and
declared cleanup targets; live CARLA 0.9.16 acceptance for those paths remains
pending.
