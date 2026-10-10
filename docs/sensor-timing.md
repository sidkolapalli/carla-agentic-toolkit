# Sensor timing and world advancement

One coordinator owns synchronous ticks. Sensor callbacks never tick, wait for buffer
space, or create another controller. Establish the adapter connection before pausing
the world; it retains that client stream across calls. CARLA may advance frames while
applying timing settings, so record setup separately from scheduled experiment steps.
Do not interpret the immediate snapshot after a mode transition as a final barrier.

The script API and Python adapter expose this sequence:

1. Attach the sensor to an existing actor.
2. Call `subscribe_sensor(sensor_id, capacity=32)` before an owner tick.
3. Advance through the owner's `tick()` and retain its returned frame ID.
4. Call `drain_sensor(sensor_id, frame)` to use the bounded two-second delivery wait,
   or pass an explicit `timeout_seconds`.
5. Call `close_sensor_subscription(sensor_id)` when finished, then destroy the actor
   when it is no longer needed.

The adapter retains its newly created sensor handle, so subscription and cleanup do
not depend on the sensor already appearing in the previous world snapshot. Known
new parent IDs use CARLA's explicit actor-ID lookup, so attachment can precede the
next owner tick. This does not make physics measurements current; frame-aligned
observations still require the owner's tick and measurement-frame checks. The managed
fixture uses its own direct creation handles for parents and sensors.

Queues are bounded to 1–1024 samples and by native payload bytes. Each script
adapter reserves at most **512 MiB total** across its listening queues, including
one-shot capture and stream readers. This is a toolkit policy, not a measurement
or guarantee of total process RSS. Trusted Python callers can choose a smaller
positive `sensor_queue_budget_bytes` when constructing `PythonCarlaAdapter`;
values above the ceiling are refused before connection.

Camera reservations use native `image_size_x * image_size_y * 4 * capacity`.
A 1920x1080 camera at capacity 32 reserves 265,420,800 bytes; two fit the
512 MiB policy, three do not. Native camera dimensions that are present but
invalid are refused before `Listen`. Variable-size sensors and legacy handles
without dimension attributes reserve at most 16 MiB per queue (or the chosen
lower execution limit), with actual buffer sizes checked on every sample.
Actual camera measurements use `width * height * 4`; an oversized single sample
is dropped rather than retained. One-shot collections also bound the total raw
payload retained for their result separately from the pending queue; those two
buffers together can exceed one reservation. Queue reservations are released when callback
acceptance freezes, including failed-listener cleanup; a failed `Stop` still
requires the existing shutdown retry.

Overflow discards the oldest sample and reports
a cumulative dropped count. Draining returns samples at or before the requested frame
and retains future samples. Metadata includes measurement frame, frame lag, timestamp,
arrival time, queue latency, pending count and timeout state. GPU camera delivery can
lag behind simulator ticks; a missing periodic frame is explicitly timed out rather
than relabeled as a current observation.

Collision, lane-invasion and obstacle blueprints default to event mode. An empty event
drain is normal and never waits, even when a timeout is supplied. Periodic drains
default to a two-second wait at the script, adapter, and subscription boundaries;
explicit waits remain bounded to 30 seconds. A queued frame at or beyond the requested
frame ends the wait. A later sample retains its own frame and stays queued: it is not
returned as the missing requested measurement. No delivery wait advances the world.
Managed GNSS observation uses the same positive wait; managed collision and lane
events remain nonblocking. The same primitives work in an asynchronously advancing
world.

Periodic delivery metadata includes `no_sample_due` and nullable `schedule` evidence.
An actual requested sample reports `no_sample_due=False`. With a readable
`sensor_tick` and fixed step, two consecutive measurements whose frames and timestamps
agree with an integral cadence establish an observed phase. A skipped frame within
that evidence reports `no_sample_due=True`, does not wait, and is not timed out.
Missing due frames remain timed out, including frames discarded by queue overflow.
Before phase confirmation, for requests preceding the first observed sample, or when
cadence is nonintegral or its clock evidence changes, `no_sample_due` is `null`.
A first or later callback cannot prove pre-subscription emission history. Phase
inference assumes the observed cadence remains stable until contradicted; it is not
a guarantee of future sensor emission. Unknown missing frames use the bounded wait
unless a queued future frame already ends it.

Trusted managed consumers can call `SensorSubscription.close_and_drain(frame)` to stop
upstream and then freeze the queue. It returns all bounded trailing samples, including
samples newer than the supplied cutoff with their original measurement frames. Late
callbacks cannot revive a closed queue. Script execution closes all listener queues
on success and failure; failed cleanup is reported without hiding the original script
exception. Ordinary close and actor destruction are separate operations.

Destroying a subscribed sensor closes the script's original listener handle
before requesting a non-ticking server destroy, including sensors inherited from
another client. Subscribing does not adopt the sensor into actor ownership. The
adapter retains its subscription episode separately, including after an explicit
listener close, so an old numeric ID cannot authorize cleanup in a replacement
world. An episode change before or during shutdown refuses subsequent destruction.

A failed native `stop()` freezes the queue but does not count as successful
unsubscribe. The original subscription remains available for a same-episode retry;
repeated failures stay visible in final script cleanup. A normal `stop()` return
makes listener close idempotent, but is not an actor-removal readback. A later
callback cannot revive the frozen queue.

The legacy `capture_sensor_frame` and `read_sensor_stream` convenience calls support
asynchronous worlds. They reject a synchronous world before listening, with guidance
to use the explicit subscription sequence. They cannot own an implicit tick while
waiting for a frame.

`drain_sensor` returns only compact digests and delivery metadata by default,
even when `output_dir` is supplied. It does not copy raw arrays into JSON or
encode files on the tick owner thread. To request persistence explicitly, use
`drain_sensor(sensor_id, frame, output_dir="captures", save_frames=True)`.
This replaces the previous output-directory-only drain behavior. Explicit writes
still run on the caller thread; choose their cadence within the CPU budget.
The asynchronous `read_sensor_stream(..., output_dir=...)` convenience API remains
an explicit collection-and-save request.

Requested persistence preserves camera images as raw PNG files and
LiDAR point clouds as PLY files. Numerical measurements without a native writer
return digests without invented file paths. Display conversion is opt-in for a
separate published image, never the raw depth or segmentation ground truth.
See [sensor evidence and publication limits](script-workflows.md#preserve-raw-sensor-evidence).

A listening sensor consumes CPU in the sandboxed process on every delivered
frame, even when it is never drained or saved. PNG encoding adds CPU cost.
The sandbox has a **60-second cumulative CPU limit**, including across
persistent-session requests, and a 4 GiB address-space limit. The CPU allowance
can expire before the requested wall-clock timeout. The queue policy does not
bound native transport buffers, Python overhead, returned samples retained by
trusted callers, other process allocations, or total RSS.

## Rendering requirements

All `sensor.camera.*` blueprints require an explicitly observed
`no_rendering_mode=False`. Camera attachment, generic camera spawning,
subscription, one-shot capture, stream reads, and `save_screenshot` refuse
disabled, unreadable, or malformed rendering settings with a structured error
that names `no_rendering_mode`. The check happens before a creation intent,
native spawn, or new listener; screenshot preflight also precedes spectator
inspection. A camera attached earlier is checked again before a new read.

GNSS, IMU, collision, lane-invasion, obstacle, lidar, and radar sensors are not
subject to this camera restriction. Buffered samples can still drain and
listeners can still close after rendering is disabled. Sensor destruction
remains available for cleanup. Off-screen rendering is different from
no-rendering mode: `-RenderOffScreen` can produce camera data when
`no_rendering_mode` is false. See CARLA's
[rendering options](https://carla.readthedocs.io/en/0.9.16/adv_rendering_options/).

## Other world mutations

Traffic population and autopilot request models accept `advance_world=False`. The
script JSON request parser preserves that flag; these traffic requests keep their
existing `advance_world=True` defaults but remain asynchronous-only. Disabling
advancement does not permit Traffic Manager use in a synchronous world. Batch
operations instead default to `do_tick=False`, passed to CARLA's actual
`apply_batch_sync` option, matching its native default. Only an explicit
`do_tick=True` requests a tick. Batch results
report the observed `synchronous_mode` and whether it changed across the call.
Required advancement errors are raised; partial population errors retain created
IDs and cleanup evidence. See [world and batch defaults](script-workflows.md#bound-rpc-timeouts-and-map-changes)
for settings-reset choices and the comparison with CARLA.

Finite scripts bind creation journals to the CARLA episode before spawning. Cleanup
checks that identity so numeric IDs from an older world cannot destroy replacement
actors. A cached snapshot missing a newly created actor is not proof of removal:
the adapter obtains an authoritative server destroy response with `do_tick=False`.
Unidentified legacy journals fail closed for nonempty cleanup. Empty scripts and
preflight do not connect merely to create an ownership journal.

CARLA recorder replay exposes no non-ticking option in the supported binding.
`replay_recording(..., do_tick=False)` rejects the request before mutation. A boolean
`replay_sensors=False` is not a tick control. Traffic Manager traffic creation,
configuration, per-vehicle changes, and density maintenance reject synchronous
worlds before Traffic Manager access or mutation. A synchronous-manager
configuration request is rejected even in an asynchronous world. Both enabling
and restoring synchronous world settings require a fully stopped traffic
controller. See
[Traffic Manager mode and cleanup policy](script-workflows.md#keep-traffic-and-simulation-timing-explicit)
for the dedicated sidecar requirement, declared global cleanup targets, and why
asynchronous seeds do not guarantee reproducibility.
