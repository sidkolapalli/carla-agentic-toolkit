# Script workflows

Use `execute_carla_script` to combine inspection, scene changes, vehicle control,
sensors and evidence in one finite Python workflow. Connect a matching CARLA
server and Python client using the [client setup guide](client-setup.md) first.

## Write a script and return a result

Scripts receive a curated `api` object. Assign a JSON-compatible value to
`result` to return it to the MCP client. For example, on a connected simulator:

```python
health = api.health_check()
world = api.get_world_state()

result = {
    "connected": health["connected"],
    "map": world["current_map"],
    "actors": world["actor_counts"],
}
```

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

Published PNG/JPEG captures include native `ImageContent` for vision-capable
clients and a `carla-output://capture/...` Resource link for later reads. JSON
text remains first for clients that ignore images. The linked file must remain
in the configured output directory to be read later.

Publication permits at most four images, each no larger than 512 KiB, within a
1 MiB combined encoded result. Encoding and JSON also consume that combined
budget, so two images below the individual limit can still exceed it. Every
published path must resolve below `CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR`.

Synchronous worlds require the explicit sequence `subscribe_sensor` → owner
`tick` → `drain_sensor` → `close_sensor_subscription`. Bounded queues report
delayed and dropped frames; empty collision-event drains do not wait. The
`capture_sensor_frame` and `read_sensor_stream` convenience calls reject
synchronous worlds. Follow [sensor timing and advancement](sensor-timing.md)
for frame ownership, delivery and listener cleanup.

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
host and port, and survive MCP server restarts that reuse that directory.
`resolve_actor()` checks the simulator and removes stale aliases. Use
`api.list_named_actors()` and `api.forget_actor()` to manage the registry.
Names are a convenience and do not grant ownership or authorization.

## Own the lifecycle and cleanup

Successful finite scripts retain their created actors. Call
`api.cleanup_owned_actors()` to destroy actors created by the current execution,
or use explicit destroy methods when the scene no longer needs them. Actors
retained from an earlier call require explicit cleanup using their known IDs;
assigning an alias does not add them to the current execution's ownership journal.

Uncaught exceptions and timeouts trigger bounded, best-effort CARLA-side cleanup
of journaled actors without touching pre-existing actors. Failed or unreachable
cleanup remains visible; a failed script is not proof that its actors are gone.
Actor cleanup does not substitute for restoring world settings your script changed.

One MCP server serializes complete finite script executions against shared
simulator state. `timeout_seconds` starts after queueing and covers sandbox
execution; time spent waiting for the server's execution lock is outside it.
Allow for queueing when configuring the MCP client's tool timeout.

Cooperating scripts, persistent sessions and managed workers coordinate through
a simulator lease. Configure the same private state directory and unambiguous
endpoint for every local process. This coordination cannot stop unrelated CARLA
clients from changing the world. The [security policy](../SECURITY.md) explains
the process boundary and supported deployment assumptions.

## Keep traffic and simulation timing explicit

Traffic controller state lasts for one script process. Keep that process alive
with `api.wait()` within its execution budget, or use the documented live
sidecar. Autopilot and traffic tuning require an existing Traffic Manager server
in a trusted client outside the sandbox; follow
[Traffic Manager setup](client-setup.md#traffic-manager).

Background density maintenance supports asynchronous worlds. Synchronous density
maintenance and non-ticking recorder replay fail before mutation. When your
caller owns the clock, population/autopilot requests accept `advance_world=False`
and batch operations accept `do_tick=False`. See [sensor timing](sensor-timing.md)
before combining these operations with an explicit tick owner.

## Put outputs on the correct host

Relative capture and evidence paths such as `captures/front.png` resolve under
`CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR` on the toolkit host. Requested durable output
stays there; per-run scratch space is deleted after script execution.

CARLA recorder paths are opened by the simulator host. Set
`CARLA_AGENTIC_TOOLKIT_RECORDER_DIR` to an absolute directory understood by that
host. The toolkit reports the exact path CARLA accepted and does not copy the
recording between hosts. This distinction matters when CARLA runs on Windows
and the toolkit runs in WSL2; see the [Windows setup](client-setup.md#windows-11-with-wsl2).

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
