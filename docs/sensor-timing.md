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
4. Call `drain_sensor(sensor_id, frame, timeout_seconds=...)`.
5. Call `close_sensor_subscription(sensor_id)` when finished, then destroy the actor
   when it is no longer needed.

The adapter retains its newly created sensor handle, so subscription and cleanup do
not depend on the sensor already appearing in the previous world snapshot. Known
new parent IDs use CARLA's explicit actor-ID lookup, so attachment can precede the
next owner tick. This does not make physics measurements current; frame-aligned
observations still require the owner's tick and measurement-frame checks. The managed
fixture uses its own direct creation handles for parents and sensors.

Queues are bounded to 1–1024 samples. Overflow discards the oldest sample and reports
a cumulative dropped count. Draining returns samples at or before the requested frame
and retains future samples. Metadata includes measurement frame, frame lag, timestamp,
arrival time, queue latency, pending count and timeout state. GPU camera delivery can
lag behind simulator ticks; a missing periodic frame is explicitly timed out rather
than relabeled as a current observation.

Collision, lane-invasion and obstacle blueprints default to event mode. An empty event
drain is normal and never waits, even when a timeout is supplied. Periodic drains wait
only for delivery of the requested frame, with a finite timeout bounded to 30 seconds.
The same subscription primitives work with an asynchronously advancing world.

Trusted managed consumers can call `SensorSubscription.close_and_drain(frame)` to stop
upstream and then freeze the queue. It returns all bounded trailing samples, including
samples newer than the supplied cutoff with their original measurement frames. Late
callbacks cannot revive a closed queue. Script execution closes all listener queues
on success and failure; failed cleanup is reported without hiding the original script
exception. Ordinary close and actor destruction are separate operations.

The legacy `capture_sensor_frame` and `read_sensor_stream` convenience calls support
asynchronous worlds. They reject a synchronous world before listening, with guidance
to use the explicit subscription sequence. They cannot own an implicit tick while
waiting for a frame.

## Other world mutations

Traffic population and autopilot request models accept `advance_world=False`. The
script JSON request parser preserves that flag. Batch operations accept
`do_tick=False`, passed to CARLA's actual `apply_batch_sync` option. Existing defaults
continue advancing once for finite-script compatibility. Required advancement errors
are raised; partial population errors retain created IDs and cleanup evidence.

Finite scripts bind creation journals to the CARLA episode before spawning. Cleanup
checks that identity so numeric IDs from an older world cannot destroy replacement
actors. A cached snapshot missing a newly created actor is not proof of removal:
the adapter obtains an authoritative server destroy response with `do_tick=False`.
Unidentified legacy journals fail closed for nonempty cleanup. Empty scripts and
preflight do not connect merely to create an ownership journal.

CARLA recorder replay exposes no non-ticking option in the supported binding.
`replay_recording(..., do_tick=False)` rejects the request before mutation. A boolean
`replay_sensors=False` is not a tick control. Background density maintenance likewise
rejects synchronous worlds before resetting actors or changing Traffic Manager state.
