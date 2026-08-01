# Client Setup

CARLA MCP runs locally through stdio. Linux clients launch it directly; Windows
11 clients use `carla-mcp-windows` to launch the entire server and its existing
Rust/Landlock sandbox inside WSL2. There is no remote endpoint or standalone
wheel yet.

## 1. Prepare the Server

```bash
git clone https://github.com/sidkolapalli/carla-mcp.git
cd carla-mcp
uv sync --locked
# Install the CARLA Python API matching your simulator into this .venv.
cargo build --locked --manifest-path sandbox-runner/Cargo.toml --release

export CARLA_MCP_HOME="$(pwd)"
export CARLA_MCP_OUTPUT_DIR="$HOME/carla-mcp-output"
export UV_BIN="$(command -v uv)"
mkdir -p "$CARLA_MCP_OUTPUT_DIR"
```

Use absolute paths in client configuration. Desktop and agent processes do not
always inherit the same `PATH` as an interactive shell.

Confirm the CARLA package and sandbox before configuring a client:

```bash
"$UV_BIN" --directory "$CARLA_MCP_HOME" run python -c \
  'import carla; print(carla.__file__)'

CARLA_MCP_OUTPUT_DIR="$CARLA_MCP_OUTPUT_DIR" \
  "$UV_BIN" --directory "$CARLA_MCP_HOME" run python - <<'PY'
from carla_mcp.sandbox import execute_script

outcome = execute_script("result = 1", timeout_seconds=2)
assert outcome.ok, outcome.to_dict()
print("Sandbox ready:", outcome.sandbox)
PY
```

## 2. Claude Code

The following command was validated with Claude Code 2.1.207:

```bash
claude mcp add --scope local --transport stdio carla \
  -e "CARLA_MCP_OUTPUT_DIR=$CARLA_MCP_OUTPUT_DIR" -- \
  "$UV_BIN" --directory "$CARLA_MCP_HOME" run carla-mcp

claude mcp list
```

`--` is required so the remaining arguments go to `uv`. Local scope limits this
destructive tool to the current project and is the recommended default. Use
`--scope user` to make it available everywhere, or `--scope project` to create a
shareable `.mcp.json`; project-scoped servers require workspace approval.

Inside Claude Code, run `/mcp` to inspect the connection and tools.

## 3. OpenAI Codex

The following command was validated with Codex CLI 0.145.0:

```bash
codex mcp add carla --env "CARLA_MCP_OUTPUT_DIR=$CARLA_MCP_OUTPUT_DIR" -- \
  "$UV_BIN" --directory "$CARLA_MCP_HOME" run carla-mcp

codex mcp list
```

Codex stores this in `~/.codex/config.toml`, shared by Codex CLI, its IDE
extension, and ChatGPT desktop on the same host. CARLA operations can exceed
Codex's default 60-second tool timeout, so the full entry should look like:

```toml
[mcp_servers.carla]
command = "/absolute/path/to/uv"
args = ["--directory", "/absolute/path/to/carla-mcp", "run", "carla-mcp"]
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "prompt"

[mcp_servers.carla.env]
CARLA_MCP_OUTPUT_DIR = "/absolute/path/to/carla-mcp-output"
```

Use `/mcp` inside Codex to inspect the connected server.

## 4. VS Code

Create `.vscode/mcp.json` in the workspace or add the same entry to your user
profile:

```json
{
  "servers": {
    "carla": {
      "type": "stdio",
      "command": "/absolute/path/to/uv",
      "args": [
        "--directory",
        "/absolute/path/to/carla-mcp",
        "run",
        "carla-mcp"
      ],
      "env": {
        "CARLA_MCP_OUTPUT_DIR": "/absolute/path/to/carla-mcp-output"
      }
    }
  }
}
```

Use **MCP: List Servers** to start, stop, and inspect it. Do not enable VS Code's
outer MCP sandbox by default: CARLA MCP already applies Landlock to scripts, and
an additional parent sandbox can block the CARLA TCP connection.

## Windows 11 with WSL2

The Windows launcher uses `wsl.exe --exec` directly. It never runs
agent-authored code with Windows Python and never falls back to an unsandboxed
path.

> [!NOTE]
> For pre-merge testing, check out `feat/windows-support` in both the WSL and
> Windows clones. After the branch merges, use `main` instead.

First prepare the Linux runtime inside WSL2:

```powershell
wsl --install --distribution Ubuntu-24.04
wsl --list --verbose
wsl --update
wsl --version
```

The Rust runner hard-requires Landlock ABI V7, introduced with Linux 6.15.
Older WSL2 kernels, including 6.6, fail closed because they cannot enforce the
same filesystem and TCP rules. `carla-mcp-windows --check` reports the actual
kernel and is the definitive compatibility test.

Then, inside that WSL2 distribution:

```bash
git clone https://github.com/sidkolapalli/carla-mcp.git
cd carla-mcp
git switch feat/windows-support
uv sync --locked
# Install the matching CARLA Python API into this WSL .venv.
cargo build --locked --manifest-path sandbox-runner/Cargo.toml --release

command -v uv
pwd
```

Keep this checkout in the WSL filesystem, such as `/home/user/carla-mcp`. On
Windows, prepare a second checkout for the small launcher:

```powershell
git clone https://github.com/sidkolapalli/carla-mcp.git C:\src\carla-mcp
Set-Location C:\src\carla-mcp
git switch feat/windows-support
uv sync --locked

$env:CARLA_MCP_WSL_DISTRO = "Ubuntu-24.04"
$env:CARLA_MCP_WSL_PROJECT = "/home/user/carla-mcp"
$env:CARLA_MCP_WSL_UV = "/home/user/.local/bin/uv"
$env:CARLA_MCP_WSL_OUTPUT_DIR = "/home/user/carla-mcp-output"

uv run carla-mcp-windows --check
uv run python scripts/windows_e2e.py
```

Replace `user`, the distribution, and the `uv` path with values from the WSL
commands above. `--check` requires WSL2, executes a harmless script through the
real Rust runner, requires fully enforced Landlock, verifies persistent output,
and cleans its preflight artifact. The end-to-end script additionally verifies
MCP initialization, tool discovery, safe execution, and a real Landlock denial:
the WSL user can write under the project, but the sandbox cannot create a probe
there and leaves no artifact. Neither command requires a running CARLA simulator.

Configure the Windows MCP client to run:

```text
C:\absolute\path\to\uv.exe --directory C:\src\carla-mcp run carla-mcp-windows
```

The client entry must set these environment variables:

| Variable | Value |
| --- | --- |
| `CARLA_MCP_WSL_DISTRO` | Exact name reported by `wsl --list --verbose` |
| `CARLA_MCP_WSL_PROJECT` | Absolute Linux path to the WSL checkout |
| `CARLA_MCP_WSL_UV` | Absolute Linux path reported by `command -v uv` |
| `CARLA_MCP_WSL_OUTPUT_DIR` | Absolute Linux path for durable output |

Use the Claude Code, Codex, or VS Code configuration shape above, replacing the
program with `carla-mcp-windows` and adding all four variables. Output is
available from Windows under
`\\wsl.localhost\<distribution>\home\<user>\carla-mcp-output`.

If CARLA itself runs on Windows, WSL2 mirrored networking can use
`127.0.0.1`. With WSL2's default NAT networking, pass the Windows host address
as the tool's `host` input instead.

## 5. First Request

Start CARLA, then ask your client:

> Use CARLA MCP to run a script that calls `api.health_check()` and return the
> result without changing the simulation.

The client normally requests approval because `execute_carla_script` is marked
destructive and open-world, unless local client policy explicitly auto-approves
it. A successful response includes connection, version, map, and actor
information.

## Platform and Client Support

| Client environment | Status |
| --- | --- |
| Claude Code on Linux | Supported via stdio |
| Codex CLI/IDE on Linux | Supported via stdio |
| VS Code on Linux | Supported via stdio |
| Windows 11 clients | Experimental via the WSL2 launcher |
| macOS or WSL1 | Unsupported; the runner requires Linux Landlock |
| Web or cloud agents | Unsupported; requires a future HTTP server |

A future Streamable HTTP distribution is separate work because it also requires
authentication, deployment, and a packaged sandbox runner.

## Troubleshooting

- **Client cannot start the server:** use absolute `uv` and checkout paths, then
  run `claude mcp list`, `codex mcp list`, or VS Code's MCP server output.
- **`sandbox_runner_missing`:** rerun the release `cargo build` command above.
- **`sandbox_error` / Landlock not fully enforced:** the kernel lacks a required
  Landlock feature. Execution intentionally fails closed; do not bypass it.
- **`carla-mcp-windows --check` rejects the distribution:** confirm
  `wsl --list --verbose` reports version 2 and `wsl --version` reports a Linux
  6.15-or-newer kernel, then run `wsl --update`. Do not bypass a failed check.
- **Windows cannot reach `wsl.exe`:** install or update WSL from an elevated
  PowerShell prompt, then reopen the client.
- **CARLA API is not importable:** install the API version matching the simulator
  into the checkout's `.venv`, then rerun the import preflight.
- **CARLA connection fails:** start CARLA and verify the host plus RPC, streaming,
  secondary, and Traffic Manager ports.
- **Codex reports a timeout:** raise `tool_timeout_sec`; keep it above the script's
  `timeout_seconds` plus the sandbox wrapper margin.
- **Output is missing:** use relative paths in scripts and inspect
  `CARLA_MCP_OUTPUT_DIR`.

## Official Client References

- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [OpenAI Codex MCP](https://developers.openai.com/codex/mcp)
- [VS Code MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
- [Microsoft WSL commands](https://learn.microsoft.com/windows/wsl/basic-commands)
- [Microsoft WSL networking](https://learn.microsoft.com/windows/wsl/networking)
- [Landlock ABI versions](https://landlock.io/rust-landlock/landlock/enum.ABI.html)
