# Open Issue Implementation Status

Updated: 2026-10-09. Scope: the 51 open issues retrieved from GitHub for this
repository. This implementation ledger distinguishes code verification,
publication, and issue closure; it does not claim all GitHub issues are resolved.

Behavior changes use a failing regression, a focused green run, and the repository
quality gate before the next implementation. Documentation-only corrections do
not need artificial tests. Live and independent external acceptance evidence is
reported separately; it must not be replaced by mocks or manufactured receipts.
Historical receipts and media under `docs/evidence/` remain as recorded;
explanatory wording corrections do not alter their observations.
The user approved publishing verified fixes through a PR to `develop`, with
issue closure after merge and completed acceptance checks. Independent fixes may
now proceed in separate worktrees, retaining a TDD cycle and gate per issue.
Provider calls remain disabled; #87 is explicitly skipped for now.

## Verification

- Integration base: fetched `origin/develop` at `a43994f`. Verified changes are
  published through draft [PR #166](https://github.com/sidkolapalli/carla-agentic-toolkit/pull/166)
  to `develop`; referenced issues remain open until merge and their own completed
  acceptance checks. The initial checkpoint's branch-policy and Linux-quality
  CI checks pass. No merge or review bypass is authorized.
- #120: full `make check` passed, 772 Python tests passed, two skipped, nine Rust
  tests passed. Live checks remain pending.
- #121: four reported cleanup paths and five additional unsafe-release cases
  reproduced as failing tests before their fixes; 72 focused tests pass. The full
  gate passed with 803 Python tests, two skips, and nine Rust tests.
- #160: two listener regressions failed before the method/boolean fix; 46 focused
  tests pass. Full gate passed with 807 Python tests, two skips, and nine Rust
  tests. Optional live warning check remains pending.
- #127: sensor and managed cleanup regressions, episode races, and invalid
  acknowledgements failed before their fixes; 115 focused tests pass. Full gate
  passed with 836 Python tests, two skips, and nine Rust tests. Live checks remain
  pending.
- #137: finite-demo wording corrected; README/docs phrase search, relative-link
  existence checks, and `git diff --check` pass. No behavior changed.
- #136: native creation, termination, concurrent intent, bounded RPC, and pending
  recovery regressions reproduced before their fixes; 217 focused tests pass.
  Full gate passed with 907 Python tests, two skips, and nine Rust tests. Live
  CARLA validation remains pending.
- #128: 30 input-error and ownership-preflight regressions demonstrated RED;
  85 focused tests pass. Full gate passed with 938 Python tests, two skips, and
  nine Rust tests. Live typo/unknown-blueprint checks remain pending.
- #132: eight regressions failed at the redundant managed autopilot calls before
  removal. Focused managed-fixture/cleanup tests and independent review pass.
  Full gate passed with 942 Python tests, two skips, and nine Rust tests. The live
  no-sidecar port-8000 check remains pending.
- #147: clarified the 23 `traffic.*` actors as signs and lights, matching the
  category implementation. Diff checks pass; historical receipts/media unchanged.
- #148: README/evidence labels identify wall-clock, asynchronous, variable-step
  endpoint displacements as illustrative and non-repeatable, not measurements.
  Checked against the unchanged motion receipt; rehearsal figures remain separate.
- #158: added CARLA-specific MIT/CC-BY attribution and linked preview GIF credits,
  verified against both upstream release notices. Relative link and diff checks pass.
- #138: 18 timing regressions failed before the shared mode guards and frame-backed
  wait implementation; 131 focused tests pass. The frozen full gate passed with
  967 Python tests, two skips, and nine Rust tests. Live tick-cue checks remain pending.
- #142: covered by the map-error RED/fix in #136. Four additional native-exception
  contract cases verify the exact reload failure, retained ownership, and subsequent
  same-episode sensor cleanup; 52 focused tests pass. Full gate passed with 971
  Python tests, two skips, and nine Rust tests. No additional production change.
- #125: 23 regressions failed before explicit lifecycle flags, non-ticking batch
  defaults, and observed mode metadata; 258 focused tests pass. Full gate passed
  with 1,005 Python tests, two skips, and nine Rust tests. Live CARLA checks pending.
- #130: 49 guard/runtime and 24 journal regressions demonstrated RED before their
  fixes. Sparse per-setter evidence, legacy recovery, async-only guards, and real
  SIGKILL recovery pass; 367 integration tests pass. The frozen full gate passed
  with 1,096 Python tests, two skips, and nine Rust tests. Live sidecar checks pending.
- #139: 34 close/recovery regressions demonstrated RED before strict six-field
  replacement verification; 62 focused tests pass. Unknown-episode mutations are
  refused and mismatches stay dirty, rather than adopting the suggested blind
  restoration. Full gate passed with 1,133 Python tests, two skips, and nine Rust
  tests. Live settings-preserving reload validation remains pending.
- #149: implemented the issue's permitted journaling-first alternative: retained
  per-vehicle spawning and corrected the controller's process documentation.
  Independent review confirms no native batch bypass or performance claim.
  Existing #136 TDD coverage and the full gate pass: 1,133 Python tests, two skips,
  and nine Rust tests. No artificial RED for documentation-only changes; the live
  30-vehicle ownership/autopilot check remains pending.
- #141: 66 standalone and 24 managed regressions demonstrated RED before
  independent version diagnostics and durable pre-startup managed validation.
  253 native and 78 managed focused checks pass. The frozen full gate passed with
  1,233 Python tests, two skips, and nine Rust tests. Live incompatible-version
  acceptance remains pending; optional fixture-to-engine binding was not guessed.
- #157: 20 listener and 16 wrapper regressions demonstrated RED before retained
  original-handle stop acknowledgements and episode checks; an independently found
  inherited capture-cache regression also failed before its guard. 155 focused
  checks pass. Full gate passed with 1,277 Python tests, two skips, and nine Rust
  tests. A Windows-native CARLA 0.9.16 GNSS probe confirms two distinct client
  handles, original-listener stop before non-ticking destruction, successful final
  close, and unchanged settings/unrelated actor IDs. This is not Linux sandbox
  or crash-recovery acceptance.
- #159: nine managed recovery regressions demonstrated RED with four passing
  safety controls before the shared mode-aware snapshot fix. 79 managed and 52
  script recovery checks pass. Full gate passed with 1,304 Python tests, two
  skips, and nine Rust tests. A Windows-native asynchronous helper wait passes;
  a subsequent dedicated CARLA 0.10.0 Linux/WSL check now passes actual SIGKILL
  and real supervised recovery in both asynchronous crash windows: before the
  first settings write and after verified restoration but before lease cleanup.
  Fresh dirty-state readers retain the journal, recovery delivers a newer frame,
  and the same episode, six typed settings, and exact baseline actor IDs are
  restored. Both worker and recovery groups are dead; the same lease is clean
  and reacquirable. This is trusted managed-worker, not script-sandbox evidence.
- #141 follow-up: a genuine incompatible Windows-native client's world handshake
  exits before health can publish its warning. 23 health and five served-prompt
  regressions demonstrated RED before version-only diagnostics and conditional
  guidance; 120 health/managed and 28 server checks pass. Native Windows checks
  now return complete matching 0.9.16 health and version-only health from a
  genuine 0.10.0 client against that server, with zero world-inspection calls.
  No incompatible-world handshake is retried. Full gate passed with 1,327 Python
  tests, two skips, and nine Rust tests. Linux managed-startup acceptance is separate.
- #161: 92 rendering regressions and three newly introduced read/creation episode
  races demonstrated RED before the camera-only guards. The dedicated Windows
  CARLA 0.9.16 check refuses camera attachment, capture, streams, subscription,
  and screenshots in no-rendering mode while GNSS still delivers. Listener and
  actor cleanup work in that mode; original settings and unrelated actor IDs are
  restored. This is not Linux sandbox acceptance. 313 focused integration tests
  pass. The frozen full gate passed with 1,486 Python tests, two skips, and nine
  Rust tests.
- #165: 27 reporting and 27 density regressions demonstrated RED, with eight
  passing controls, before six-field reporting and explicit fatal frame waits.
  A read-only native CARLA 0.9.16 regression first confirms the omitted fields,
  then passes with all six health/world-state values matching native settings.
  Actor IDs and settings remain unchanged. 149 reporting and 123 density focused
  checks pass. The frozen full gate passed with 1,548 Python tests, two skips, and
  nine Rust tests; this native reporting check does not establish
  live density-timeout or Linux sandbox acceptance. Added regressions also catch
  post-failure episode relabelling and secondary callback failures that otherwise
  replace the fatal wait with a generic retry.
- #152: 27 focused regressions demonstrated RED with 17 passing controls. Native
  read-only checks also fail before the fix on both releases: 27 selected versus
  23 native cars on 0.9.16, and seven selected versus six native cars on 0.10.0.
  All 11 observed 0.10.0 vehicle blueprints expose `base_type`, with values `car`,
  `truck`, `bus`, and the empty string. Post-fix native checks pass with sorted
  car-only selection, unchanged unrestricted inventories, settings, episode, and
  actor IDs. 44 focused and 183 integration checks pass. The frozen full gate
  passed with 1,592 Python tests, two skips, and nine Rust tests.
- #131: 19 specification/metadata and 29 comparison/demo regressions demonstrated
  RED before canonical repetition labels and consistency checks. The 115 new
  cases include passing safety controls and eight additional GREEN-only coverage
  cases; they are not all claimed as RED. 291 managed and 197 reader integration
  checks pass, as do all ten example-spec validations. The frozen full gate
  passed with 1,707 Python tests, two skips, and nine Rust tests. Historical trace,
  comparison JSON/HTML, and media bytes are unchanged. Renderer CLI imports pass
  in the locked project environment; isolated offline script execution cannot
  resolve the uncached pinned Pillow dependency, and no install was attempted.
- #162: the conditional live-first check passes on a dedicated Windows-native
  CARLA 0.9.16 server: 30 fresh-camera frames in each verified bright/dark scene,
  real first/last image inspection, no drops, and first-to-last encoded mean
  brightness changes below 0.02 on a 0-255 scale. No runtime fix is justified by
  these samples. The [measurement and limits](first-frame-exposure-check.md)
  describe the unchanged first-frame capture contract. All weather/settings,
  episode, spectator, and actor baselines are restored. The 0.10.0 map reports
  native weather disabled; its two no-camera refusals were explicitly recovered
  after read-only verification of the same dirty leases. No private receipts or
  images were published, and no artificial TDD regression was added for this
  evidence/documentation-only result.
- #146: ten regressions demonstrated RED with two passing controls before the
  exact-boolean `additional_data` option; 63 focused tests pass. Its isolated
  full gate passes with 1,719 Python tests, two skips, and nine Rust tests. Native
  CARLA 0.9.16 records velocity, bounding-box, and physics data and accepts full
  replay. Recording includes the authoritative owned vehicle deletion; recorder
  and replay stop, and original actors, episode, six settings, weather, and
  spectator are verified. An earlier replay remapped an actor and retained its
  dirty journal; only the verified dedicated server was decommissioned, with a
  verified replacement baseline before explicit journal recovery. That failed
  receipt is retained, not described as same-episode restoration. The optional
  managed-recorder feature was not added. This is trusted Windows-native, not
  Linux sandbox, evidence.
- #145: eleven regressions demonstrated RED with one passing control; 32 focused
  tests pass. One snapshot supplies transform, velocity, acceleration, frame,
  elapsed time, and speed, with missing snapshot actors explicitly rejected.
  Controls and ancillary actor values remain separate reads. Its isolated full
  gate passes with 1,719 Python tests, two skips, and nine Rust tests.
- #151: nine regressions demonstrated RED with 13 passing controls; 82 focused
  integration tests pass. Canonical `desired_speed_kmh`, strict deprecated input,
  and documented TM preset/profile override semantics are implemented. Its
  isolated gate passes with 1,727 Python tests, three skips, and nine Rust tests;
  the extra collection skip is its absent optional SDK, not a provider call.
  The integrated environment restores those two network-free SDK tests. A real
  CARLA 0.9.16 owned vehicle uses the cautious preset and 36 km/h target, then two
  actual maintenance passes. Snapshot speeds are 4.9036 and 6.2627 m/s; they do
  not alone prove internal TM target state or steady-state speed. Autopilot is
  disabled, the owned vehicle is authoritatively deleted, exact original actor,
  episode, settings, weather, and spectator baselines are verified, and the
  process-owned TM port closes. No provider or Linux sandbox claim is made.
- Integrated #145/#146/#151 gate: 1,753 Python tests passed, two skipped, nine
  Rust tests passed; Ruff/format, Ty, Radon CC/MI all A, Rustfmt, Clippy, and
  Rust build pass. Independent issue commits are preserved.
- #129: eight regressions demonstrated RED with one passing legacy control;
  19 focused navigation/API tests pass. `follow_waypoints` names the greedy
  traversal accurately; the deprecated `generate_route` alias remains available.
  Results report final Euclidean distance and arrival within one step, including
  early stopping when already near the destination. Full gate passes with
  1,762 Python tests, two skips, and nine Rust tests. A matching native CARLA
  0.10.0 read-only Town10 query crosses actual junctions, reaches its 200-waypoint
  budget, and correctly reports not reached with 65.0974 metres remaining.
  An independent final-waypoint distance agrees; actor, episode, six settings,
  weather, and spectator baselines are unchanged. This is not Linux sandbox
  evidence or a complete-route-planning claim.
- #140: twenty regressions demonstrated RED with two passing legacy controls;
  164 focused tests pass. Native box offsets, all three extents, and the composed
  actor/local box rotations drive projected route and merge clearances; actor
  control/progress origins are unchanged. Its isolated gate passes with 1,729
  Python tests, two skips, and nine Rust tests. Actual supported fixture boxes
  were measured on CARLA 0.9.16 and 0.10.0, then checked against all eight native
  world vertices within 50 micrometres. Local horizontal offsets are 0.029219 m
  and 0.006216 m, respectively, not clearance-error guarantees. Three tilted
  cases also agree with native LibCarla corners; those are local value-object
  checks, not live tilted-vehicle measurements. Owned vehicles were deleted and
  all native baselines verified. Historical traces and media are unchanged.
- #126: 28 failures reproduced across the initial and supplementary TDD phases;
  42 new alias cases and 59 focused checks pass. The isolated full gate passes
  with 1,749 Python tests, two skips, and nine Rust tests. A real CARLA 0.9.16
  vehicle was named and resolved while absent from the cached snapshot, with no
  tick or frame change. After verified cleanup and an actual dedicated-server
  restart, a spectator with the same ID, type, and role was rejected by episode
  mismatch; the old vehicle alias was rejected too. Both simulator baselines
  are verified clean. These are Windows-native, not persistent Linux sandbox,
  acceptance checks.
- #123: 31 failures and eight passing controls preceded the original correction;
  303 focused tests and its isolated 1,746-test Python gate pass, with two skips
  and nine Rust tests. Native acceptance then exposed a 525,488-byte 640x360 PNG,
  above the 524,288-byte publication limit. A supplementary regression genuinely
  failed before choosing a 480x270 default; explicit caller sizes remain intact.
  This intentionally differs from the issue's proposed 640x360 and requires
  review of that acceptance adjustment. All 41 focused evidence/publication
  tests pass. The real repeat drains both LiDAR types to PLY and depth to PNG;
  a separate converted depth copy is published without replacing the raw PNG.
  Its default screenshot is 304,485 PNG bytes and 406,269 combined output bytes.
  Failed probe receipts remain intact; their journals were recovered only after
  explicit server-ID absence and exact same-episode baseline verification. The
  successful probe verifies all baseline state and clears its ownership journal.
  Native writers are checked for acknowledgement/file existence, not fsync
  durability. This is trusted Windows-native, not Linux sandbox, evidence.
- Integrated #129/#140/#126/#123 gate: 1,867 Python tests passed, two skipped,
  nine Rust tests passed; all required static checks pass. The combined adapter
  initially crossed the existing maintainability threshold; moving unchanged
  methods into its existing mixins restored the all-A gate. Existing timing
  tests retain their assertions with mocks at the relocated method boundary.
- #153: 26 regressions demonstrated RED with five controls; 110 focused tests
  pass. The static showcase guards health/version/catalog/color and reports
  CARLA 0.10.0's fixed-daylight limitation without connecting during retrieval.
  Its isolated full gate passes with 1,738 Python tests, two skips, and nine
  Rust tests. Both matching native releases return actual weather readback.
  On 0.10.0, all three requested fields remain unchanged and weather is disabled;
  no rendered-weather claim is made. Original weather, actors, episode, six
  settings, and spectator are verified after restoration on both servers.
- Integrated weather gate: 1,898 Python tests passed, two skipped, nine Rust
  tests passed; Ruff/format, Ty, Radon CC/MI all A, Rustfmt, Clippy, and build pass.
- The user authorized CARLA startup for testing. A dedicated CARLA 0.9.16 process
  was started on RPC 3400 with separate pinned client runtimes. Windows-native
  health confirms matching 0.9.16 releases and no warnings; a genuine 0.10.0 client
  against that server produces the mismatch warning and compatibility rejection
  without requesting a world. This is not a Linux managed-startup trace receipt.
  Existing explicit CARLA 0.9.16 firewall blocks prevent WSL access to that
  executable; no firewall rule or tool permission was changed. The dedicated
  0.9.16 server was stopped after its initial checks and later restarted on the
  same reserved ports for journaled native acceptance. A separate CARLA 0.10.0
  server on RPC 3500 is reachable from WSL: matching-client read-only health
  confirms Town10HD_Opt, all six settings, no warnings, and no vehicles, walkers,
  or sensors. #159's separate managed worker-death checks now pass as described
  above. The quarantined WSL
  loopback:2000 endpoint remains untouched.
- #87 is skipped at the user's request; #82 requires independent reporting
  verification. #152's native inventory and pre/post checks are complete on
  CARLA 0.9.16 (Windows) and 0.10.0 (WSL); its full gate passes.
  #162's live comparison did not reproduce a material brightness difference in
  the tested 0.9.16 setup; no default warmup or manual-exposure fix was applied.

## Native Safe-Filter Lists

Read-only #152 checks on 2026-10-09, using matching native clients and dedicated
Town10HD_Opt servers. `safe_filter=False` retains the complete sorted inventory:
41 blueprints on CARLA 0.9.16 and 11 on 0.10.0. No actors or settings were changed.

CARLA 0.9.16, `safe_filter=True` (23):

```text
vehicle.audi.a2
vehicle.audi.etron
vehicle.audi.tt
vehicle.chevrolet.impala
vehicle.citroen.c3
vehicle.dodge.charger_2020
vehicle.dodge.charger_police
vehicle.dodge.charger_police_2020
vehicle.ford.crown
vehicle.ford.mustang
vehicle.jeep.wrangler_rubicon
vehicle.lincoln.mkz_2017
vehicle.lincoln.mkz_2020
vehicle.mercedes.coupe
vehicle.mercedes.coupe_2020
vehicle.micro.microlino
vehicle.mini.cooper_s_2021
vehicle.nissan.micra
vehicle.nissan.patrol
vehicle.nissan.patrol_2021
vehicle.seat.leon
vehicle.tesla.model3
vehicle.toyota.prius
```

CARLA 0.10.0, `safe_filter=True` (6):

```text
vehicle.dodge.charger
vehicle.dodgecop.charger
vehicle.lincoln.mkz
vehicle.mini.cooper
vehicle.nissan.patrol
vehicle.taxi.ford
```

## Issue Ledger

| Issue | Work | Status |
| --- | --- | --- |
| [#26](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/26) | Support bounded CARLA sessions and managed closed-loop experiments | Pending optional synchronous-density work; external child prerequisites remain. |
| [#82](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/82) | Validate and publish the source-only experimental alpha with release evidence | External verification pending: independent private-report submission and receipt. |
| [#87](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/87) | Expose managed experiment controls and publish a reproducible rules-versus-Jev demo | Skipped for now at the user's request; original article input remains unavailable. |
| [#94](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/94) | Exercise Jev along a route with controlled traffic hazards | Pending genuine UE5 live scenario validation; provider calls explicitly disabled by the user. |
| [#96](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/96) | Fix UE5 pedestrian hazard motion and verify physical scenario completion | Pending UE5 walker diagnostics and physical-completion validation. |
| [#120](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/120) | Restore world and Traffic Manager settings after script and persistent-session runs instead of marking the lease clean | Implemented; full gate passed; live CARLA validation pending. |
| [#121](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/121) | Count already-destroyed actors as cleaned up and release successful batch destroys from the journal | Implemented; full gate passed; live validation pending. |
| [#122](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/122) | Give the vehicle under test role_name hero and stop calling the other merge car ego | Pending implementation. |
| [#123](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/123) | Save and publish sensor evidence with correct file types and keep the result when publication fails | Implemented; TDD, native file/publication checks, and full gate pass; review evidence-backed 480x270 default instead of proposed 640x360. |
| [#124](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/124) | Bound sensor queue memory and encoding cost by image size and sensor count | Pending implementation. |
| [#125](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/125) | Align load_world, reload_world and apply_batch defaults with CARLA or make them explicit | Implemented; full gate passed; live CARLA validation pending. |
| [#126](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/126) | Bind actor aliases to the episode and check liveness against the server | Implemented; TDD, actual pre-tick naming and post-restart rejection, and full gate pass. |
| [#127](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/127) | Fall back to a batch destroy for sensors and managed actors when destroy() returns False | Implemented; full gate passed; live CARLA validation pending. |
| [#128](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/128) | Return structured errors for unknown blueprints, attributes and sensor kinds | Implemented; full gate passed; live CARLA validation pending. |
| [#129](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/129) | Report whether generate_route reached its destination, or rename it to follow_waypoints | Implemented permitted greedy-follower naming and honest arrival results; TDD, native junction check, and full gate pass. |
| [#130](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/130) | Reject synchronous Traffic Manager requests and unguarded autopilot traffic that the sidecar cannot step | Implemented; full gate passed; live sidecar validation pending. |
| [#131](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/131) | Make managed experiment seeds vary the initial condition, or rename them as replicates | Implemented locally: canonical replicate index, strict legacy input, and identical-initial-condition warnings; TDD and full gate pass. |
| [#132](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/132) | Do not create a Traffic Manager in the managed worker by calling set_autopilot(False) | Implemented; full gate passed; live CARLA validation pending. |
| [#133](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/133) | Reload the world and reset traffic lights before each managed repetition | Pending implementation. |
| [#134](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/134) | Seed, settle and stop AI walker controllers as CARLA's walker lifecycle requires | Pending implementation. |
| [#135](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/135) | Wait for periodic sensor data by default and stop waiting once a later frame arrives | Pending implementation. |
| [#136](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/136) | Journal spawned actors as each is created and decouple the per-RPC timeout from the script budget | Implemented; full gate passed; live CARLA validation pending. |
| [#137](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/137) | Describe the alpha demo as a step-by-step procedure, not a reproducible result | Implemented; documentation checks pass. |
| [#138](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/138) | Guard tick, tick_n, watch_actor and api.wait by synchronous mode | Implemented; full gate passed; live CARLA validation pending. |
| [#139](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/139) | Do not mark the managed lease clean after a world replacement without checking settings | Implemented fail-closed verification policy; full gate passed; live validation pending. |
| [#140](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/140) | Use bounding_box.location and rotation in ground-truth boxes and clearance metrics | Implemented; TDD, both native fixture/corner checks, and full gate pass; historical evidence unchanged. |
| [#141](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/141) | Surface CARLA client/server version mismatches in toolkit warnings | Implemented locally, including native-verified version-only health; TDD and full gate passed. |
| [#142](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/142) | Convert reload_world RuntimeError into the structured reload_world_failed result | Covered by #136; failure/retention/cleanup contract and full gate pass. |
| [#143](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/143) | Explain missing Traffic Manager servers and unimportable CARLA APIs in error messages | Pending implementation. |
| [#144](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/144) | Let persistent sessions use a non-default Traffic Manager port | Pending implementation. |
| [#145](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/145) | Read script telemetry from one world snapshot and report its frame | Implemented: frame-coherent motion and explicit missing-state errors; TDD and full gate pass. |
| [#146](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/146) | Expose recorder additional_data and optionally record managed runs with the CARLA recorder | Implemented required recorder option; TDD, native record/replay, and full gate pass; optional managed recorder not added. |
| [#147](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/147) | Clarify that the 23 remaining traffic actors in the camera demo are signs and lights | Implemented; documentation checks pass. |
| [#148](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/148) | Label the bolded drive-demo distances as wall-clock, asynchronous and not repeatable | Implemented; documentation checks pass. |
| [#149](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/149) | Batch traffic spawning and autopilot with SpawnActor.then(SetAutopilot), and fix the stale controller docstring | Implemented permitted per-vehicle safety alternative; full gate passed; live validation pending. |
| [#150](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/150) | Reduce script API divergence from CARLA's names and argument order | Pending implementation. |
| [#151](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/151) | Name Traffic Manager presets and desired_speed units unambiguously | Implemented naming and permitted profile-interaction documentation; TDD, native two-pass speed read, and full gate pass. |
| [#152](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/152) | Filter safe traffic vehicles by base_type instead of a name list | Implemented locally: exact native car classification with legacy fallback; TDD, both native inventory checks, and full gate pass. |
| [#153](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/153) | Read weather back from CARLA and make the showcase prompt engine-aware | Implemented; TDD, both native readback/restoration checks, and integrated full gate pass. |
| [#154](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/154) | Support a configured default CARLA host and a stable lease key under WSL2 | Pending implementation. |
| [#155](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/155) | Drop the cached CARLA client in persistent sessions after a simulator restart | Pending implementation. |
| [#156](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/156) | Allow non-default CARLA streaming and secondary ports in the sandbox | Pending implementation. |
| [#157](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/157) | Close a script's subscription before destroying an inherited sensor | Implemented locally: original-handle unsubscribe, retry acknowledgement, and same-episode guards; full gate passed. |
| [#158](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/158) | State CARLA's MIT and CC-BY licences in the README credits | Implemented; documentation checks pass. |
| [#159](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/159) | Make managed recovery's fresh-snapshot check work in an asynchronous world | Implemented locally: shared mode-aware fresh-snapshot recovery; TDD and full gate passed. |
| [#160](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/160) | Call Sensor.is_listening as a method | Implemented; full gate passed; optional live warning check pending. |
| [#161](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/161) | Refuse or warn when attaching cameras in no-rendering mode | Implemented locally; TDD, native rendering check, and full gate pass. |
| [#162](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/162) | Let camera auto-exposure settle before keeping a one-shot capture | Investigated locally: not reproduced in the supported live bright/dark check; unchanged capture contract and limitations documented. |
| [#163](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/163) | Reuse SensorSubscription in the demo capture script | Pending implementation. |
| [#164](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/164) | Add ground-truth helpers and richer image digests for perception tests | Pending implementation. |
| [#165](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/165) | Report substepping settings and stop swallowing density wait_for_tick timeouts | Implemented locally: six-field reporting and fatal frame waits with lifecycle evidence; TDD, native reporting check, and full gate pass. |
