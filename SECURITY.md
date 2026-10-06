# Security Policy

## Supported Versions

Security fixes reach `main` through reviewed `hotfix/*` pull requests, then are
back-merged into `develop`. Coordinate undisclosed fixes privately with the
maintainer before opening any public branch or pull request.
Experimental alpha tags are snapshots;
update to the latest `main` when a fix is published. There is no long-term
support branch for an alpha release.

| Version | Supported |
| --- | --- |
| Latest `main` | Yes |
| Older commits and forks | No |

## Reporting a Vulnerability

Email the maintainer privately at
[[withdrawn maintainer email]](mailto:[withdrawn maintainer email]), with a subject beginning
`[CARLA Agentic Toolkit security]`. Do not open a public issue or include
vulnerability details in a public pull request.

You may also use GitHub's private **Report a vulnerability** form under
[Security → Advisories](https://github.com/sidkolapalli/carla-agentic-toolkit/security/advisories).
If the form is unavailable or you do not have a GitHub account, use the email
address above. Email remains an available reporting route independently of
GitHub's form. Please allow the maintainer time to investigate and coordinate a
fix before disclosing details publicly.

Include the affected commit, Linux distribution and kernel, reproduction steps,
impact, and any proposed mitigation. Remove credentials, CARLA recordings, and
unrelated host data from the report.

## Security Scope

CARLA Agentic Toolkit treats the Rust Landlock runner as a security boundary around
model-authored Python. Reports involving sandbox escape, private API access,
unexpected filesystem or network access, process escape, or unsafe fallback
behavior are security issues. The AST validator is defense in depth, not a
replacement for Landlock. Execution fails closed when every requested rule
cannot be fully enforced.

The current release is a local experimental stdio server. It does not provide
authentication, authorization, tenant isolation, or safe public-network access.

## Landlock Policy

The child receives read/execute access only to existing paths in these classes:

- the project root, including source and a project-local virtual environment;
- the resolved Python installation containing the selected interpreter;
- `/usr`, `/lib`, and `/lib64` for the Linux runtime and shared libraries.
- existing system hostname and resolver configuration files under `/etc`.

`/dev`, `/sys`, and `/proc` are not granted. A virtual environment outside both
the project root and resolved interpreter installation is unsupported unless its
path is added deliberately after a real runtime need is demonstrated.

Write access is limited to:

- one per-run temporary work directory, deleted after verified cleanup;
- `CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR`, which persists for captures and evidence.

CARLA recorder files are different: the simulator process opens them on the
simulator host. `CARLA_AGENTIC_TOOLKIT_RECORDER_DIR` names that server-side directory and
does not grant the sandbox host filesystem access.

## Network Policy

The child receives TCP **connect** rights for the CARLA RPC base port, its two
adjacent streaming ports, port 8000, and explicitly requested Traffic Manager
ports. It receives no TCP bind rights.

Landlock filters port numbers, not destination addresses or hostnames. An
allowed port can therefore be reached on any routable host; connect only to
trusted networks. Unlisted ports remain blocked by Landlock.

## Resource and Output Policy

The child has a 4 GiB address-space limit, 60 CPU-second limit, 128-process
limit, 256-file-descriptor limit, and the requested wall-clock script deadline.
Timeout cleanup kills the complete child process group.

A small trusted cleanup guardian starts before Landlock and retains the simulator
lease and outer output pipes. It accepts only the registered child process-group
identity through a close-on-exec pipe, and kills/verifies that group when the
wrapper exits or is killed. It never runs generated code. EOF therefore waits for
verified group death before the parent begins actor cleanup. If process-death
evidence cannot be read, ownership remains quarantined rather than assumed clean.

Traffic Manager must already be running in a trusted client process before a
sandbox script uses autopilot or traffic tuning. Its server needs TCP bind
rights, which the sandbox deliberately does not grant. The self-contained MCP
smoke test uses direct vehicle controls and does not require Traffic Manager.

Child stdout and stderr are drained while execution runs and share a 1 MiB
capture limit. Exceeding it discards the captured payload and returns
`output_too_large`; genuine deadline overruns return `script_timeout` with
bounded partial output.

Python script stdout is limited to 512 KiB of UTF-8 before serialization.
Script results may contain at most 10,000 total collection entries and 50
nested collection levels. Cycles and nonfinite numbers return
`result_not_serializable`; oversized stdout returns `output_too_large`.

Actor IDs created through the curated API are written incrementally to the
per-run work directory. After an uncaught exception or killed timeout, the
parent reads that journal before deleting the directory and asks CARLA to
destroy only those IDs, in reverse creation order. Cleanup is best effort: its
structured report preserves the original failure classification and records any
CARLA connection or destroy failure. Successful scripts retain actors unless
they explicitly clean them.

Failed cleanup retains the ownership journal and an independent dirty simulator
marker. An available process lock alone does not establish that cleanup succeeded.
Do not remove recovery markers to admit another mutator without verifying the
simulator state.

## Persistent Script Sessions

`CARLA_AGENTIC_TOOLKIT_ENABLE_SCRIPT_SESSIONS=1` enables one additional local
lifecycle tool. Session IDs are random and scoped to the current stdio server;
another server instance cannot address them. The namespace, resource limits, and
Landlock rules last for the entire session. No imports, new network rights, or
credential environment variables are added. Idle, request, and absolute deadlines
are bounded; CPU accounting is cumulative across requests.

There is one outstanding request and one latest result per session. Telemetry is
limited to an explicitly owned actor, a validated interval, and a latest-only slot
with replacement counts. Close, cancellation, and disconnect terminate execution
and clean all actors created by that session. Termination and cleanup failure are
reported separately; failed journals remain available for trusted recovery.

## Trusted Managed Experiments

`CARLA_AGENTIC_TOOLKIT_MANAGED_EXPERIMENTS=1` exposes the reviewed merge experiment
through local start/status/stop/result operations. The worker is trusted Python
outside Landlock and must never accept generated code. Its strict numerical spec
rejects unknown fields, imports, shell commands, and provider URLs. The supported
fixture requires a dedicated CARLA instance and assigns creation ownership,
protection, and exactly one controller to its two vehicles. Numerical planning,
feasibility checks, actuation, and termination remain in reviewed code.

Finite scripts, persistent sessions, and managed workers use the same nonexpiring
interprocess simulator lease. Cooperating clients must share the state directory
and resolve their endpoint to the same identity. Ambiguous hostname resolution is
rejected. This coordinates local clients; it cannot isolate hostile or unrelated
CARLA clients, alternate network identities, or separate state directories. An
unexpected frame or episode change invalidates an active managed run.

Status and cancellation use bounded local files independently of simulator RPCs
and provider inference. Only the supervisor confirms worker-tree termination.
Cleanup restores owned settings and preserves failures; an interrupted experiment
remains invalid even when a subsequent recovery succeeds.

The optional Jev SDK runs only in the trusted worker. `TYPESAFE_API_KEY` belongs in
the trusted launch environment, never the repository, a generated script, an
experiment spec, or the output directory. The Rust child receives a cleared
environment. Keep any credential files outside all sandbox-readable paths.
Requests contain reviewed, range-filtered simulator ground truth and numerical
candidate evidence. One inference is outstanding at a time; deadlines, retries,
and per-run limits bound requests. A durable SQLite reservation budget coordinates
workers sharing one private state root. It does not enforce limits across other
machines or other state roots and does not replace provider-side billing limits.
Missing provider usage remains unknown.

`CARLA_AGENTIC_TOOLKIT_STATE_DIR` defaults to an owner-only local directory outside
the sandbox read/write allowlists. It holds run IDs, authoritative traces, recovery
journals, and the shared provider budget. Traces reject credential-shaped fields
and common token forms, enforce size limits, and retain invalid/partial evidence.
They are not an invitation to log arbitrary trusted environment data. Local run
IDs are access controls for one OS user, not authenticated multi-tenant identities.
Retain private state across restarts when crash recovery or account budgets must
survive them. Archive terminal jobs deliberately; retained-job limits reject new
runs rather than deleting evidence automatically.

## Container Runtime

The image runs as UID/GID 10001 using a headless Debian distroless runtime. It
contains the required native library closure, Debian package metadata, and license
notices; it has no shell or package manager. Optional terminal/GUI Python extensions
are excluded, and Python's portable UUID implementation remains available. Build
and CI checks verify CARLA imports and actual sandbox execution. Vulnerability
scans cover the selected database, package inventory, and severity threshold; a
clean result is not a guarantee that every bundled native library is vulnerability-free.

Only captures explicitly marked `publish=True` are returned as MCP image
content. The parent resolves each path below `CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR`, rejects
escapes and invalid image signatures, and enforces count, per-file, and combined
encoded-response limits. The same validation is applied when reading a durable
`carla-output://capture/...` Resource link.

Landlock has no byte quota, and `RLIMIT_FSIZE` would terminate the child with
`SIGXFSZ` before it could reliably serialize the required dedicated error.
Therefore individual output-file and aggregate persistent-directory limits are
not enforced in this release. Operators must apply filesystem quotas and clean
`CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR` according to local retention policy.
