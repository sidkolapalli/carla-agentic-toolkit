# Release readiness

This checkout is an experimental local toolkit candidate. It is not approved for
production use or a public release. The implementation work covers the current
open issue set; acceptance that requires external evidence remains explicit below.
No repository visibility change, release tag, PyPI upload, or hosted release has
been made by this validation work.

## Release acceptance gates

| Gate | Required evidence |
| --- | --- |
| Candidate and CI (#81, #82) | Commit the reviewed candidate and obtain a successful new hosted CI run on that exact commit. Local checks do not replace this. |
| Public release (#82) | Owner approval of the concrete candidate and visibility/publication changes, followed by a prerelease tag. Distribution remains a source checkout; the wheel does not bundle the Rust runner. |
| Security reporting (#82) | The repository is private; the private-vulnerability-reporting endpoint returned 404 on 2026-10-02. Configure and verify a public-release reporting route before publication. The owner's public profile currently lists no email or website fallback. |
| Live provider (#86) | Run the separately opted-in Jev check using the locally configured credential. Its variable name or configuration path has not yet been identified; no secret value should be pasted into a chat or report. |
| Matched evaluation (#87) | Save actual rules and Jev trials with matching fixture, seeds, timing, environment, planner/controller and code versions. Generate the strict comparison report from those traces. Offline adapter tests and rules-only runs are insufficient. |
| Follow-on article (#87) | Read the current original article before preparing its follow-up. Its URL or file path is still required. |
| Demo (#87) | Record a successful run and a failure/fallback only after the corresponding final validation gates pass. Retain machine-readable evidence alongside the video. |

## Delivered implementation

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
  fallback. Live-provider acceptance remains pending above.
- **#87:** CLI/MCP lifecycle controls and static matched-comparison tooling are
  implemented. The completed comparison, article and video remain separate gates.
- **#81, #82:** the workflow pins, full-history scan, source packaging, container
  runtime, and local release checks have been repaired. Publication remains gated.

## Reproduction

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

The configured desktop MCP entry still points to a separate WSL source checkout.
These checks explicitly launch this candidate's environment; they do not update
that configured checkout or certify its older code. Sync and validate the reviewed
candidate there before recording its final demo.

## Verified local results

- `make check` passed lint, typing, complexity/maintainability, Rust formatting,
  Clippy, build, **7 Rust unit tests, 2 guardian integration tests, and 527 Python
  tests**. Two Python tests were skipped: the explicitly configured live Jev check
  and the platform rejection test that requires a non-WSL host. Ruff formatting
  checked 160 files. Documentation checks validated 35 local links and both specs.
- Dedicated CARLA checks passed public MCP driving/image/resource access, density
  ownership/revision/restart behavior, explicit sensor timing, persistent-session
  follow-ups, finite-script timeout cleanup, and destruction before a synchronous
  tick. Managed completion, cancellation, worker death, and supervisor death all
  verified owned-actor cleanup and world restoration.
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
