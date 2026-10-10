# Client Setup

CARLA Agentic Toolkit runs locally through stdio. Clients can launch its Docker image,
Linux clients can run it directly, and Windows 11 clients can use
`carla-agentic-toolkit-windows` to run from source inside WSL2. There is no remote endpoint.

## Prerequisites

CARLA and CARLA Agentic Toolkit are separate programs. CARLA is the simulator; this
repository is the MCP server that connects to it.

| Component | Linux | Windows 11 |
| --- | --- | --- |
| CARLA simulator | Linux host or another reachable machine | Windows host or another reachable machine |
| MCP server, CARLA Python API, and Rust sandbox | Linux | WSL2 |
| MCP client | Linux | Windows |
| `carla-agentic-toolkit-windows` launcher | Not needed | Windows |

Before starting, install or confirm:

- Git.
- Docker for the packaged path; or Python 3.12,
  [uv](https://docs.astral.sh/uv/), Rust, and a C/C++ linker (`build-essential`
  on Ubuntu) for the source path. Pin Python 3.12: newer Linux releases may
  otherwise select Python 3.14, which CARLA 0.9.16 does not support.
- Linux kernel 6.15 or newer, which provides the Landlock ABI V7 required by the
  sandbox. On Windows this kernel must be supplied by WSL2.
- A reachable CARLA server and a matching CARLA Python API release in the MCP
  virtual environment. A 0.9.16 server requires `carla==0.9.16`; see
  [version matching](#version-matching) for diagnostics and source-build suffixes.
- Free, non-reserved CARLA ports. The defaults are RPC 2000, streaming 2001,
  secondary 2002, and Traffic Manager 8000. WSL2/Hyper-V may reserve the
  defaults on Windows even when `netstat` shows no listener.

The official CARLA packaged-release requirements are Windows 10/11 or Ubuntu
20.04/22.04, about 20 GB of disk, and a dedicated GPU equivalent to an NVIDIA
RTX 2070 with at least 8 GB VRAM recommended. Download CARLA from its
[official release page](https://carla.readthedocs.io/en/0.9.16/download/), not
from this repository.

## CARLA release compatibility

Release inventory checked on **2026-10-05**. CARLA has parallel engine lines:
[0.9.16 is GitHub's latest published release](https://github.com/carla-simulator/carla/releases/tag/0.9.16)
on UE4.26; [0.10.0 is the published UE5.5 release](https://github.com/carla-simulator/carla/releases/tag/0.10.0).
The larger version number alone does not describe this toolkit's compatibility.

The retained installation, MCP, managed rules/Jev, sensor, cancellation and cleanup
validation used **CARLA server/API 0.9.16 and Python 3.12**, with the toolkit on
Linux/WSL2. Docker defaults to `CARLA_VERSION=0.9.16`. That is the demonstrated
configuration; see the [release evidence](release-readiness.md).

**UE5 has scoped experimental validation.** The 2026-10-05
[live report](evidence/ue5-validation-2026-10-05/README.md) covers the official
Windows 0.10.0 simulator and matching Linux client in WSL2. Core MCP driving,
camera/resource delivery, managed rules/Jev merges, persistent follow-ups,
cancellation and final cleanup checks passed after compatibility fixes.
One earlier persistent close returned a missing cleanup report. The later
[cleanup worker lifetime fix](evidence/cleanup-worker-2026-10-05/README.md)
retains the native client through report publication and passed regression,
live close and recovery checks. The exact upstream native crash was not proven.
This is not full production support or a claim that every CARLA API works on UE5.

Install the matching **Linux** Python 3.12 wheel inside Linux/WSL2, even when the
simulator runs on Windows. Obtain it from the matching Linux simulator package;
the Windows wheel cannot run inside WSL. Follow the
[official client-installation guide](https://carla-ue5.readthedocs.io/en/latest/start_quickstart/#install-client-library):

```bash
uv pip install --python .venv /path/to/Carla-0.10.0-Linux-Shipping/PythonAPI/carla/dist/carla-0.10.0-cp312-cp312-linux_x86_64.whl
```

Keep the existing Rust/Landlock setup and private state directory. Start
`CarlaUnreal.exe` on a free RPC/streaming port block and make those ports reachable
from the toolkit. This machine's validation used a temporary local stdio relay
because Windows blocked inbound WSL connections to the new executable; that relay
is a test harness, not a shipped networking feature. Direct UE5 WSL-to-Windows
networking still needs local configuration and verification.

For a dedicated instance, replace the host/port below with its reachable endpoint:

```bash
uv run --no-sync python scripts/live_mcp_smoke.py --confirm-live \
  --host 127.0.0.1 --port 3200 --vehicle-blueprint vehicle.lincoln.mkz --skip-weather
```

`--skip-weather` reports `weather_tested: false`; it does not claim a weather pass.
CARLA 0.10.0 has fixed daylight weather, no Light Manager and other
[documented upstream limitations](https://carla.org/2024/12/19/release-0.10.0/).
Capability reports inspect method presence, which alone does not prove behavior.

Use `town10-merge-ue5-v1` and the
[UE5 experiment specifications](managed-experiments.md#ue5-fixture) for managed
experiments. The original Tesla fixture remains unchanged; its vehicle is absent
from UE5. Keep UE4 and UE5 results in separate comparisons. Docker continues to
default to 0.9.16; changing its version argument is not a validated UE5 image.

### Version matching

`api.health_check()` retains the full available `client_version` and
`server_version` strings. Its `warnings`, and the warnings in world-state
results such as `api.get_world_state()`, compare the leading `X.Y.Z` release
prefixes. Same-release source-build suffixes are accepted: for example,
`0.9.16-custom` and `0.9.16` match this release check, while their full strings
remain available for diagnosis. Different release prefixes produce a structured
warning naming both versions. A missing, unreadable, or unparseable version is
reported distinctly as unknown compatibility, not mislabeled as a mismatch.

For mismatched or unknown compatibility, health returns version-only diagnostics
without attaching to a world. `connected: false` means a compatible world/session
connection was not verified; it is not proof that the server is offline. The
unobserved `current_map`, `settings`, and `actor_counts` are `null`, and a warning
explains that world inspection was skipped. Check health's connection status and
compatibility warnings before calling `api.get_world_state()` or other world
operations. Those operations are not automatically protected by the health check.

Managed startup records available full versions in trace metadata under
`environment.carla_client` and `environment.carla_server`. It refuses both
mismatched and unknown compatibility before constructing the policy or session,
or changing the simulator. See the [managed guide](managed-experiments.md).
A matching release prefix does not guarantee every native capability or imply
a fixture-to-engine catalog match; the existing capability and fixture checks
still apply. Live acceptance with an incompatible CARLA wheel and server remains
separate from managed startup: Windows-native checks on 2026-10-09 verified full
health with matching 0.9.16 releases and version-only health from a genuine 0.10.0
client against the 0.9.16 server, with zero world-inspection calls. Linux managed
startup acceptance remains pending.

## Docker MCP server

The image contains the MCP server, CARLA Python API, and compiled Rust sandbox.
It deliberately does not contain the CARLA simulator: keep CARLA native so its
GPU-rendered window remains visible and recordable.

The headless runtime image has no shell, compiler, or package manager. Run its
installed commands directly; `docker exec ... sh` and runtime package installs
are unavailable. The image includes the default dependencies only. For the
optional Jev SDK, use the [managed experiment source setup](managed-experiments.md#optional-jev-selection).

Build the image with the Python API version matching the simulator, create its
durable output volume, and run the real sandbox preflight:

```bash
git clone https://github.com/sidkolapalli/carla-agentic-toolkit.git
cd carla-agentic-toolkit
docker build --build-arg CARLA_VERSION=0.9.16 -t carla-agentic-toolkit .
docker volume create carla-agentic-toolkit-output

docker run --rm --read-only --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --mount source=carla-agentic-toolkit-output,target=/output \
  carla-agentic-toolkit carla-agentic-toolkit-preflight
```

Do not continue unless preflight reports `"ok": true` and
`"ruleset_enforced": true`. The container fails closed when the host kernel or
container runtime cannot enforce Landlock ABI V7. A Linux 6.15+ kernel and a
runtime that permits the `landlock_*` syscalls are required.

Use this as the MCP stdio process. `-i` is required; do not add `-t`:

```bash
docker run --rm -i --read-only --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --add-host=host.docker.internal:host-gateway \
  --mount source=carla-agentic-toolkit-output,target=/output carla-agentic-toolkit
```

For Claude Code:

```bash
claude mcp add --scope local --transport stdio carla -- \
  docker run --rm -i --read-only --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --add-host=host.docker.internal:host-gateway \
  --mount source=carla-agentic-toolkit-output,target=/output carla-agentic-toolkit
```

For Codex:

```bash
codex mcp add carla -- \
  docker run --rm -i --read-only --security-opt=no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  --add-host=host.docker.internal:host-gateway \
  --mount source=carla-agentic-toolkit-output,target=/output carla-agentic-toolkit
```

In prompts, tell the agent to connect to CARLA at
`host.docker.internal:<rpc-port>`; for example,
`host.docker.internal:3000`. The sandbox permits that RPC port, its next two
streaming ports, Traffic Manager port 8000, and any additional Traffic Manager
ports explicitly supplied to the tool. Output persists in the
`carla-agentic-toolkit-output` Docker volume.

## 1. Prepare the Server from source

```bash
git clone https://github.com/sidkolapalli/carla-agentic-toolkit.git
cd carla-agentic-toolkit
uv sync --locked --python 3.12
# Replace 0.9.16 if your simulator uses another version.
uv pip install --python .venv "carla==0.9.16"
cargo build --locked --manifest-path sandbox-runner/Cargo.toml --release

export CARLA_AGENTIC_TOOLKIT_HOME="$(pwd)"
export CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR="$HOME/carla-agentic-toolkit-output"
export UV_BIN="$(command -v uv)"
mkdir -p "$CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR"
```

Use absolute paths in client configuration. Desktop and agent processes do not
always inherit the same `PATH` as an interactive shell.

Confirm the CARLA package and sandbox before configuring a client:

```bash
"$UV_BIN" --directory "$CARLA_AGENTIC_TOOLKIT_HOME" run python -c \
  'import carla; print(carla.__file__)'

CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR="$CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR" \
  "$UV_BIN" --directory "$CARLA_AGENTIC_TOOLKIT_HOME" run python - <<'PY'
from carla_agentic_toolkit.sandbox import execute_script

outcome = execute_script("result = 1", timeout_seconds=2)
assert outcome.ok, outcome.to_dict()
print("Sandbox ready:", outcome.sandbox)
PY
```

## 2. Claude Code

The following command was validated with Claude Code 2.1.207:

```bash
claude mcp add --scope local --transport stdio carla \
  -e "CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR=$CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR" -- \
  "$UV_BIN" --directory "$CARLA_AGENTIC_TOOLKIT_HOME" run carla-agentic-toolkit

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
codex mcp add carla --env "CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR=$CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR" -- \
  "$UV_BIN" --directory "$CARLA_AGENTIC_TOOLKIT_HOME" run carla-agentic-toolkit

codex mcp list
```

Codex stores this in `~/.codex/config.toml`, shared by Codex CLI, its IDE
extension, and ChatGPT desktop on the same host. CARLA operations can exceed
Codex's default 60-second tool timeout, so the full entry should look like:

```toml
[mcp_servers.carla]
command = "/absolute/path/to/uv"
args = ["--directory", "/absolute/path/to/carla-agentic-toolkit", "run", "carla-agentic-toolkit"]
startup_timeout_sec = 30
tool_timeout_sec = 120
default_tools_approval_mode = "prompt"

[mcp_servers.carla.env]
CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR = "/absolute/path/to/carla-agentic-toolkit-output"
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
        "/absolute/path/to/carla-agentic-toolkit",
        "run",
        "carla-agentic-toolkit"
      ],
      "env": {
        "CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR": "/absolute/path/to/carla-agentic-toolkit-output"
      }
    }
  }
}
```

Use **MCP: List Servers** to start, stop, and inspect it. Do not enable VS Code's
outer MCP sandbox by default: CARLA Agentic Toolkit already applies Landlock to scripts, and
an additional parent sandbox can block the CARLA TCP connection.

## Windows 11 with WSL2

The Windows launcher uses `wsl.exe --exec` directly. It never runs
agent-authored code with Windows Python and never falls back to an unsandboxed
path.

First prepare the Linux runtime inside WSL2:

```powershell
wsl --install --distribution Ubuntu-24.04
wsl --list --verbose
wsl --update
wsl --version
```

The Rust runner hard-requires Landlock ABI V7, introduced with Linux 6.15.
Older WSL2 kernels, including 6.6, fail closed because they cannot enforce the
same filesystem and TCP rules. `carla-agentic-toolkit-windows --check` reports the actual
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

git clone https://github.com/sidkolapalli/carla-agentic-toolkit.git
cd carla-agentic-toolkit
git switch main
uv sync --locked --python 3.12
# Replace 0.9.16 if your simulator uses another version.
uv pip install --python .venv "carla==0.9.16"
cargo build --locked --manifest-path sandbox-runner/Cargo.toml --release
mkdir -p "$HOME/carla-agentic-toolkit-output"

command -v uv
pwd
```

The clone command runs Git inside WSL. While this repository is private,
authenticate Git inside WSL with an account that has repository access. Keep the
working checkout in the WSL filesystem rather than running it under `/mnt/c`.

On Windows, prepare a second checkout for the small launcher:

```powershell
New-Item -ItemType Directory -Force "$HOME\src" | Out-Null
git clone https://github.com/sidkolapalli/carla-agentic-toolkit.git "$HOME\src\carla-agentic-toolkit"
Set-Location "$HOME\src\carla-agentic-toolkit"
git switch main
uv sync --locked --python 3.12

$env:CARLA_AGENTIC_TOOLKIT_WSL_DISTRO = "Ubuntu-24.04"
$env:CARLA_AGENTIC_TOOLKIT_WSL_PROJECT = "/home/user/carla-agentic-toolkit"
$env:CARLA_AGENTIC_TOOLKIT_WSL_UV = "/home/user/.local/bin/uv"
$env:CARLA_AGENTIC_TOOLKIT_WSL_OUTPUT_DIR = "/home/user/carla-agentic-toolkit-output"
# Optional: server-side directory when CARLA runs on Windows.
$env:CARLA_AGENTIC_TOOLKIT_RECORDER_DIR = "E:/CARLA_0.9.16/recordings"

uv run carla-agentic-toolkit-windows --check
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
C:\absolute\path\to\uv.exe --directory C:\Users\you\src\carla-agentic-toolkit run carla-agentic-toolkit-windows
```

The client entry must set these environment variables:

| Variable | Value |
| --- | --- |
| `CARLA_AGENTIC_TOOLKIT_WSL_DISTRO` | Exact name reported by `wsl --list --verbose` |
| `CARLA_AGENTIC_TOOLKIT_WSL_PROJECT` | Absolute Linux path to the WSL checkout |
| `CARLA_AGENTIC_TOOLKIT_WSL_UV` | Absolute Linux path reported by `command -v uv` |
| `CARLA_AGENTIC_TOOLKIT_WSL_OUTPUT_DIR` | Absolute Linux path for durable output |
| `CARLA_AGENTIC_TOOLKIT_RECORDER_DIR` | Optional absolute recorder directory understood by the CARLA simulator host |

Use the Claude Code, Codex, or VS Code configuration shape above, replacing the
program with `carla-agentic-toolkit-windows` and adding the four required variables. Output is
available from Windows under
`\\wsl.localhost\<distribution>\home\<user>\carla-agentic-toolkit-output`.

CARLA opens recorder files on the simulator host, not in WSL. With
`CARLA_AGENTIC_TOOLKIT_RECORDER_DIR` set, a relative `record_episode("run.log")` request is
sent under that directory and success reports the exact path CARLA accepted.
An empty CARLA response becomes `record_episode_failed`; no response claims the
file was copied into `CARLA_AGENTIC_TOOLKIT_WSL_OUTPUT_DIR`.
The optional `additional_data=True` recorder flag includes extra velocities,
bounding boxes, traffic-light timings and vehicle physics controls; its default
is `False`, matching CARLA. See [recorder options and query categories](script-workflows.md#put-outputs-on-the-correct-host).

If CARLA itself runs on Windows, WSL2 mirrored networking can use
`127.0.0.1`. With WSL2's default NAT networking, get the Windows host address
from inside WSL and pass it as the tool's `host` input:

```bash
ip route show default | awk '{print $3}'
```

### Verify in order

1. Run `uv run carla-agentic-toolkit-windows --check` from Windows. This proves WSL2,
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
   cd "$HOME/carla-agentic-toolkit"
   uv run python scripts/live_smoke.py --host 172.18.112.1 --port 3000 \
     --reset-existing --vehicle-count 4
   ```

Git Bash rewrites Linux-looking environment values such as `/home/user` before
passing them to Windows programs. Prefer PowerShell for launcher commands. If
Git Bash is required, prefix the command with `MSYS_NO_PATHCONV=1`.

### Traffic Manager

The sandbox can connect to Traffic Manager but cannot bind a TCP server port or
step a synchronous manager in another process. Toolkit Traffic Manager workflows
are asynchronous-only. Before using autopilot or traffic tuning, keep a trusted
CARLA client running outside the sandbox on a dedicated toolkit-owned Traffic
Manager port. Do not share that manager with unrelated clients: global settings
and their cleanup targets affect every vehicle using the same manager.

For example, inside the Linux environment with the matching CARLA Python API:

```python
import time
import carla

client = carla.Client("127.0.0.1", 2000)  # Use the reachable simulator endpoint.
client.set_timeout(10.0)
manager = client.get_trafficmanager(8000)
manager.set_synchronous_mode(False)
try:
    while True:
        time.sleep(1.0)
except KeyboardInterrupt:
    pass
```

This starts no vehicles. Keep this process alive while sandbox scripts use
Traffic Manager on port 8000. For other ports, also include them in the MCP
tool's `traffic_manager_ports` argument. With Windows CARLA and WSL NAT, use the
Windows host address and an available RPC port as described above. The live
MCP smoke test uses direct throttle and brake controls, so it needs no sidecar.

Toolkit traffic creation and configuration workflows reject a synchronous world
before mutation. `api.configure_traffic_manager({"synchronous_mode": True})` is
rejected in either world mode. Synchronous Traffic Manager support remains
tracked in
[#26](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/26). Stop the
background traffic controller completely before enabling or restoring synchronous
world settings.

Seeds do not make this asynchronous traffic reproducible; CARLA's
[deterministic Traffic Manager mode requires synchronous operation](https://carla.readthedocs.io/en/0.9.16/adv_traffic_manager/#deterministic-mode).
Setting a Traffic Manager seed also
[resets all traffic lights](https://github.com/carla-simulator/carla/blob/0.9.16/LibCarla/source/carla/trafficmanager/TrafficManagerLocal.cpp#L449-L453),
including when cleanup sets seed 0. Cleanup restores only attempted global
settings to [declared targets](script-workflows.md#keep-traffic-and-simulation-timing-explicit),
not to an unknown shared client's original values. Live CARLA 0.9.16 acceptance
with this dedicated sidecar remains pending.

## 5. First Request

Start CARLA, then ask your client:

> Use CARLA Agentic Toolkit to run a script that calls `api.health_check()` and return the
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
- **`carla-agentic-toolkit-windows --check` rejects the distribution:** confirm
  `wsl --list --verbose` reports version 2 and `wsl --version` reports a Linux
  6.15-or-newer kernel, then run `wsl --update`. Do not bypass a failed check.
- **Windows cannot reach `wsl.exe`:** install or update WSL from an elevated
  PowerShell prompt, then reopen the client.
- **`carla_api_unavailable`:** the message retains the original import or ABI
  error. Install the API matching the simulator and Python version into the
  checkout's `.venv`, resolve missing native libraries, then rerun the import
  preflight. This is a local client failure, not a simulator connection failure.
- **`traffic_manager_port_not_allowed`:** the message names the requested TM
  port. Include it in `traffic_manager_ports` when creating the one-shot
  execution or persistent session; script TM methods still require the same
  explicit port. Rejected ports fail before any CARLA connection or mutation.
- **`traffic_manager_unavailable`:** CARLA reported a TM bind error for the
  named port. Start a dedicated TM sidecar outside the sandbox for this
  simulator, keep it running, or select its actual port. The failure can also
  reflect another native bind problem; it does not prove no server exists.
  Do not grant sandbox bind permissions to work around it.
- **`traffic_manager_network_policy_error`:** connect-port evidence supplied by
  the sandbox runner is malformed. Rebuild the reviewed runner; access fails
  closed rather than treating unreadable policy as unrestricted.
- **`invalid_request`:** execution inputs are rejected before filesystem or
  process setup. `host` must be non-empty; the base RPC port is `1..65533` so
  CARLA's two adjacent ports remain valid; Traffic Manager ports are
  `1..65535`; and `timeout_seconds` is finite and in `(0, 3600]`. Hostnames are
  resolved by CARLA, while Landlock limits ports rather than destination hosts.
- **`carla_connection_error`:** start CARLA and verify the reported host and RPC
  port plus the adjacent streaming/secondary ports and Traffic Manager port. The
  Rust watchdog allows two seconds beyond CARLA's client deadline so this error
  can be serialized; genuine script budget exhaustion remains `script_timeout`.
- **`CARLA_AGENTIC_TOOLKIT_WSL_PROJECT must be an absolute Linux path` in Git Bash:** use
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
  `CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR`.

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
