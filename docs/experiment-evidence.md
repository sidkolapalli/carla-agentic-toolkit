# Experiment evidence

Managed experiments use one authoritative JSONL event envelope (`schema_version=1`).
Each event carries the run ID, world generation, actor ID or null, simulator frame or
null, contiguous sequence number, kind, monotonic seconds, zoned wall time, and data.
The coordinator records metadata, observations, candidate sets, decision requests and
responses, validation, executed controls, interventions, sensor delivery, cleanup, and
outcomes. Requested choices and executed controls are separate events. Original
numerical values are preserved beside any versioned semantic features.

The metadata event records the fixture, code, controller and policy versions,
replicate indices, and environment. Decision metadata records requested model/question configuration,
returned model/question versions, and available identifiers. Missing provider
usage is unknown, not zero. Reports reproduce saved metadata; they do not infer it
from the current checkout.

## Storage and recovery

`TraceStore` creates a unique directory under the private state root, outside every
generated-script readable/output directory. Directories are owner-only and events
use exclusive, no-follow append descriptors with an operating-system writer lock.
Each acknowledged event has been flushed to storage. The default limit is 16 MiB per
run, configurable up to 64 MiB; one event is limited to 256 KiB. Exceeding a limit must
stop the experiment with invalid evidence rather than silently dropping history.
Inline lifecycle results contain summaries and paths, never the full trace.

Credential field names, bearer tokens, and common API-key text are rejected before
writing, including serialized JSON within provider metadata. Trusted callers must
still supply reviewed configuration fields rather than arbitrary environment dumps.
Only the trusted worker and recovery supervisor write evidence.

Recovery may resume after the prior writer has terminated. The writer lock prevents
concurrent resume. Recovery preserves interrupted bytes, separates an unterminated
record, and appends explicit interruption/cleanup/outcome evidence. Corrupt identity
or sequence records block resume. A recovered interrupted trace remains
infrastructure-invalid; a successful cleanup does not rewrite the experiment result.
Well-formed traces without terminal outcomes are partial. Simulator and cleanup
failures are infrastructure-invalid.

## Physical metric definitions

`summarize_trace` and `write_trace_report` derive the same versioned summary solely
from saved records. The static report has no external scripts, styles, or services.
Coverage counts make absent observation/sensor records visible. Sensor samples retain
measurement frame, delivery frame, drops, trailing status, collision impulse vectors,
and lane-marking evidence. Separate sensor events feed the aggregate metrics.

| Metric | Definition and units |
| --- | --- |
| Collision events | Delivered collision samples; impulse magnitude sums use kg m/s. |
| Completion | Explicit terminal completed boolean; unknown without outcome evidence. |
| Hesitation | Actor-seconds between observations whose preceding absolute longitudinal speed is at most 0.2 m/s. |
| Reversals | Sign changes in `longitudinal_speed_mps`, ignoring magnitudes at or below 0.2 m/s; legacy traces use `speed_mps`, which must be signed to represent reversals. |
| Tracking error | Root mean square of recorded lateral tracking error, in metres. |
| Interventions/fallbacks | Recorded controller interventions and their explicit fallback flags. |
| Latency/staleness | Mean recorded decision wall latency in seconds and maximum frame age. |
| Sensor drops | Maximum cumulative drop count per world/actor/sensor stream, summed across streams. |
| Trailing sensors | Delivered sensor records explicitly marked trailing at shutdown. |
| Real-time factor | Sum of per-world simulation duration divided by full-trace monotonic duration. |

Gap and TTC calculations require declared reference points, numerical positions, and
positive closing speed along the same path. Semantic scores and unsigned speed are
not TTC measurements. Crossing-path and acceleration prediction are outside this
metric version. No aggregate alone establishes improved realism or safety.

## Offline replay identity

Recorded-response replay is an offline saved-context API. Load saved events with
`load_trace`, construct `RecordedPolicy` from that trace, and supply a `PolicyRequest`
reconstructed from its saved observation, candidate set, and decision context to
`await policy.choose(request, fallback_id)`. This path needs neither CARLA nor a
provider connection and does not create or advance a simulator scene.

`RecordedDecisionReplay` checks world generation, actor, frame, canonical numerical
observation digest and canonical candidate digest. Candidate digests include expiry;
the separately recorded stable candidate-set ID is retained for policy identity.
`RecordedPolicy` additionally checks revision, phase, and maneuver generation before
returning the recorded choice using the current request's run ID and deadline.
An absent, ambiguous, or changed identity fails explicitly. Reusing a fixed action in
a changed scene is a different experiment and is never labeled recorded-response
replay. Matched rules/provider comparisons require separately validated runs with
matched fixtures and replicate indices; repeated runs do not promise bitwise
CARLA determinism.

Fresh live replay is unsupported. Public managed `start`/`run` and direct engine
entrypoints reject `policy: replay` before simulator access or mutation. A new live
session necessarily has a new world generation, actor IDs, and frames; the runtime
does not weaken saved identity checks or substitute a fixed action sequence to make
that request appear valid. Historical specifications may still retain `replay` and
`replay_run_id` so their saved evidence remains readable.

## Matched static comparison

After saving rules and Jev runs, generate a report without connecting to CARLA or a
provider:

```bash
uv run --no-sync python -m carla_agentic_toolkit.experiment_comparison \
  --output "$HOME/carla-comparison" \
  /absolute/path/to/rules/events.jsonl /absolute/path/to/jev/events.jsonl
```

Supply every declared trial, including failures; repeat trace arguments for multiple
replicate indices. The command writes `comparison.json` and a self-contained,
escaped `comparison.html`. Exit code 0 means the reproduction evidence matched;
exit code 2 means comparison was refused and the saved report explains why.

Matching requires the complete saved specification except policy/replay run ID,
the exact fixture except ephemeral actor IDs, replicate index, environment,
package/code hash, planner, controller, and fixture versions. Each cohort must contain equal rules and
Jev sample counts. Missing counterparts, duplicate run IDs, missing versions, changed
poses, and mismatched constraints withhold comparative metrics. Saved run summaries
and genuine provider metadata remain visible.

Legacy `seed` labels are normalized to `replicate_index` only for comparison;
source traces and their hashes are unchanged. No missing specification fields
are filled in to make historical runs match. Replicate indices vary nothing.
Identical recorded initial conditions across distinct indices produce a separate,
nonblocking warning, not evidence of independent conditions or bitwise determinism.

Physical rates exclude partial, cancelled, infrastructure-invalid, and insufficient-
evidence runs while retaining them in sample and exclusion counts. Completion is the
explicit completed count divided by eligible runs. Observed collision rate counts
runs with delivered collision evidence, not a guarantee that other runs were free
of collisions. Numeric averages are unweighted means of measured per-run values,
each with its own denominator. Unequal attrition can bias a comparison. Missing
usage stays unknown; provider/controller attribution and significance are not inferred.
