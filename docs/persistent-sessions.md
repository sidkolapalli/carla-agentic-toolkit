# Bounded persistent script sessions

Set `CARLA_AGENTIC_TOOLKIT_ENABLE_SCRIPT_SESSIONS=1` before starting the Linux/WSL2
stdio server to expose the optional `carla_script_session` tool. The existing
finite `execute_carla_script` tool remains available. A session retains its curated
`api` object and ordinary Python variables across requests. This mode runs generated
code inside the Rust sandbox; managed experiment specifications use a separate
trusted worker.

Call `open` with a `config` object:

```json
{"host":"127.0.0.1","port":2000,"idle_timeout_seconds":60,"absolute_timeout_seconds":300,"request_timeout_seconds":30}
```

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

Each request receives the same trusted API and safe builtins; the `result` variable
is reset before execution. Validation runs on every request. A script exception
is returned explicitly and may leave earlier variable assignments in place.

Use action `telemetry` with `config: {"actor_id": 123, "interval_seconds": 0.2}`
to sample one session-owned actor using the trusted, non-ticking vehicle telemetry
method. Intervals must be 0.1–10 seconds. `actor_id: null` disables sampling.
`read` returns the latest sample only; old samples are replaced, never queued.
Sampling occurs between script requests, so a running script or slow RPC delays
it. This interface does not promise real-time control or sensor-frame alignment.

Call `close` when finished. It terminates the complete sandbox process group and
destroys session-created actors, including actors left alive by a successful script.
`cancel` also terminates an active or blocked request and reports cancellation.
Client disconnect triggers the same cleanup for every owned session. `active`
stays true until the worker is dead and cleanup finishes. Cleanup failure leaves
durable recovery evidence and prevents another mutation from acquiring the lease.

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

The existing Landlock filesystem/network policy, 4 GiB address-space limit,
60-second cumulative CPU limit, process/file limits, and environment scrubbing
apply throughout the session. Sessions do not receive provider credentials. Source
is limited to 64 KiB per request, each IPC message to 1 MiB, script stdout to
512 KiB UTF-8, and results retain the existing depth/item limits. There is one
pending request, one latest result, one latest telemetry slot, and at most 32
snapshot URIs. CPU time is cumulative across requests, not reset by a new request.

The focused tests cover persistent state, per-client isolation, malformed file
messages, request backpressure, deadlines, blocked-script cancellation, disconnect,
and cleanup failures. Live CARLA validation performed spawn, throttle, and brake
requests using one retained vehicle, sampled telemetry, then verified no actor leak
and unchanged asynchronous world settings. Dedicated simulator checks remain
necessary after changing the installed CARLA or sandbox runtime.
