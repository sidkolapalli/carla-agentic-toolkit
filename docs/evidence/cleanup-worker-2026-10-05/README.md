# Cleanup worker lifetime fix — 2026-10-05

Issue [#93](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/93)
reported a persistent close with removed actors but no cleanup result file.
The toolkit now retains the native CARLA client until its bounded result is
flushed, fsynced and atomically published. The disposable worker then exits without
running native client finalizers. Its supervisor still reaps the process group,
enforces the deadline and retains the lease through cleanup.

Abnormal worker exits and missing/unreadable results now produce explicit failure
diagnostics: `cleanup.worker.exit_code` and `result_available`. Negative exit
codes identify Linux signals. These diagnostics expose no raw worker stderr or
environment. They preserve the recovery barrier; missing evidence is never
converted into a successful cleanup.

## Diagnosis and regression

The earlier validation window has a kernel SIGSEGV record at 12:14:37 EDT in the
official 0.10.0 client. Resolving offset `0x723440` in that installed library gives
CARLA's streaming `Client::UnSubscribe` routine. A full native stack was not
captured, so this does not establish the exact upstream C++ race.

Inspection found that `_cleanup_connected` released its last adapter/client
reference before the caller could publish the report. A disposable-process test
with a fatal native-finalizer stand-in reproduces that failure deterministically:
the old worker exits with status 73 and no result file, for both successful and
failed destruction. The fixed worker publishes the correct report and exits 0.
Failed destruction still preserves the owned actor IDs.

Three further tests cover missing reports after exit 0, exit 17 and SIGSEGV. They
verify explicit diagnostics, unchanged journals and retained quarantine. All five
new cases failed before the implementation change; the focused cleanup/recovery/
session suite then passed 35 tests.

## Live validation

The official Windows CARLA 0.10.0 / UE5.5 server and matching Linux client ran in
the same isolated WSL2 setup and temporary local relay used by the
[earlier UE5 validation](../ue5-validation-2026-10-05/README.md).

| Check | Observed result |
| --- | --- |
| Baseline diagnostics | 92 closes passed; the intermittent native crash did not recur |
| Final production worker | 24/24 closes, retained state and telemetry passed |
| Unrelated actor | Survived every final close and the forced failure/recovery |
| Forced cleanup-worker exit 17 | Close failed explicitly; a new session was blocked |
| Explicit recovery after forced failure | Removed the owned actor, cleared quarantine and restored the world |
| Finite scripts | Pre-frame destruction and blocked native timeout cleanup passed |
| Final local quality gate | 599 Python tests passed, two expected skips; nine Rust tests passed |

Lint, typing, complexity, formatting, Clippy and builds also passed. The workspace
and live runtime have the same Python source SHA-256:
`516d2580d82afcde10689d69c0d812f5607a4835a2faaf5ed36408210d99c333`.

An earlier fixed-code harness run also passed all 24 closes, but its single final
observation after removing its own unrelated verification actor failed the world
comparison. A subsequent independent probe found no leftover actors. That report
is retained. The final run used bounded fresh-snapshot confirmation and passed;
the toolkit source did not change between these runs.

The allowlisted [summary](summary.json) preserves every cohort, forced failure,
source fingerprint and raw-report hashes. Raw diagnostics remain in ignored
`target/ue5-cleanup-93-2026-10-05`; credentials, private journals and full snapshots
are excluded. The earlier merge comparison remains a separate source revision
and cohort. This fixes the toolkit's report-loss path; it does not patch CARLA's
native library or establish production readiness or full UE5 feature coverage.
