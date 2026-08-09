# Security Policy

## Supported Versions

Until tagged releases exist, only the latest commit on `main` receives security
fixes.

| Version | Supported |
| --- | --- |
| Latest `main` | Yes |
| Older commits and forks | No |

## Reporting a Vulnerability

Use **Security → Advisories → Report a vulnerability** in the public GitHub
repository. Do not open a public issue or disclose the vulnerability before a
fix is available. If private vulnerability reporting is unavailable, contact the
repository owner through the contact method on their GitHub profile.

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

`/dev`, `/sys`, and `/proc` are not granted. A virtual environment outside both
the project root and resolved interpreter installation is unsupported unless its
path is added deliberately after a real runtime need is demonstrated.

Write access is limited to:

- one per-run temporary work directory, deleted after execution;
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

The child has a 4 GiB address-space limit, 60 CPU-second limit, 4096-process
limit, and the requested wall-clock script deadline. Timeout cleanup kills the
complete child process group.

Child stdout and stderr are drained while execution runs and share a 1 MiB
capture limit. Exceeding it discards the captured payload and returns
`output_too_large`; genuine deadline overruns return `script_timeout` with
bounded partial output.

Actor IDs created through the curated API are written incrementally to the
per-run work directory. After an uncaught exception or killed timeout, the
parent reads that journal before deleting the directory and asks CARLA to
destroy only those IDs, in reverse creation order. Cleanup is best effort: its
structured report preserves the original failure classification and records any
CARLA connection or destroy failure. Successful scripts retain actors unless
they explicitly clean them.

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
