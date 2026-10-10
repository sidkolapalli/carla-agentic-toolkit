# Open Issue Implementation Status

Updated: 2026-10-10. Scope: the 51 open issues retrieved from GitHub for this
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
  nine Rust tests. Native supplementary verification is recorded below.
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
  967 Python tests, two skips, and nine Rust tests. Native tick-cue acceptance is
  recorded below.
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
- #163: the recorder regression failed in five cases before subscription reuse;
  the historical-reader phase failed in fourteen cases, with passing controls
  retained. All 134 focused checks and its isolated 1,729-test gate pass, with
  two skips and nine Rust tests. Raw BGRA pixels are hashed before PNG writing;
  bounded queue/drop accounting and trailing Stop deliveries use the shared
  subscription. Labelled receipts verify decoded BGRA, including alpha, using
  real Pillow; unlabelled historical receipts still verify PNG bytes. Historical
  receipts and media are unchanged.
- #124: twenty regressions demonstrated RED with two controls; 336 focused tests
  and its isolated 1,768-test gate pass, with two skips and nine Rust tests.
  Actual camera dimensions reserve a shared 512 MiB pending-queue budget before
  Listen, with byte-counted overflow and released reservations. Metadata drains
  do not encode merely because output_dir is supplied; saving requires explicit
  save_frames=True. Returned one-shot frames have a separate cap: neither cap is
  a whole-process RSS guarantee. A trusted Linux/WSL CARLA 0.10.0 1080p probe
  performs two 64-tick phases, each with 32 queue-fill ticks and 63 native samples.
  Metadata without/with output_dir writes zero files; sampled RSS peaks are
  320,847,872/329,068,544 bytes and measured process CPU deltas are 0.653/0.590 s.
  Original actors, episode, six settings, weather, and spectator are verified;
  the same lease is clean. This is not script-sandbox or RLIMIT acceptance.
- Integrated #163/#124 gate: 1,942 Python tests passed, two skipped, nine Rust
  tests passed; all required static checks pass. Native type and dimensions were
  added to an incomplete camera test double without removing its assertions.
- #144: the initial eighteen new contracts fail before implementation: six field
  and forwarding checks and twelve field-specific invalid-input diagnostics.
  The focused session/sandbox run passes; two additional boundary controls pass.
  Persistent configuration now freezes at most sixteen strict TCP connect ports
  and passes them to execution, the runner and worker IPC. No binding permission
  or default-port derivation was added. Full gate passes with 1,962 Python tests,
  two skips and nine Rust tests. A real matching CARLA 0.10.0 persistent sandbox
  connects to the dedicated trusted sidecar on port 8500. Landlock reports an
  enforced ruleset with that connect port, no actors are spawned and no TM global
  setters requested. Session close, exact native baseline, and the same clean
  lease are verified; the process-owned sidecar port closes after probe exit.
- #150: 46 regressions demonstrated RED with twenty controls; nine post-GREEN
  controls bring the new cases to 75. Its 183 focused checks and full gate pass:
  1,940 Python tests, two skips and nine Rust tests. Mutable JSON-backed value
  constructors, canonical attach_to/waypoint order, strict legacy aliases and
  reviewed native-operation discovery preserve validation and the import ban.
  Three actual LibCarla 0.9.16 transforms, vector order and independent defaults
  agree with constructor values and parsed native translations, without RPCs.
- #164: 62 regressions demonstrated RED across the initial and supplemental
  phases; all 88 new cases and 343 focused checks pass. Its isolated full gate
  passes with 2,008 Python tests, two skips and nine Rust tests. Explicit-origin
  nearest-first level bounds, snapshot-bound native actor vertices and actual
  camera intrinsics are read-only. Image digests include dimensions, contextual
  FOV and labelled raw-BGRA SHA; unknown FOV is null, and DVS/optical-flow behavior
  remains unchanged. Native server acceptance is separate from these tests.
- Integrated #144/#150/#164 gate: 2,125 Python tests passed, two skipped, nine
  Rust tests passed; all required static checks pass. An import-only conflict was
  resolved retaining both independent API surfaces and the sensor memory policy.
- #135: 30 regressions demonstrated RED across initial and arithmetic-boundary
  checks, with eight controls; all 38 new cases and 339 focused checks pass.
  Periodic drains default to two seconds, later-frame delivery wakes the waiter
  without relabelling future samples, and events remain nonblocking. Integral
  configured cadence is inferred only after two consistent native observations;
  unknown and contradicted history stays unknown. Its isolated gate passes with
  1,980 Python tests, two skips and nine Rust tests.
- #143: 19 mock-backed diagnostics regressions and two actual runner evidence
  regressions demonstrated RED; all 25 new cases pass. The runner supplies its
  actual enforced connect-port list after clearing parent environment. Excluded
  TM ports fail before CARLA access, recognized native bind failures have their
  own actionable error, and local import text is retained as
  `carla_api_unavailable`. No network rule, bind privilege or retry limit changes.
  Integrated #135/#143 gate passes with 2,188 Python tests, two skips and nine
  Rust tests, including all required static checks.
- #164 native supplement: the real 0.10.0 client returns a contiguous byte
  `memoryview`, not `bytes`. Nine regressions demonstrated RED with seven
  controls before supporting complete one-dimensional byte buffers without
  copying. Incomplete, noncontiguous, nonbyte and released views remain errors.
  All 142 focused checks and the full gate pass: 2,204 Python tests, two skips
  and nine Rust tests; required static thresholds remain unchanged.
- Combined #135/#164 native acceptance on the dedicated 0.10.0 WSL endpoint:
  twelve owner ticks delivered twelve 960x540 RGB samples and three GNSS samples
  using default periodic waits, with two confirmed no-sample-due GNSS frames.
  Drains issue no ticks or image writes. Reported raw-BGRA hashes match native
  memory, camera intrinsics match actual 80-degree attributes, and eight native
  vehicle vertices agree exactly with the selected snapshot. Spatial selection
  returns the nearest eight of 72 eligible bounds from 608 native pole boxes.
  The broader `Any` collection contains 36 native negative-half-extent boxes;
  its clear geometry error is retained, not normalized into invented bounds.
  Failed diagnostic receipts remain intact. Every attempt independently verifies
  authoritative owned deletion, original episode, actor inventory, six settings,
  weather and spectator restoration before the same lease is clean/reacquirable.
  This is trusted native-facade evidence, not a script-sandbox or provider claim.
- Published checkpoint `998c6ca` passes GitHub branch-policy and Linux-quality
  CI (run 38020695358). No issues are closed or PR merged by this checkpoint.
- #134: thirty lifecycle regressions demonstrated RED with two controls;
  205 focused checks pass. The isolated gate passes with 2,157 Python tests,
  two skips and nine Rust tests. Navigation seeding precedes native navigation
  queries; controller startup waits boundedly for walker publication, and every
  managed cleanup stops controllers before deleting walkers. Failed stops retain
  conservative ownership evidence. Native CARLA 0.9.16 creates two walker pairs
  in async mode and two in sync mode, then verifies deletion of all eight owned
  IDs and the original episode, actor inventory, six settings, weather and
  spectator. CARLA 0.10.0 refuses both attempted walker spawns; that failed
  receipt remains separate and its unchanged baseline and clean lease are
  verified. The navigation seed has no original-value getter; setting seed 42
  is recorded, not described as restored. The successful Windows-native check
  is not a Linux script-sandbox or UE5 physical-crossing acceptance claim.
- #156: sixty-four regressions demonstrated RED with three controls; all 67 new
  cases and 232 broader integration checks pass. The isolated gate passes with
  2,192 Python tests, two skips and nine Rust tests. Strict optional streaming
  and secondary ports replace their adjacent defaults in finite and persistent
  connect policies; no bind rule or environment permission is broadened.
  The verified dedicated CARLA 0.10.0 server was decommissioned and restarted
  with RPC 3500, streaming 3900 and secondary 3902. Its new episode and exact
  initial baseline are verified; this is replacement, not same-episode restore.
  Both actual Landlock one-shot and persistent executions receive three native
  GNSS samples, report only connect ports 3500/3900/3902/8000, delete their sensor,
  and verify original native state and the same clean lease. An earlier malformed
  test transform failed before creation and is retained separately. Native
  sensor timeouts do not expose the hidden socket endpoint; an explicit denied
  port cannot truthfully be inferred from them. That acceptance limitation is
  documented, not replaced with a guessed diagnostic.
- Integrated #134/#156 gate: 2,303 Python tests passed, two skipped and nine Rust
  tests passed, including all required static checks.
- #122: forty regressions demonstrated RED with fourteen controls; all 54 new
  cases pass. The isolated gate passes with 1,996 Python tests, two skips and
  nine Rust tests. Controlled vehicles default to configurable `hero`; the
  other merge car is canonical `target`, with distinct target-car and lane-anchor
  poses. Historical aliases are read without altering saved evidence or
  retroactively inventing hero roles. Managed creation journals intent before
  native spawn and raw returned IDs before metadata/listeners. Only explicit
  same-episode journaled IDs authorize recovery: lost replies and legacy coverage
  gaps stay quarantined, rather than adopting actors by role or inventory.
  Actual dedicated 0.10.0 merge and lead-brake route setup checks each find
  exactly one `hero` in `world.get_actors()`, matching the policy ID. All other
  vehicle/sensor roles are distinct; native IDs exactly match the durable journal.
  Authoritative cleanup verifies original episode, inventory, six settings,
  weather, spectator and the same clean lease after each fixture. This is
  native managed setup, not a physical hazard or provider acceptance claim.
- Integrated #122 gate: 2,357 Python tests passed, two skipped and nine Rust
  tests passed. The listener conflict retains both journaled creation and the
  #135 fixed-step cadence metadata; all required static checks remain green.
- Published checkpoint `3ddc6df` passes GitHub CI (run 38022205599). No issue
  closure or PR merge has occurred.
- #154: fifty-six regressions demonstrated RED with twenty-eight controls;
  84 new cases and 207 broader checks pass. The isolated gate passes with 2,288
  Python tests, two skips and nine Rust tests. Trusted HOST/PORT environment
  defaults resolve independently only for omitted direct, MCP and session
  inputs; malformed configuration or explicit null/bool/string ports are not
  silently defaulted. Windows forwards exactly those two nonsecret values as
  separate argv data. Native finite/persistent Landlock health checks using
  omitted endpoints reach the configured 0.10.0 server, spawn no actors and
  verify unchanged state and the same clean lease. Stable literal-IP/mirrored
  networking guidance is documented; lease identity and dirty-state logic are
  unchanged and no changing WSL address is treated as a safe lease alias.
- Integrated #154 gate: 2,441 Python tests passed, two skipped and nine Rust
  tests passed; all required static checks pass. Integration retains #156's
  explicit streaming/secondary fields and resolves omitted HOST/PORT before
  constructing that expanded execution request.
- #128 native supplement: matching CARLA 0.10.0 reports unknown local blueprint
  lookups and attributes as `RuntimeError("std::exception")`, exposing a finite
  facade gap. Ten supplemental regressions genuinely failed; the added catalog
  RPC control passed. All 42 focused cases now pass. Only local library lookup
  and attribute errors use the distinct `BlueprintInputError`; catalog RPCs
  remain transport errors. The actual Landlock repeat returns contextual
  blueprint, sensor-kind and attribute failures, with no created actors, exact
  unchanged native state and the same clean lease. The failed receipt remains
  local. Invalid attribute values that native CARLA accepts are not falsely
  described as rejected. Full gate: 2,452 Python tests passed, two skipped and
  nine Rust tests passed; Ruff/format, Ty, Radon all A and Rust checks pass.
- #138 native acceptance: an actual Landlock persistent session on matching
  CARLA 0.10.0 rejects async `tick`, `tick_n(3)` and `tick_n(0)`, while async
  `wait(0.2)` completes through frame waits. After switching to fixed-step sync,
  the native frame stays paused: no old helper tick cues are observed. Both
  zero/nonzero waits and watching are refused with their synchronous-mode
  diagnostics. Three explicit owner ticks advance exactly frames 104809-104811
  and no more during the subsequent paused check. Close restores all six original
  settings, exact actor/episode/weather/spectator baseline and the same lease.
  No actors were created and no provider was used. Existing TDD/gate proof is
  unchanged; this native check does not replace the other issues' crash windows.
- #96 diagnostic: the original `walker.pedestrian.0015` route-position command
  is reproduced without a key, multiplier or teleport on native 0.10.0. At the
  same settled position, six seconds of a received 2 m/s control produce only
  0.585938 m movement and 0.0976563 m/s median native velocity in both unpaced
  and wall-paced fixed-step modes. Async mode yields the same native median
  and 0.585197 m displacement over 6.001256 seconds. These measurements rule
  out pacing alone, not every engine/control cause. Bounded raw snapshot chunks
  retain positions, z, velocities and control/box data. All three owned walkers
  are authoritatively deleted and original native state/same lease verified.
  The first diagnostic exceeded the receipt-size limit after cleanup; its
  measurements are not claimed as retained evidence. This reproduces failure,
  not a valid crossing, video, physical fix or provider acceptance run.
- #96 reporting/validity implementation: 28 genuine regressions fail before
  fixed local-lane geometry, independent measured entry/crossing deadlines and
  terminal fixture summaries. All 188 affected checks pass; its isolated gate
  passes with 2,385 Python tests, two skips and nine Rust tests. Full owned
  snapshots record actual positions, z, velocity and displacement before any
  provider range filter. The snapshot actor-origin criterion is distinct from
  native bounding-box centre/body clearance. Late entry remains invalid for its
  original deadline, and route completion, cleanup and infrastructure outcomes
  stay separate. Diagnostic/provider events cannot manufacture physical
  validity; old traces without measured evidence remain unverified. This does
  not correct native UE5 pedestrian speed or establish a valid live crossing.
  Commands, historical evidence and provider inputs are unchanged.
- Integrated #96 reporting gate: 2,480 Python tests passed, two skipped and nine
  Rust tests passed; all required static checks pass. Native physical correction
  and genuine valid-crossing acceptance remain pending.
- #155: 32 genuine regressions fail before persistent-only transport invalidation,
  originating-episode guards and durable connection evidence; 49 new cases and
  282 broader checks pass. Ordinary transport failure drops only the cached
  client, does not replay an RPC, and permits a fresh connection on the next
  request. Unexpected episode replacement and missing/malformed connection
  evidence remain terminal, even when script code ignores the error. Ownership,
  settings and sensor origins are never adopted from a replacement world.
  Actual offline worker SIGKILL and fresh recovery checks retain dirty evidence
  before actor/settings operations; they are not native restart acceptance.
  The integrated gate passes: 2,529 Python tests, two skips and nine Rust tests;
  Ruff/format, Ty, Radon all A and Rust checks pass. Native restart acceptance
  remains pending.
- #133: 36 genuine regressions fail before journaled same-map reload and published
  traffic-light reset; 45 new cases, including nine passing controls, and 139
  focused checks pass. Retained actor handles and unresolved creation intents
  refuse setup before any native preflight. The returned episode ID is journaled
  before map inspection; missing replies, identity races and failed durable
  acknowledgements cannot clear the lease. Actual light states and their reset
  publication frame are fixture metadata, not selector inputs. Comparison omits
  only ephemeral light actor IDs and reset frame. The integrated gate passes:
  2,574 Python tests, two skips and nine Rust tests; required static checks all
  pass. The existing hazard frame-limit test now separately asserts the mandatory
  reset setup frame and exactly one runtime tick, preserving its outcome checks.
- #133 initial native acceptance failed on dedicated CARLA 0.10.0: `reload_world(False)`
  raised `std::exception` before a returned episode ID could be acknowledged.
  No fixture actors were created. The same lease retained its `pending` journal
  and original six settings; the observed world was still synchronous with the
  requested setup settings, not restored. Read-only diagnosis confirms two
  available maps, the current Town10HD_Opt map, unchanged native state and
  unchanged dirty evidence. There was no reload retry, episode adoption, blind
  cleanup or provider call. The exception text does not establish whether the
  failure was rejection, timeout or publication; per-internal-RPC limits are
  distinct from the supervisor wall limit. That attempt left the endpoint
  quarantined pending operator review; the later approved replacement and
  explicit ten-second acceptance are recorded below. Failed receipts and
  journals remain private and retained.
- #133 native follow-up on the separate dedicated CARLA 0.9.16 Windows server
  also fails before acknowledgement, reporting a 5-second simulator timeout.
  A later read observes a changed episode; no fixture actors were created, and
  the existing test lock/journal remains pending and dirty. The earlier cleanup
  implementation attempted settings restoration while the old episode ID was
  still published; that ID was not sufficient authority after a lost reply.
  Two genuine RED close/recovery regressions reproduce this gap and now pass
  with unconditional unresolved-reload cleanup refusal. No cleanup frame wait,
  tick, actor deletion or settings write is permitted by that unresolved state.
  Further failing diagnostic assertions require an unknown episode result rather
  than an unobserved unchanged-world claim. The pending journal remains unchanged;
  no late publication is adopted.
  All 61 focused reload/session/hazard checks pass. Both native endpoints were
  quarantined at that stage; neither failed attempt establishes repetition
  acceptance. The 0.9.16 journal remains pending; only the dedicated 0.10.0
  service was subsequently replaced with user approval, as recorded below.
  The final integrated gate passes with 2,576 Python tests, two skips and nine
  Rust tests; Ruff/format, Ty, Radon all A and all Rust checks pass.
- #139/#133 settings-write follow-up: three genuine startup/close/recovery
  regressions fail before the post-lookup episode guard; all 87 focused managed
  reload/cleanup checks now pass. An episode change during `get_settings` cannot
  authorize the following settings write. Two additional failing diagnostic
  assertions in the same cases require generic cleanup exceptions to report
  unknown episode identity, without another native query. Dirty evidence remains
  retained. The final full gate passes with 2,579 Python tests, two skips and nine
  Rust tests; Ruff/format, Ty, Radon all A and all Rust checks pass.
- #155 cleanup/error follow-up: 20 genuine regressions fail before distinguishing
  local alias errors from native lookup failures and preserving converted cleanup
  failures; 35 new cases include 15 passing controls. Native destruction and Stop
  failures freeze further RPCs in the same request, including persistent-worker
  finalization, while retaining original sensor handles and ownership/settings
  evidence. Local registry/file errors do not drop the client; finite cleanup
  continuation is unchanged. The integrated full gate passes with 2,614 Python
  tests, two skips and nine Rust tests; all required static checks pass. Native
  simulator restart acceptance remains pending.
- #26 optional synchronous-density feature: 69 genuine regressions demonstrate
  RED during implementation; 107 new cases include 38 passing controls. The
  frozen focused set passes all 112 cases, including five existing root controls.
  Strict optional configuration uses a dedicated proven-local Linux TM host;
  default no-density serialization, fixture ownership and script bind denial are
  unchanged. Real local-socket proof tests and mock-backed CARLA checks cover
  durable host/actor intents, startup/reload/spawn proof boundaries, bounded
  four-spawn/four-missing-removal work, same-snapshot observations, and guarded
  shutdown before settings restoration. Worker death cannot prove deregistration;
  unknown host/reload evidence remains quarantined. The final combined full gate
  passes with 2,721 Python tests, two skips and nine Rust tests; all required static
  checks pass. Native density had not run at that source checkpoint; the later
  provider-free native lifecycle result is recorded below. The parent remains
  open for its external child/process prerequisites and acceptance limits.
- Published pre-density checkpoint `0ebfe80` passes GitHub CI (run 38030227260). Issues stay
  open until merge and their completed acceptance checks.
- Published combined checkpoint `67bc8f7` passes GitHub CI (run 38031100187).
  Its local gate remains 2,721 Python tests, two intentional skips and nine Rust
  tests, with all required static checks passing. This does not replace native
  acceptance or authorize issue closure before merge.
- Published documentation checkpoint `6dfcdc7` passes GitHub CI (run 38053001385).
- #133 operator replacement and native retry on 2026-10-10: the user approved
  replacing only the known dedicated CARLA 0.10.0 test service. Fresh process and
  listener checks found its old processes already absent and all reserved ports
  closed; this operation did not stop them or touch the 0.9.16 server. The same
  recovering endpoint lease was held while the original pending marker and all
  three failed receipt files were archived byte-for-byte and hash-verified.
  The new launcher/native identities and port ownership were verified, followed
  by matching 0.10.0 versions, a changed episode, the exact original asynchronous
  six-setting/inventory/weather/spectator baseline, the same map and two advancing
  published frames. Explicit operator decommission/replacement resolution then
  made that same lease clean and reacquirable. This was not managed recovery,
  same-episode restoration or reload acceptance; original failed evidence remains.
  A new bounded provider-free #133 attempt then again raised `std::exception`
  before reload acknowledgement. No fixture actors were created. Cleanup correctly
  refused frame waits, ticks, actor deletion and settings restoration, retaining
  a new pending journal on that same endpoint. Read-only diagnostics observed a
  different episode, synchronous requested settings and a fresh client's cached
  snapshot at frame zero with no actor entries. This is not proof that the loaded
  scene is empty or fully published. Readback and exact pending bytes remained
  unchanged during diagnosis. No late episode was adopted, no reload was retried
  against dirty state and no Traffic Manager was constructed. This five-second
  attempt left the dedicated 0.10.0 endpoint quarantined again. All receipts stay
  local and private; the next approved replacement and successful checks follow.
- #133 explicit ten-second native acceptance on 2026-10-10: under the existing
  dedicated-service replacement approval, fresh exact process/start/parent and
  listener ownership checks identified only the known test launcher/native pair.
  The same recovering lease retained the second pending journal and four failed
  receipts byte-for-byte in a hash-verified archive. A durable decommission intent
  preceded stopping only the known native process; its launcher exited without
  a separate stop command. Replacement identities, closed old
  ports, matching release, exact original asynchronous scene configuration and
  two advancing published frames were verified before explicit operator resolution.
  This is not managed recovery or same-episode restoration; the 0.9.16 service,
  firewall and script sandbox permissions were untouched.
  Two consecutive provider-free `lead_brake` route repetitions then passed with
  explicit `rpc_timeout_seconds: 10.0` and a separate 180-second external TERM
  deadline plus ten-second KILL grace. Both durably acknowledged the returned
  replacement episode before map access, reset and published all 15 traffic
  lights, and recorded identical initial light states excluding ephemeral actor
  IDs/frame. Each prepared the real fixture and advanced three owner steps, then
  authoritatively deleted all owned actors and verified the original six settings
  and acknowledged episode's exact inventory/weather/spectator baseline. The same
  endpoint lease was clean after both. This satisfies the consecutive-light-state
  check for that explicit profile, not the default five-second profile, completed
  hazards, all scenarios or provider acceptance. The global default remains five
  seconds and maximum ten; generic earlier exceptions do not identify a failure
  cause. Saved specifications, historical evidence and failed bytes are unchanged.
- #26 provider-free native density lifecycle on 2026-10-10: the trusted WSL worker
  opened a proven same-process local TM on 8500, rebound it only to the acknowledged
  replacement episode, kept it asynchronous during protected fixture preparation,
  then requested synchronization. Five background registrations were acknowledged
  over 20 consecutive owner frames with four-attempt/four-missing-removal boundary
  limits and no unresolved spawn intent. Fixture actors remained protected and
  original actors were never adopted. Snapshot positions and the 150-metre range
  exclusion were checked; all backgrounds were out of range, so positive in-range
  telemetry was not exercised by this first run. No missing-removal event, completed
  route/hazard or physical-motion guarantee follows from the smoke check.
  Authoritative owned deletion, async/shutdown acknowledgements, closed original
  TM listener, exact original six settings and acknowledged episode inventory,
  weather and spectator were verified before the same lease was reported clean.
  RPC 10 seconds and external 180/10-second bounds were explicit. Provider calls
  remained zero; no sidecar or arbitrary-script bind grant was used. #26 remains
  open for its external child/process prerequisites and remaining acceptance.
- #26 separate in-range native diagnostic: the next run required the first
  density receipt's exact successful cleanup baseline, closed host proof and
  same clean lease, then opened a fresh proven local TM. A probe-only map view
  reordered actual native spawn transforms near the already prepared route start;
  it did not modify transforms, teleport/adopt actors, expand the 150-metre range
  or change production sources. The session's unwrapped native map reference
  was restored before fixture cleanup. Five registrations over 20 consecutive
  owner frames supplied
  99 matching in-range background observations from the exact owner snapshots,
  at measured distances 7.10-34.69 metres. This exercises positive telemetry under
  an explicitly arranged spawn order, not default placement or complete route,
  hazard, physical-motion or deterministic-traffic acceptance. Missing removal
  was not exercised. Exact owned deletion, original six settings and acknowledged
  episode inventory/weather/spectator, local TM async/shutdown acknowledgement
  and original listener closure were verified again. A separate receipt-only
  post-run check reacquired the same clean lease and confirmed no TM listener,
  without constructing a native client. Providers remained disabled; RPC 10s
  and external 180/10-second bounds and original failed evidence were unchanged.
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
  or sensors before #133's failed reload. Its failed journals remain archived
  after approved operator replacement; the later explicit ten-second repetition
  and density checks verify a clean lease and asynchronous baseline, as recorded
  above. The separate 0.9.16 pending journal remains unresolved. #159's separate
  managed worker-death checks pass as described above. The quarantined WSL
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
| [#26](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/26) | Support bounded CARLA sessions and managed closed-loop experiments | Optional density implemented with TDD/full gate, native bounded lifecycle/cleanup and separately arranged in-range telemetry checks; external child/process prerequisites and remaining acceptance limits persist. |
| [#82](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/82) | Validate and publish the source-only experimental alpha with release evidence | External verification pending: independent private-report submission and receipt. |
| [#87](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/87) | Expose managed experiment controls and publish a reproducible rules-versus-Jev demo | Skipped for now at the user's request; original article input remains unavailable. |
| [#94](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/94) | Exercise Jev along a route with controlled traffic hazards | Pending genuine UE5 live scenario validation; provider calls explicitly disabled by the user. |
| [#96](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/96) | Fix UE5 pedestrian hazard motion and verify physical scenario completion | Partial: independent measured completion checks implemented with TDD/full gate; native motion defect reproduced but physical fix/valid crossing and provider acceptance pending. |
| [#120](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/120) | Restore world and Traffic Manager settings after script and persistent-session runs instead of marking the lease clean | Implemented; full gate passed; live CARLA validation pending. |
| [#121](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/121) | Count already-destroyed actors as cleaned up and release successful batch destroys from the journal | Implemented; full gate passed; live validation pending. |
| [#122](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/122) | Give the vehicle under test role_name hero and stop calling the other merge car ego | Implemented: configurable hero, canonical target and journal-only ownership; TDD, both native fixture role checks and full gate pass; unknown replies remain quarantined. |
| [#123](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/123) | Save and publish sensor evidence with correct file types and keep the result when publication fails | Implemented; TDD, native file/publication checks, and full gate pass; review evidence-backed 480x270 default instead of proposed 640x360. |
| [#124](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/124) | Bound sensor queue memory and encoding cost by image size and sensor count | Implemented: queue reservations, actual byte bounds, and explicit saving; TDD, native 1080p metadata RSS/CPU checks, and full gate pass; no whole-process memory guarantee. |
| [#125](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/125) | Align load_world, reload_world and apply_batch defaults with CARLA or make them explicit | Implemented; full gate passed; live CARLA validation pending. |
| [#126](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/126) | Bind actor aliases to the episode and check liveness against the server | Implemented; TDD, actual pre-tick naming and post-restart rejection, and full gate pass. |
| [#127](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/127) | Fall back to a batch destroy for sensors and managed actors when destroy() returns False | Implemented; full gate passed; live CARLA validation pending. |
| [#128](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/128) | Return structured errors for unknown blueprints, attributes and sensor kinds | Implemented including native local RuntimeError normalization; TDD, actual Landlock native invalid-input checks and full gate pass. |
| [#129](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/129) | Report whether generate_route reached its destination, or rename it to follow_waypoints | Implemented permitted greedy-follower naming and honest arrival results; TDD, native junction check, and full gate pass. |
| [#130](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/130) | Reject synchronous Traffic Manager requests and unguarded autopilot traffic that the sidecar cannot step | Implemented; full gate passed; live sidecar validation pending. |
| [#131](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/131) | Make managed experiment seeds vary the initial condition, or rename them as replicates | Implemented locally: canonical replicate index, strict legacy input, and identical-initial-condition warnings; TDD and full gate pass. |
| [#132](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/132) | Do not create a Traffic Manager in the managed worker by calling set_autopilot(False) | Implemented; full gate passed; live CARLA validation pending. |
| [#133](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/133) | Reload the world and reset traffic lights before each managed repetition | Implemented with TDD/full gate; two native UE5 repetitions share initial light states with explicit RPC 10s and verified cleanup. Default 5s attempts failed; evidence preserved through approved replacement, not episode adoption. |
| [#134](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/134) | Seed, settle and stop AI walker controllers as CARLA's walker lifecycle requires | Implemented; TDD, native 0.9.16 async/sync lifecycle and full gate pass; UE5 spawns refused; navigation seed restoration is not claimed. |
| [#135](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/135) | Wait for periodic sensor data by default and stop waiting once a later frame arrives | Implemented: bounded periodic waiting, later-frame wakeup and honest cadence evidence; TDD, native RGB/GNSS checks, and full gate pass. |
| [#136](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/136) | Journal spawned actors as each is created and decouple the per-RPC timeout from the script budget | Implemented; full gate passed; live CARLA validation pending. |
| [#137](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/137) | Describe the alpha demo as a step-by-step procedure, not a reproducible result | Implemented; documentation checks pass. |
| [#138](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/138) | Guard tick, tick_n, watch_actor and api.wait by synchronous mode | Implemented; TDD, actual Landlock native mode/tick-cue checks and full gate pass. |
| [#139](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/139) | Do not mark the managed lease clean after a world replacement without checking settings | Implemented fail-closed verification policy; full gate passed; live validation pending. |
| [#140](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/140) | Use bounding_box.location and rotation in ground-truth boxes and clearance metrics | Implemented; TDD, both native fixture/corner checks, and full gate pass; historical evidence unchanged. |
| [#141](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/141) | Surface CARLA client/server version mismatches in toolkit warnings | Implemented locally, including native-verified version-only health; TDD and full gate passed. |
| [#142](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/142) | Convert reload_world RuntimeError into the structured reload_world_failed result | Covered by #136; failure/retention/cleanup contract and full gate pass. |
| [#143](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/143) | Explain missing Traffic Manager servers and unimportable CARLA APIs in error messages | Implemented required diagnostics; TDD, actual Landlock policy-evidence checks, and full gate pass; optional paused-world message not added. |
| [#144](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/144) | Let persistent sessions use a non-default Traffic Manager port | Implemented: frozen bounded connect-port configuration; TDD, actual Landlock persistent connection on port 8500, and full gate pass. |
| [#145](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/145) | Read script telemetry from one world snapshot and report its frame | Implemented: frame-coherent motion and explicit missing-state errors; TDD and full gate pass. |
| [#146](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/146) | Expose recorder additional_data and optionally record managed runs with the CARLA recorder | Implemented required recorder option; TDD, native record/replay, and full gate pass; optional managed recorder not added. |
| [#147](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/147) | Clarify that the 23 remaining traffic actors in the camera demo are signs and lights | Implemented; documentation checks pass. |
| [#148](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/148) | Label the bolded drive-demo distances as wall-clock, asynchronous and not repeatable | Implemented; documentation checks pass. |
| [#149](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/149) | Batch traffic spawning and autopilot with SpawnActor.then(SetAutopilot), and fix the stale controller docstring | Implemented permitted per-vehicle safety alternative; full gate passed; live validation pending. |
| [#150](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/150) | Reduce script API divergence from CARLA's names and argument order | Implemented: value constructors, canonical names/order, compatible strict legacy inputs, and native-operation hints; TDD, native value-object comparison, and full gate pass. |
| [#151](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/151) | Name Traffic Manager presets and desired_speed units unambiguously | Implemented naming and permitted profile-interaction documentation; TDD, native two-pass speed read, and full gate pass. |
| [#152](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/152) | Filter safe traffic vehicles by base_type instead of a name list | Implemented locally: exact native car classification with legacy fallback; TDD, both native inventory checks, and full gate pass. |
| [#153](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/153) | Read weather back from CARLA and make the showcase prompt engine-aware | Implemented; TDD, both native readback/restoration checks, and integrated full gate pass. |
| [#154](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/154) | Support a configured default CARLA host and a stable lease key under WSL2 | Implemented omission-only endpoint defaults and stable-IP guidance; TDD, actual Landlock configured-endpoint checks and full gate pass; lease protection unchanged. |
| [#155](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/155) | Drop the cached CARLA client in persistent sessions after a simulator restart | Implemented: next-request reconnect, durable sticky restart refusal and retained recovery evidence; TDD/full gate pass; native restart acceptance pending. |
| [#156](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/156) | Allow non-default CARLA streaming and secondary ports in the sandbox | Implemented configuration; TDD, actual Landlock finite/persistent native streaming and full gate pass; hidden socket denial cannot be inferred from a timeout. |
| [#157](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/157) | Close a script's subscription before destroying an inherited sensor | Implemented locally: original-handle unsubscribe, retry acknowledgement, and same-episode guards; full gate passed. |
| [#158](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/158) | State CARLA's MIT and CC-BY licences in the README credits | Implemented; documentation checks pass. |
| [#159](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/159) | Make managed recovery's fresh-snapshot check work in an asynchronous world | Implemented locally: shared mode-aware fresh-snapshot recovery; TDD and full gate passed. |
| [#160](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/160) | Call Sensor.is_listening as a method | Implemented; full gate passed; optional live warning check pending. |
| [#161](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/161) | Refuse or warn when attaching cameras in no-rendering mode | Implemented locally; TDD, native rendering check, and full gate pass. |
| [#162](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/162) | Let camera auto-exposure settle before keeping a one-shot capture | Investigated locally: not reproduced in the supported live bright/dark check; unchanged capture contract and limitations documented. |
| [#163](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/163) | Reuse SensorSubscription in the demo capture script | Implemented: shared bounded listener, raw-BGRA digest provenance, and compatible historical PNG verification; TDD and full gate pass. |
| [#164](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/164) | Add ground-truth helpers and richer image digests for perception tests | Implemented: read-only spatial queries, frame-bound native actor boxes, measured intrinsics and labelled native-buffer image metadata; TDD, native geometry/calibration/hash comparison, and full gate pass. |
| [#165](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/165) | Report substepping settings and stop swallowing density wait_for_tick timeouts | Implemented locally: six-field reporting and fatal frame waits with lifecycle evidence; TDD, native reporting check, and full gate pass. |
