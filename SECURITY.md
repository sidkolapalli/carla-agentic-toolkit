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
repository owner through the contact method on their GitHub profile before
sharing details.

Include the affected commit, Linux distribution and kernel, reproduction steps,
impact, and any proposed mitigation. Remove credentials, CARLA recordings, and
unrelated host data from the report.

## Security Scope

CARLA MCP executes model-authored Python and therefore treats the Rust Landlock
runner as a security boundary. Reports involving sandbox escape, private API
access, unexpected filesystem access, unexpected network access, process escape,
or unsafe fallback behavior are security issues.

The AST validator is defense in depth, not a replacement for Landlock. The
server intentionally fails closed when the runner is missing or the requested
Landlock rules cannot be fully enforced.

Child stdout and stderr are drained while the script runs and share a 1 MiB
capture limit. Exceeding it discards the captured payload and returns
`output_too_large`; a genuine deadline overrun keeps bounded partial output and
returns `script_timeout` after killing the process group.

The current release is a local experimental stdio server. It does not claim to
provide authentication, authorization, tenant isolation, or safe exposure over a
public network.
