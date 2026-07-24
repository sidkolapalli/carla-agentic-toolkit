# Client Setup

CARLA MCP is currently a local Linux stdio server. Every client launches the
same source checkout with `uv`; there is no remote endpoint or standalone wheel
yet.

## 1. Prepare the Server

```bash
git clone https://github.com/akon2997/carla-mcp.git
cd carla-mcp
uv sync
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
claude mcp add --scope user --transport stdio carla \
  -e "CARLA_MCP_OUTPUT_DIR=$CARLA_MCP_OUTPUT_DIR" -- \
  "$UV_BIN" --directory "$CARLA_MCP_HOME" run carla-mcp

claude mcp list
```

`--` is required so the remaining arguments go to `uv`. User scope makes the
server available in every Claude Code project. Use `--scope local` instead to
limit it to the current project, or `--scope project` to create a shareable
`.mcp.json`; project-scoped servers require workspace approval.

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
| macOS or Windows clients | Unsupported; the runner requires Linux Landlock |
| WSL2 | Unverified; requires all requested Landlock features |
| Web or cloud agents | Unsupported; requires a future HTTP server |

A future Streamable HTTP distribution is separate work because it also requires
authentication, deployment, and a packaged sandbox runner.

## Troubleshooting

- **Client cannot start the server:** use absolute `uv` and checkout paths, then
  run `claude mcp list`, `codex mcp list`, or VS Code's MCP server output.
- **`sandbox_runner_missing`:** rerun the release `cargo build` command above.
- **`sandbox_error` / Landlock not fully enforced:** the kernel lacks a required
  Landlock feature. Execution intentionally fails closed; do not bypass it.
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
