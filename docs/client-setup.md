# Client Setup

CARLA MCP runs locally through stdio. Linux clients launch it directly; Windows
11 clients use `carla-mcp-windows` to launch the entire server and its existing
Rust/Landlock sandbox inside WSL2. There is no remote endpoint or standalone
wheel yet.

## Prerequisites

CARLA and CARLA MCP are separate programs. CARLA is the simulator; this
repository is the MCP server that connects to it.

| Component | Linux | Windows 11 |
| --- | --- | --- |
| CARLA simulator | Linux host or another reachable machine | Windows host or another reachable machine |
| MCP server, CARLA Python API, and Rust sandbox | Linux | WSL2 |
| MCP client | Linux | Windows |
| `carla-mcp-windows` launcher | Not needed | Windows |

Before starting, install or confirm:

- Git and access to this private repository. Authenticate Git separately inside
  WSL because Windows credentials are not inherited automatically.
- Python 3.12, [uv](https://docs.astral.sh/uv/), a Rust toolchain, and a C/C++
  linker (`build-essential` on Ubuntu). Pin Python 3.12: newer Linux releases
  may otherwise select Python 3.14, which CARLA 0.9.16 does not support.
- Linux kernel 6.15 or newer, which provides the Landlock ABI V7 required by the
  sandbox. On Windows this kernel must be supplied by WSL2.
- A reachable CARLA server and the same CARLA Python API version in the MCP
  virtual environment. A 0.9.16 server requires `carla==0.9.16`.
- Free, non-reserved CARLA ports. The defaults are RPC 2000, streaming 2001,
  secondary 2002, and Traffic Manager 8000. WSL2/Hyper-V may reserve the
  defaults on Windows even when `netstat` shows no listener.

The official CARLA packaged-release requirements are Windows 10/11 or Ubuntu
20.04/22.04, about 20 GB of disk, and a dedicated GPU equivalent to an NVIDIA
RTX 2070 with at least 8 GB VRAM recommended. Download CARLA from its
[official release page](https://carla.readthedocs.io/en/0.9.16/download/), not
from this repository.

## 1. Prepare the Server

```bash
git clone https://github.com/sidkolapalli/carla-mcp.git
cd carla-mcp
uv sync --locked --python 3.12
# Replace 0.9.16 if your simulator uses another version.
uv pip install --python .venv "carla==0.9.16"
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
sudo apt update
sudo apt install -y build-essential curl git
curl -LsSf https://astral.sh/uv/install.sh | sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | \
  sh -s -- -y --profile minimal
source "$HOME/.local/bin/env"
source "$HOME/.cargo/env"

# Reuse Git for Windows credentials when it is installed at this path.
if test -x "/mnt/c/Program Files/Git/mingw64/bin/git-credential-manager.exe"; then
  git config --global credential.helper \
    "/mnt/c/Program\ Files/Git/mingw64/bin/git-credential-manager.exe"
fi
# This must succeed before cloning the private repository.
git ls-remote https://github.com/sidkolapalli/carla-mcp.git HEAD
git clone https://github.com/sidkolapalli/carla-mcp.git
cd carla-mcp
git switch feat/windows-support
uv sync --locked --python 3.12
# Replace 0.9.16 if your simulator uses another version.
uv pip install --python .venv "carla==0.9.16"
cargo build --locked --manifest-path sandbox-runner/Cargo.toml --release
mkdir -p "$HOME/carla-mcp-output"

command -v uv
pwd
```

The helper command above reuses an authenticated Git for Windows installation.
If it is installed elsewhere, update the path or authenticate Git inside WSL by
another method. Keep the working checkout in the WSL filesystem rather than
running it under `/mnt/c`.

On Windows, prepare a second checkout for the small launcher:

```powershell
New-Item -ItemType Directory -Force "$HOME\src" | Out-Null
git clone https://github.com/sidkolapalli/carla-mcp.git "$HOME\src\carla-mcp"
Set-Location "$HOME\src\carla-mcp"
git switch feat/windows-support
uv sync --locked --python 3.12

$env:CARLA_MCP_WSL_DISTRO = "Ubuntu-24.04"
$env:CARLA_MCP_WSL_PROJECT = "/home/user/carla-mcp"
$env:CARLA_MCP_WSL_UV = "/home/user/.local/bin/uv"
$env:CARLA_MCP_WSL_OUTPUT_DIR = "/home/user/carla-mcp-output"
# Optional: server-side directory when CARLA runs on Windows.
$env:CARLA_MCP_RECORDER_DIR = "E:/CARLA_0.9.16/recordings"

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
C:\absolute\path\to\uv.exe --directory C:\Users\you\src\carla-mcp run carla-mcp-windows
```

The client entry must set these environment variables:

| Variable | Value |
| --- | --- |
| `CARLA_MCP_WSL_DISTRO` | Exact name reported by `wsl --list --verbose` |
| `CARLA_MCP_WSL_PROJECT` | Absolute Linux path to the WSL checkout |
| `CARLA_MCP_WSL_UV` | Absolute Linux path reported by `command -v uv` |
| `CARLA_MCP_WSL_OUTPUT_DIR` | Absolute Linux path for durable output |
| `CARLA_MCP_RECORDER_DIR` | Optional absolute recorder directory understood by the CARLA simulator host |

Use the Claude Code, Codex, or VS Code configuration shape above, replacing the
program with `carla-mcp-windows` and adding the four required variables. Output is
available from Windows under
`\\wsl.localhost\<distribution>\home\<user>\carla-mcp-output`.

CARLA opens recorder files on the simulator host, not in WSL. With
`CARLA_MCP_RECORDER_DIR` set, a relative `record_episode("run.log")` request is
sent under that directory and success reports the exact path CARLA accepted.
An empty CARLA response becomes `record_episode_failed`; no response claims the
file was copied into `CARLA_MCP_WSL_OUTPUT_DIR`.

If CARLA itself runs on Windows, WSL2 mirrored networking can use
`127.0.0.1`. With WSL2's default NAT networking, get the Windows host address
from inside WSL and pass it as the tool's `host` input:

```bash
ip route show default | awk '{print $3}'
```

### Verify in order

1. Run `uv run carla-mcp-windows --check` from Windows. This proves WSL2,
   Landlock, the Rust runner, and persistent output without requiring CARLA.
2. Run `uv run python scripts/windows_e2e.py`. This additionally proves MCP
   stdio and a real denied filesystem write, still without requiring CARLA.
3. Download and extract the Windows package matching the Python API version.
   Check whether Windows has reserved CARLA's default ports:

   ```powershell
   netsh interface ipv4 show excludedportrange protocol=tcp
   ```

   If 2000-2002 are available, start `CarlaUE4.exe` normally. If they fall in an
   excluded range, use another free three-port block such as 3000-3002:

   ```powershell
   Set-Location "C:\path\to\CARLA_0.9.16"
   .\CarlaUE4.exe -carla-rpc-port=3000
   ```

4. With CARLA listening, run the live check inside WSL. For default NAT, replace
   the example host with the address returned by the command above:

   ```bash
   cd "$HOME/carla-mcp"
   uv run python scripts/live_smoke.py --host 172.18.112.1 --port 3000 \
     --reset-existing --vehicle-count 4
   ```

Git Bash rewrites Linux-looking environment values such as `/home/user` before
passing them to Windows programs. Prefer PowerShell for launcher commands. If
Git Bash is required, prefix the command with `MSYS_NO_PATHCONV=1`.

## 5. First Request

Start CARLA, then ask your client:

> Use CARLA MCP to run a script that calls `api.health_check()` and return the
> result without changing the simulation.

The client normally requests approval because `execute_carla_script` is marked
destructive and open-world, unless local client policy explicitly auto-approves
it. A successful response includes connection, version, map, and actor
information. If CARLA uses a non-default endpoint, include it in the request,
for example `host="172.18.112.1"` and `port=3000`.

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
- **`invalid_request`:** execution inputs are rejected before filesystem or
  process setup. `host` must be non-empty; the base RPC port is `1..65533` so
  CARLA's two adjacent ports remain valid; Traffic Manager ports are
  `1..65535`; and `timeout_seconds` is finite and in `(0, 3600]`. Hostnames are
  resolved by CARLA, while Landlock limits ports rather than destination hosts.
- **`carla_connection_error`:** start CARLA and verify the reported host and RPC
  port plus the adjacent streaming/secondary ports and Traffic Manager port. The
  Rust watchdog allows two seconds beyond CARLA's client deadline so this error
  can be serialized; genuine script budget exhaustion remains `script_timeout`.
- **`CARLA_MCP_WSL_PROJECT must be an absolute Linux path` in Git Bash:** use
  PowerShell or set `MSYS_NO_PATHCONV=1` so Git Bash does not rewrite `/home/...`
  as a Windows path.
- **`CarlaUE4.exe` shows `Fatal error` immediately:** run `netsh interface ipv4
  show excludedportrange protocol=tcp`. If Windows reserved 2000-2002, launch
  with `-carla-rpc-port=3000` and pass `port=3000` to the MCP tool. This can
  happen even when `netstat` shows no listener. For other crashes, follow
  CARLA's official FAQ and inspect `%LOCALAPPDATA%\CarlaUE4\Saved\Crashes`.
- **Codex reports a timeout:** raise `tool_timeout_sec`; keep it above the script's
  `timeout_seconds` plus the sandbox wrapper margin.
- **Output is missing:** use relative paths in scripts and inspect
  `CARLA_MCP_OUTPUT_DIR`.

## Official References

- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [OpenAI Codex MCP](https://developers.openai.com/codex/mcp)
- [VS Code MCP configuration](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
- [Microsoft WSL commands](https://learn.microsoft.com/windows/wsl/basic-commands)
- [Microsoft WSL networking](https://learn.microsoft.com/windows/wsl/networking)
- [Landlock ABI versions](https://landlock.io/rust-landlock/landlock/enum.ABI.html)
- [CARLA 0.9.16 quick start](https://carla.readthedocs.io/en/0.9.16/start_quickstart/)
- [CARLA rendering options](https://carla.readthedocs.io/en/0.9.16/adv_rendering_options/)
- [CARLA FAQ](https://carla.readthedocs.io/en/0.9.16/build_faq/)
