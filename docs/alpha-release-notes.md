# Experimental alpha release notes

This is an unpublished source-checkout candidate for the local CARLA toolkit,
tracked in [#82](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/82).
It is not a production release. Build the Rust runner from the checkout; do not
publish the Python wheel, which does not bundle that runner
([#52](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/52)).

The merged implementation and evidence at
[`d891f4df124ba8b7117f5e02afc466338f9c0e09`](https://github.com/sidkolapalli/carla-agentic-toolkit/commit/d891f4df124ba8b7117f5e02afc466338f9c0e09)
passed [main CI run 37130509549](https://github.com/sidkolapalli/carla-agentic-toolkit/actions/runs/37130509549).
Its byte-identical pre-merge candidate passed 579 Python tests, one explicitly
configured live-provider skip, and nine Rust tests in
[CI run 37129889937](https://github.com/sidkolapalli/carla-agentic-toolkit/actions/runs/37129889937).
Main CI also passed the quality, dependency, history, package, hardened-container,
and container-vulnerability gates. [PR #88](https://github.com/sidkolapalli/carla-agentic-toolkit/pull/88)
contains the implementation; [PR #89](https://github.com/sidkolapalli/carla-agentic-toolkit/pull/89)
adds the recorded demo and independent reproduction. Any later changes need
their own successful CI run before a release is tagged.

## Changes in this candidate

- Controller shutdown and restart track the real worker lifetime; the latest
  requested update cannot be overwritten by an older in-flight update
  ([#42](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/42),
  [#77](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/77)).
- Density reduction destroys controller-created vehicles only. Adopted or
  pre-existing vehicles are preserved, and an impossible target reports a
  conflict ([#44](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/44)).
- World advancement failures remain visible, and partially created actors remain
  tracked for cleanup ([#78](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/78)).
- Bounded sensor subscriptions support owner-driven synchronous ticks, frame
  selection, event sensors, delivery delays, dropped samples and trailing events.
  Synchronous density maintenance is rejected before mutation
  ([#79](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/79),
  [#80](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/80)).
- Ownership journals bind actor IDs to the CARLA episode. Cleanup does not infer
  that a newly spawned actor is absent from an older cached snapshot. Process
  guardians and recovery checks cover interrupted scripts and trusted workers.
- The finite MCP smoke test checks manual driving, a published image and resource
  read, script rejection, timeout handling and restoration. It needs no Traffic
  Manager sidecar. The repaired CI audits the hashed locked dependencies without
  trying to install the editable project ([#81](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/81)).

The candidate also includes opt-in persistent script sessions and managed
experiments. Those have separate lifecycle and evidence contracts; see
[persistent sessions](persistent-sessions.md) and
[managed experiments](managed-experiments.md). The optional Jev adapter passed its
configured live provider check and a [six-trial matched rules/Jev evaluation](evidence/merge-comparison-2026-10-02/README.md).
All six trials completed with verified cleanup; the small descriptive comparison
does not establish improved safety or realism. A
[recorded success/fallback demo](evidence/managed-demo-2026-10-03/README.md) and an
[independent automated reproduction](evidence/independent-reproduction-2026-10-03/README.md)
are now retained. The article still requires review against the original under the separate
[#87](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/87) milestone.
These managed results are separate from the existing-toolkit alpha's finite demo
and release conditions.

## Reproduce the local demo

Follow the [alpha demo guide](alpha-demo.md) for a fresh checkout, the offline
sandbox walkthrough, and a self-cleaning live MCP run. Preserve its commit,
preflight and JSON reports alongside any recording. The
[release-readiness record](release-readiness.md) distinguishes live local
evidence from hosted CI and lists the remaining release gates.

Local validation used CARLA server/Python API 0.9.16, Python 3.12.14 and Ubuntu
26.04 under WSL2, kernel `6.18.33.2-microsoft-standard-WSL2`, with the simulator
on the Windows host. This validates that specific WSL2 path. It does not certify
every Windows client, an older configured checkout, WSL1, or native Windows
sandboxing. The final local headless image used Python 3.12.15 and had no
HIGH/CRITICAL findings in its recorded offline Trivy scan; that is point-in-time
evidence at those severities.

A new native-Linux checkout and virtual environment at the pinned implementation
commit passed installation, a release Rust build, preflight, the sandbox
walkthrough and the live MCP smoke, with actors and settings restored. This was
performed on the existing WSL2 host; it was not a newly provisioned operating
system. The local report is `target/fresh-install-validation.json`.

## Known limitations and release conditions

- Local stdio only; remote HTTP transport and multi-user service deployment are
  outside this release ([#28](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/28)).
- Landlock ABI V7 must be fully enforced. Unsupported kernels fail closed.
  The simulator lease coordinates cooperating local processes and cannot stop an
  unrelated CARLA client from changing the world.
- Successful finite scripts retain their created actors unless they explicitly
  destroy them or call `cleanup_owned_actors()`. Error recovery is bounded and can
  report incomplete cleanup if the simulator cannot be reached; inspect that
  result before continuing. The demo explicitly cleans its own actors.
- Background density is asynchronous only. Synchronous camera use requires the
  explicit subscription/tick/drain/close sequence. Non-ticking recorder replay
  is unsupported; see [sensor timing](sensor-timing.md).
- Autopilot and traffic tuning require an existing trusted Traffic Manager
  outside the sandbox. The finite demo uses manual control instead. Published
  images remain limited to four images, 512 KiB each and 1 MiB combined encoded
  output.
- The headless image has no shell or package manager and includes default
  dependencies only. Optional Jev setup uses a source checkout.
- MIT licensing, [contribution guidance](../CONTRIBUTING.md), and the
  [security policy](../SECURITY.md) remain in place. The accepted history decision
  in [#51](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/51)
  is preserved. No visibility change, history rewrite, publication or tag is part
  of these notes.
- Before publication, the owner must authorize the concrete candidate and verify
  a working vulnerability-reporting route. The currently private repository's
  route has not been verified for public reporting. A passing CI run alone does
  not satisfy these release conditions.
