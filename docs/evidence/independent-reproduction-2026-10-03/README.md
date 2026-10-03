# Independent automated reproduction

On 2026-10-03, a separate automated operator reproduced the six declared
rules/Jev trials at commit `660ac45ef0163f8758d2abe51f1b2136dcc9e568` using the
checked-in instructions. It started without the implementing operator's
conversation history, private traces, or test harnesses. All six trials completed
and cleaned up; the comparison accepted three eligible runs per policy with no
exclusions or matching blockers. The Jev runs recorded 36 actual provider
selections. No failed trial was retried.

This is an independent automated operator test on the same supported Windows/WSL2
host, not an external human trial or a second-machine portability claim. It used
a fresh native Linux checkout, virtual environment, private state directory,
output directory and compiled Rust runner. Package download caches were shared.
A separate operator supplied the dedicated simulator and passed the existing
credential privately; the reproducing operator did not inspect credential files.

## Procedure and environment

The operator followed [client setup](../../client-setup.md),
[managed experiments](../../managed-experiments.md), the
[original evaluation procedure](../merge-comparison-2026-10-02/README.md), and
the public comparison CLI. The exact six specs are in [evaluation.json](evaluation.json).
Only the dedicated simulator endpoint changed from the original experiment.

The private repository required an authenticated Windows Git clone followed by
transfer into a fresh native WSL checkout. Direct WSL HTTPS cloning timed out;
that setup failure is retained in the operator's local audit and did not begin a
trial. The remaining installation, preflight, baseline, provider and comparison
commands worked as documented. In particular, installing the matching CARLA API
again after syncing the optional SDK was necessary and already documented.

The verified environment was CARLA server/API 0.9.16, Python 3.12.14,
`typesafe-sdk==0.7.2`, model `jev-1.13.0`, and Linux
`6.18.33.2-microsoft-standard-WSL2` with Landlock enforcement. Each run used
simulation-time scheduling and the same fixture, planner and controller. After
all trials, a separate read-only check found the original asynchronous world
settings and no vehicle, walker, sensor or controller actors.

## Retained evidence

- [Static comparison](comparison.html) and [machine-readable comparison](comparison.json).
- [Exact specs](evaluation.json), [trace and artifact hashes](trace-manifest.json),
  and [final simulator state](final-simulator-check.json).

The comparison replaces only private source paths with stable trace aliases.
All numerical fields, counts, metrics, usage and model metadata remain exactly
as returned by the public CLI. The six raw traces remain in private local state;
their hashes are published for audit. This is a separate replication cohort,
not extra samples silently added to the original comparison.

Three trials per policy are descriptive evidence. Zero delivered collision
events, completed maneuvers and successful cleanup do not establish safety,
production reliability, realistic perception or improved driving behavior.
