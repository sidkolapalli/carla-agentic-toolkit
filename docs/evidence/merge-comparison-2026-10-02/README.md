# Matched merge evidence — 2026-10-02

This is a descriptive evaluation of six declared trials: seeds **7, 19 and 31**,
one rules run and one Jev run per seed, with no failed-trial retries. All six
completed the maneuver, passed cleanup, and restored the original actors and
world settings. All six are eligible; none are partial, invalid or excluded.
Three successful pairs in one fixture do not establish comparative safety or
realism.

The later [budget-exhaustion probe](../merge-budget-fallback-2026-10-02.json) is a
separate failure-path demonstration and is not included in these sample counts.
It terminated and restored actors/settings, but did not complete the maneuver
(`corridor_invalid`). See the [article's reproduction steps](../../jev-integration-follow-up.md#a-separate-budget-fallback-demonstration).

Open [the static report](comparison.html) or inspect
[the numerical comparison](comparison.json). Both come from the saved-trace
comparison tool. The JSON retains exact matching evidence, per-run metrics,
denominators, provider metadata and unknown values. The HTML is self-contained
and has no external resources or executable scripts.

## Provenance and contents

Implementation commit:
[`49a739ebab377f04161afd2201d93fab1a4bd8d7`](https://github.com/sidkolapalli/carla-agentic-toolkit/commit/49a739ebab377f04161afd2201d93fab1a4bd8d7).
Package-source fingerprint:
`85f1aa3aae493397edb61b242e4cc7a0cf2b57619a372837d3fe5d1ed0ff4d68`.
The local evaluation date is October 2 in America/New_York; exact UTC event times
are retained in the manifest and excerpt.

| File | Purpose |
| --- | --- |
| [evaluation.json](evaluation.json) | Trial plan and the complete saved specification for each of the six runs. |
| [comparison.json](comparison.json) | Exact saved comparison with private source paths replaced by aliases. |
| [comparison.html](comparison.html) | Standard comparison renderer applied to that same projected JSON. |
| [trace-manifest.json](trace-manifest.json) | Original trace SHA-256 hashes, run IDs, sizes, event counts, times, restoration checks and derived-file hashes. |
| [decision-trace-excerpt.json](decision-trace-excerpt.json) | A real seed-7 Jev observation → candidate → request → decision → validation → execution sequence. |

Raw traces remain in private local state and are not part of this bundle.
`private-trace:seed-7-jev/events.jsonl` and its peers are stable reference aliases,
not resolvable URLs or local paths. Only `runs[].source_path` was changed in the
comparison; all other values, including matching identities and numerical fields,
are identical to the tool's result recomputed from the six original traces.
Raw trace hashes were checked against the live validation manifest. The excerpt
documents its omitted fields and keeps original event envelopes and retained
values. It is a projection, not a replacement complete trace or replay input.

The recorded environment is CARLA server/API 0.9.16, Python 3.12.14 and WSL2 kernel
6.18.33.2. The fixture is `town10-merge-v1`, planner
`bounded-merge-planner-v1`, controller `bounded-lateral-speed-tracker-v1`.
All trials use `simulation_time`, 0.05-second steps, 6 m/s policy target and 5 m/s
ego target. The fixture uses range-filtered simulator ground truth, not realistic
occlusion or inferred driver intent. The recorded host/port identify the dedicated
test endpoint; choose your own endpoint for reproduction.

Jev made 36 recorded requests with SDK 0.7.2 and requested/returned model
`jev-1.13.0`. The local question version is `carla-merge-choice-v1`;
`returned_question_version` is null because it was not provided. The saved token
usage is retained where returned. Rules usage and its provider-latency mean remain
null, not invented zeros.

## Reproduce

Follow [managed setup](../../managed-experiments.md), install the optional SDK,
configure the credential only in the trusted environment, and use a dedicated
initially asynchronous Town10HD instance. On the tested implementation, copy the
saved specifications into an ignored working directory and change only the
endpoint consistently:

```bash
uv run --no-sync python - <<'PY'
import json
from pathlib import Path

bundle = Path("docs/evidence/merge-comparison-2026-10-02")
output = Path("target/reproduce-merge")
output.mkdir(parents=True, exist_ok=True)
for run in json.loads((bundle / "evaluation.json").read_text())["runs"]:
    spec = run["spec"]
    spec["host"], spec["port"] = "127.0.0.1", 2000  # Your dedicated CARLA endpoint.
    name = f"seed-{spec['seed']}-{spec['policy']}.json"
    (output / name).write_text(json.dumps(spec, indent=2) + "\n")
PY
```

Run in the declared order and retain failures instead of replacing them silently:

```bash
set -o pipefail
for seed in 7 19 31; do
  for policy in rules jev; do
    if ! uv run --no-sync carla-agentic-toolkit-experiment run \
      --spec "target/reproduce-merge/seed-$seed-$policy.json" \
      | tee "target/reproduce-merge/result-$seed-$policy.json"; then
      # Inspect termination/cleanup before admitting another owner.
      break 2
    fi
  done
done
```

Use every attempted result's returned `trace_path` with
`python -m carla_agentic_toolkit.experiment_comparison --output YOUR_REPORT_DIR`.
Supply all six paths for a completed six-trial plan. If interrupted, retain the
partial input set; the tool must refuse a missing counterpart instead of inventing
it. The [comparison guide](../../experiment-evidence.md#matched-static-comparison)
explains matching and exclusion rules. New trials have new frame, actor and run
identities; matching the setup does not promise identical physics or provider
choices.

## Interpretation

Completion was 3/3 per policy; delivered-collision events, interventions, fallbacks,
reversals and sensor drops were zero for both. Each run recorded four lane-invasion
events during the maneuver. These are sensor event counts, not collision rates or
evidence of realistic driving. Mean per-run tracking RMSE was 0.846222 m for rules
and 0.844090 m for Jev. Mean real-time factor was 3.012089 and 0.820965 respectively;
rules varied substantially across seeds, so the aggregate is not a controlled
latency benchmark. Jev's mean of per-run decision latencies was 0.155717 seconds.

All physical rate denominators are three eligible runs per policy. Numerical means
use only runs with that measurement and retain their denominators; rules provider
latency has denominator zero and a null mean. Cleanup and trailing sensor evidence
remain separate from completion. Inspect the per-run values and the
[metric definitions](../../experiment-evidence.md#physical-metric-definitions)
before interpreting these small-sample results.
