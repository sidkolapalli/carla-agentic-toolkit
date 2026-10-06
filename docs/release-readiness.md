# Release readiness

This record distinguishes the experimental source-only alpha from production
readiness. On 2026-10-06, the owner authorized landing the tested changes and
opening the repository while documented feature work continues. The final
candidate, visibility transition, reporting checks and prerelease receipt are
tracked in [#82](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/82).
PyPI/wheel publication and production-readiness claims remain outside this scope.

## Release acceptance gates

| Gate | Required evidence |
| --- | --- |
| Candidate and CI (#81, #82) | PRs #92 and #95 are merged. Their combined source/evidence tree `02ac49e` passed [CI 37384683695](https://github.com/sidkolapalli/carla-agentic-toolkit/actions/runs/37384683695): 643 Python tests, one opt-in skip, nine Rust tests, and quality, dependency, history, package and container gates. The final publication commit requires its own successful CI; record that commit and run in #82 before tagging. |
| Public release (#82) | Owner authorized the experimental public alpha on 2026-10-06. Publish only the tested final candidate and record its prerelease tag. Distribution remains a source checkout; the wheel does not bundle the Rust runner. |
| Private reporting (#82) | The owner selected `[withdrawn maintainer email]` for private security and conduct reports and confirmed access on 2026-10-06. Inbox delivery was not independently tested. Enable GitHub private vulnerability reporting during publication and verify API configuration and the public reporting route. Record what was actually checked in #82; a maintainer API response or visible link does not prove submission from an external researcher's account. Email remains available independently of that form. |
| Live provider (#86) | Passed the separately opted-in live Jev check using the existing Windows Credential Manager entry through private process input. The key was neither printed nor copied to configuration or report files. |
| Matched evaluation (#87) | Six declared trials completed: one rules and one Jev run for each seed 7, 19 and 31. Strict matching accepted all pairs; all runs verified actor cleanup and world restoration. The [saved comparison](evidence/merge-comparison-2026-10-02/README.md) records sample counts, metrics, versions and limitations. |
| Follow-on article (#87) | Read the current original article before preparing its follow-up. Its URL or file path is still required. |
| Demo (#87) | The [recorded camera demo](evidence/managed-demo-2026-10-03/README.md) shows a completed maneuver and a deliberate budget fallback with an incomplete maneuver. Both cleaned up and restored actors/settings. Exact-frame projections and coverage gaps accompany the video. |
| Reproduction (#87) | An [independent automated operator](evidence/independent-reproduction-2026-10-03/README.md), starting from the checked-in instructions in a fresh checkout/venv/state, reproduced all six trials with no retries or comparison blockers. This is not an external-human or second-machine validation claim. |

## Delivered implementation

The [2026-10-05 route study](evidence/route-study-2026-10-05/README.md) adds
full-frame numerical data and six PNG/SVG graphs to the route experiments.
All three ego cars reached the 135m goal, but the pedestrian never entered the
lane. #96 tracks that defect and physical scenario acceptance. #94 stays open
until the missing crossing validation is complete. Those known limitations are
disclosed in the public alpha; they are not reported as successful hazard tests.
The missing original-article reference in #87 is separate from source publication.

The [2026-10-05 UE5 validation](evidence/ue5-validation-2026-10-05/README.md)
adds a separate Lincoln fixture for CARLA 0.10.0, corrects its steering overshoot,
and fixes destruction of an actor before its first snapshot. The final cohort
completed three rules and three Jev trials, with no delivered collision events
and verified restoration. Local checks passed 594 Python tests (two platform/live
skips) and nine Rust tests. Weather is an explicit upstream limitation. One earlier
persistent-close cleanup report was missing; recovery and reruns succeeded, but
that source revision left its cause unresolved. The subsequent
[cleanup worker lifetime fix](evidence/cleanup-worker-2026-10-05/README.md)
reproduced report loss under fatal native finalization, retained the client through
report publication, and added explicit worker-exit diagnostics. Its final 24 live
closes, forced failure/recovery with an unrelated actor, and finite timeout checks
passed; the local gate passed 599 Python tests and nine Rust tests. These are
separate lifecycle checks from the earlier merge cohort and do not replace the public-release
gates above or establish full UE5 support. Later changes still require candidate CI.

- **#42, #77:** tracked controller lifetime, restart exclusion, worker generations,
  and desired/applied revisions prevent overlap and lost newer requests.
- **#44:** creation ownership is distinct from adopted/pre-existing traffic;
  ordinary density reduction preserves unrelated vehicles.
- **#78–80:** advancement failures are explicit, subscriptions coordinate with
  owner-driven ticks, and unsupported synchronous density is rejected before
  mutation. Non-ticking batch/populate/autopilot variants are explicit; replay's
  unavailable non-ticking capability is rejected.
- **#26, #83:** bounded persistent sandbox sessions and a separate trusted managed
  experiment runtime share cooperative leases, explicit lifecycles, cancellation,
  actor ownership, and recovery barriers.
- **#84:** a versioned two-vehicle merge fixture, numerical candidates, stateful
  tracker, and no-key rules policy use frame-consistent ground truth.
- **#85:** private append-only traces preserve decisions, controls, sensors,
  outcomes, interrupted runs and cleanup; reports derive physical metrics from
  saved events, and offline recorded-response replay requires exact identity.
  Unsupported fresh live replay is rejected before simulator mutation.
- **#86:** the optional pinned Jev Choice adapter implements bounded async
  inference, persistent account reservations, validation and phase-appropriate
  fallback. The configured live provider check passed.
- **#87:** CLI/MCP lifecycle controls, a saved matched rules/Jev comparison, a
  recorded camera demo and independent automated reproduction are delivered.
  The original-article review remains incomplete until its URL/path is supplied.
- **#81, #82:** the workflow pins, full-history scan, source packaging, container
  runtime, and local release checks have been repaired. Publication remains gated.

## Reproduction

The [alpha release notes](alpha-release-notes.md) describe the source-only scope;
the [alpha demo guide](alpha-demo.md) gives reproducible success and failure checks.

Follow [client setup](client-setup.md), then run the repository checks and the
documented offline walkthrough before touching a dedicated simulator:

```bash
uv sync --locked --all-extras --python 3.12
make check
uv run ruff format --check .
uv run python scripts/sandbox_demo.py
```

Install a CARLA Python API matching the dedicated simulator, then use the
[live MCP smoke test](../README.md#demo), [managed experiment commands](managed-experiments.md),
[persistent-session walkthrough](persistent-sessions.md), and
[saved-trace comparison](experiment-evidence.md). Use a private native Linux state
directory and a writable output directory. Windows-mounted directories can have
ACLs that reject Linux writes; verify the selected output path with preflight.

Local validation artifacts are retained under the ignored `target/` directory;
authoritative live traces remain in private Linux state directories. Failed probes
are retained alongside successful reruns. Do not package private diagnostic folders,
credentials, simulator assets, or arbitrary local output with a release.

## Local validation environment

Validation on 2026-10-02 used Ubuntu 26.04 under WSL2 with kernel
`6.18.33.2-microsoft-standard-WSL2`, Python 3.12.14, uv 0.12.5, Rust 1.97.1,
and matching CARLA server/Python API 0.9.16. The dedicated simulator ran on the
Windows host with `Town10HD_Opt`. Live probes restored the original asynchronous
world settings and checked that their own vehicles and sensors were removed.

The separate WSL source checkout used by the desktop MCP was updated to the
validated implementation and passed preflight and the public MCP live smoke test.
Its prior local recorder-test fixture edit was preserved in a backup and Git stash;
the same fixture cleanup is included in the candidate. Existing desktop MCP
processes must reconnect to load the updated code. These checks launch a new MCP
process and do not claim to replace code already loaded in an older process.

A clean native Linux source checkout and new virtual environment at candidate
`49a739e` also passed the published source installation, release-mode Rust build,
preflight, sandbox walkthrough, and public MCP live smoke test. This used the same
WSL2 host, not a new operating-system installation. It installed no optional Jev
SDK or credential, and verified actor and world-settings restoration. The exact
commands and results are retained in `target/fresh-install-validation.json`.
The checked-in no-key Python example also completed from that environment and
restored actors/settings; see `target/programmatic-baseline-live.json`.

## Verified local results

- `make check` passed lint, typing, complexity/maintainability, Rust formatting,
  Clippy, build, **7 Rust unit tests, 2 guardian integration tests, and 532 Python
  tests**. Two Python tests were skipped: the explicitly configured live Jev check
  and the platform rejection test that requires a non-WSL host. Ruff formatting
  checked 162 files. Both versioned example specifications validate.
- Dedicated CARLA checks passed public MCP driving/image/resource access, density
  ownership/revision/restart behavior, explicit sensor timing, persistent-session
  follow-ups, finite-script timeout cleanup, and destruction before a synchronous
  tick. Managed completion, cancellation, worker death, and supervisor death all
  verified owned-actor cleanup and world restoration.
- Live synchronous world/TM checks also validated the explicit density rejection
  policy. Rejected start/reconfiguration and idempotent shutdown made no TM calls,
  hidden ticks, actor changes or world-settings changes. The externally owned
  autopilot actors subsequently moved under 100 ticks from their sole owner.
  This verifies non-interference; synchronous density remains unsupported.
- The configured Jev Choice test passed separately from the offline quality gate.
  All six matched rules/Jev trials completed and cleaned up successfully. The
  comparison includes every declared trial and reports zero exclusions. Its small
  sample size and shared fixture do not establish improved realism or safety.
- The final no-key baseline completed as run
  `64215d7334fe4c78ae8f5532edb5cd14`. Its package-source fingerprint is
  `85f1aa3aae493397edb61b242e4cc7a0cf2b57619a372837d3fe5d1ed0ff4d68`.
  This identifies the tested Python implementation independently of documentation
  and commit metadata; it is not a substitute for the release candidate's CI.
- Dependency audits found no known vulnerabilities across 31 runtime Python pins,
  33 pins with the optional Jev dependency, and 48 Rust dependencies. All locked
  platform pins were included in advisory lookup.
- The final headless image passed ordinary and hardened preflight, native imports,
  unreachable-simulator handling, and forced-supervisor recovery. The hardened
  checks used a read-only filesystem, no network, no extra capabilities, and
  `no-new-privileges`. Its Python version is 3.12.15.
- Offline Trivy 0.75.0 scanning found **zero HIGH/CRITICAL vulnerabilities** in image
  `sha256:af84185c3b022224d1ea15b3d1cf497235ccd627503bd6a1db5111489c967010`
  on 2026-10-02. This is point-in-time advisory evidence, not a guarantee that the
  image contains no vulnerabilities.

The ignored local reports include `final-quality-evidence.json`,
`final-candidate-baseline.json`, `managed-supervisor-final-live-validation.json`,
`public-mcp-final-live-validation.json`, `finite-final-live-validation.json`, and
`final-container-security-audit.json` under `target/`. Red-test and failed-live-probe
evidence was retained. The release owner must retain the applicable reports with
the final candidate evidence; these machine-local reports are not bundled into
source distributions.
