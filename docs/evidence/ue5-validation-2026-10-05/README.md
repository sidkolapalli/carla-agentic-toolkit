# CARLA UE5 validation — 2026-10-05

The tested core workflows run on the latest published UE5 package,
[CARLA 0.10.0 / Unreal Engine 5.5](https://github.com/carla-simulator/carla/releases/tag/0.10.0),
after the compatibility fixes below. This is single-machine experimental evidence,
not full feature parity or production certification.

The allowlisted [numerical summary](summary.json) records every merge trial,
including initial failures, source fingerprints, trace hashes, cleanup outcomes,
package provenance and limitations. Full traces, credentials and simulator assets
are not published with this summary. Raw traces and diagnostics remain under the
ignored `target/ue5-validation-2026-10-05` directory and private Linux state.

## What ran

The official Windows simulator used an AMD Radeon RX 7900 XTX, Town10HD_Opt,
offscreen rendering and Low quality. The toolkit ran in an isolated Ubuntu 26.04
WSL2 checkout with Python 3.12.14, the matching official **Linux** 0.10.0 wheel,
the locked dependencies and a newly compiled Rust sandbox. Existing 0.9.16
installation files and environments were retained.

Windows blocked inbound WSL access to the new executable. A temporary local stdio
relay carried CARLA RPC/streaming traffic without changing firewall rules. Its
streaming connections were prepared before paused-world tests to avoid a relay
startup race. This transport adds overhead and is not shipped by the toolkit;
the measurements are not native networking or rendering benchmarks.

The final Python source fingerprint was
`24640591021fc4c2d9e9b7ffd94e9d5adbd0949c63fff0feebe9cdadada0d3b8`.
The installed simulator's `VERSION` identifies CARLA commit
`ada75f920642e18cace0a9f85ecf9d4077ddb531`. Locally computed download hashes
are in the summary; no independently published archive checksum was available.

## Findings and fixes

1. **Vehicle catalog:** the original Tesla blueprint is absent. Added the explicit
   `town10-merge-ue5-v1` Lincoln MKZ fixture and a configurable smoke-test vehicle.
   The original UE4 fixture remains the default. Missing fixture vehicles now
   produce an actionable error.
2. **Local steering:** the initial rules and Jev Lincoln runs both overshot the
   target lane and collided with the environment. Increasing heading correction
   from 0.9 to 1.8 only for the UE5 fixture corrected the observed overshoot.
   Vehicle and gain are recorded in fixture metadata. Geometry, braking and
   collision checks were retained.
3. **Pre-frame destruction:** a valid UE5 actor handle can return `false` from
   `destroy()` before its first snapshot. Cleanup now requests an authoritative,
   non-ticking server destruction in that case. A rejected server response keeps
   ownership intact. The failed probe actor was explicitly recovered.
4. **Weather:** the full smoke correctly failed its weather-change assertion.
   Direct API probes confirmed no change, matching CARLA's
   [fixed-daylight limitation](https://carla.org/2024/12/19/release-0.10.0/).
   An explicit `--skip-weather` mode reports weather as untested while checking
   driving, camera delivery and cleanup. It does not turn weather into a pass.

Each implementation fix began with failing regression tests. The final local
quality gate passed **594 Python tests**, with two expected skips, and **nine Rust
tests**, plus lint, formatting, typing and complexity/maintainability checks.

## Final measured cohort

Three repeats per policy used seed 7 and the same dedicated world, Lincoln fixture,
50 ms fixed step and request budget. The strict comparison accepted the six final
traces with no matching blockers or exclusions. Earlier implementation revisions
remain recorded separately and are not added to these denominators.

| Measurement | Rules | Jev |
| --- | ---: | ---: |
| Completed maneuvers | 3/3 | 3/3 |
| Runs with delivered collision events | 0/3 | 0/3 |
| Actor/settings restoration | 3/3 | 3/3 |
| Actual provider responses | 0 | 48 |
| Mean recorded decision latency | N/A | 152 ms |
| Mean tracking error RMSE | 1.332 m | 1.332 m |
| Mean delivered lane-invasion events | 4 | 4 |
| Dropped sensor samples | 0 | 0 |
| Mean simulation/wall-time ratio | 1.359 | 0.952 |

Lane-invasion events include the intended lane crossing. Tracking RMSE measures
error against the changing local target across the whole maneuver. Zero delivered
collision events is an observation, not proof of collision absence or safety.
These are repeated runs of one constrained scene, not independent scenario
coverage or evidence that Jev outperforms the rules policy.

Jev used `typesafe-sdk==0.7.2`, model `jev-1.13.0`, and bounded Choice requests over
numerical simulator ground truth. It selected maneuvers; the local planner and
controller retained trajectory, actuation, timing and cleanup responsibility.

The separate one-request trial made one Jev selection followed by 16
`budget_exhausted` fallbacks. It stayed in the source lane and ended incomplete
with `corridor_invalid`, without delivered collision events, then restored
actors/settings. An incomplete maneuver is the recorded fallback outcome.

## Lifecycle and limits

The public MCP check passed vehicle control, camera image/resource delivery,
Landlock enforcement, import rejection, timeout classification and restoration.
Dedicated probes also passed destruction before a simulation tick, owned-actor
cleanup after a blocked native timeout, persistent follow-up state and telemetry,
and managed cancellation with actor/settings restoration.

One earlier persistent close returned a missing cleanup-result file although the
actors were removed. Explicit recovery cleared the empty journal; subsequent
instrumented and uninstrumented checks passed. That source revision left the
failure unresolved. The subsequent [cleanup worker fix](../cleanup-worker-2026-10-05/README.md)
reproduces report loss during native client finalization and validates retaining
the client through publication, explicit exit diagnostics, and live recovery.
The original failed observation remains in this cohort. The separate paused-world streaming
timeout was isolated to the temporary relay: the native Windows client passed,
and preparing relay streaming connections resolved it.

UE5 weather, Light Manager, map-layer/OpenDrive and other upstream gaps still
apply. Method presence in a capability report does not prove that feature works.
This run does not certify UE5 Traffic Manager/density, recorder/replay, ROS2,
arbitrary maps, a native Linux simulator, or production operation.

Reproduce using the [matching-client setup](../../client-setup.md#carla-release-compatibility)
and the explicit [UE5 specifications](../../managed-experiments.md#ue5-fixture).
Configure a reachable dedicated endpoint and verify networking locally first.
