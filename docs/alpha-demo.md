# Reproducible local alpha demo

This walkthrough demonstrates the finite local toolkit covered by
[#82](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/82). It needs no
provider key. Read the [alpha release notes](alpha-release-notes.md) and
[client prerequisites](client-setup.md#prerequisites) first. Use a dedicated
CARLA instance; the live step creates actors and temporarily changes weather
and the spectator view.

## Prepare a fresh source environment

Run these commands in Bash on supported Linux, with uv and Rust installed and a
kernel capable of enforcing Landlock ABI V7. The public repository's
[`v0.1.0-alpha.1` prerelease](https://github.com/sidkolapalli/carla-agentic-toolkit/releases/tag/v0.1.0-alpha.1)
pins the source checkout below. Its publication commit and CI checks are recorded
in [release readiness](release-readiness.md).

```bash
set -euo pipefail
git clone https://github.com/sidkolapalli/carla-agentic-toolkit.git carla-alpha-demo
cd carla-alpha-demo
git checkout --detach v0.1.0-alpha.1
uv sync --locked --python 3.12
uv pip install --python .venv 'carla==0.9.16'
cargo build --locked --manifest-path sandbox-runner/Cargo.toml --release

export CARLA_AGENTIC_TOOLKIT_HOME="$PWD"
export CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR="$HOME/carla-alpha-demo-output"
export CARLA_AGENTIC_TOOLKIT_STATE_DIR="$HOME/.local/state/carla-alpha-demo"
unset CARLA_AGENTIC_TOOLKIT_ENABLE_SCRIPT_SESSIONS CARLA_AGENTIC_TOOLKIT_MANAGED_EXPERIMENTS
mkdir -p "$CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR"
mkdir -p target/alpha-demo
git rev-parse HEAD > target/alpha-demo/commit.txt
uname -a > target/alpha-demo/kernel.txt
uv run --no-sync python --version > target/alpha-demo/python.txt
uv run --no-sync carla-agentic-toolkit-preflight | tee target/alpha-demo/preflight.json
```

Use a native Linux home directory for private state, including when working
through WSL2. Do not put it under the source checkout or output directory.
`--no-sync` retains the separately installed CARLA Python API. If the simulator
version differs, install its matching API and record that difference. The retained
0.9.16 live evidence used matching server and client versions on earlier source
revisions; it is historical evidence, not a new live run of this release tag.
Keep the commit and reports from your own run with any new recording.

Preflight must report `ok: true` and `ruleset_enforced: true`. Stop on a failed
check; there is no weaker fallback. This guide disables the optional lifecycle
tools because the finite smoke harness checks the default single-tool surface.

## Show sandbox execution and rejection

This part needs no simulator:

```bash
uv run --no-sync python scripts/sandbox_demo.py | tee target/alpha-demo/sandbox.txt
```

The walkthrough executes a script under Landlock, rejects private API traversal
with `script_rejected`, and writes a persistent evidence manifest below the output
directory. It must exit zero. Show the enforced rule and rejected access in a
recording, not just the successful return value.

## Run the live MCP workflow

Start CARLA 0.9.16 with Town10HD_Opt loaded, rendering enabled, and asynchronous
world settings. Use the simulator's actual RPC host and port below; when CARLA
runs on the Windows host under WSL NAT, `127.0.0.1` may not reach it. Keep other
clients idle throughout the run. No Traffic Manager sidecar is needed.

```bash
uv run --no-sync python scripts/live_mcp_smoke.py \
  --confirm-live --host 127.0.0.1 --port 2000 \
  | tee target/alpha-demo/live-mcp.json
```

The harness launches this checkout's MCP stdio entry point. It spawns a tagged
vehicle and a 320×180 camera, accelerates and brakes with manual control,
publishes an image, reads the image resource, and runs rejected-script and timeout
probes. It then reports the cleanup and restoration checks in one JSON record.
Retain stderr as well if diagnosing a failure. The complete command must exit
zero; `set -o pipefail` prevents `tee` from hiding a failure.

The report must contain these successful checks:

```json
{
  "ok": true,
  "landlock_enforced": true,
  "image_content": true,
  "resource_read": true,
  "validator_rejected": true,
  "timeout_cleaned": true,
  "weather_restored": true,
  "spectator_restored": true,
  "leftovers": []
}
```

The real report also records the CARLA versions, map, initial world settings,
capture path/frame, and before/after speed in m/s. Require `after_mps` to exceed
`before_mps`; exact speeds and frame IDs vary. The PNG remains below the configured
output directory. Inspect its recorded path rather than expecting an inline
image from this command-line harness. A nonzero exit or `ok: false` is failed
evidence; preserve the report and resolve it before another attempt.

## Record a short demonstration

After the required checks pass, capture the pinned commit and preflight, the
sandbox rejection, the visible vehicle/camera result, and the final JSON cleanup
checks. Keep the machine-readable files with the video. Do not imply that an
edited video establishes reliability or that this finite demo validates a Jev
comparison. Follow [client setup](client-setup.md) to show the same workflow in a
client, and verify that the configured server points at this release checkout.

For Windows clients, follow the explicit WSL2 setup and preflight in that guide
before using the harness's `--windows` flag. The validated WSL2/Linux path is
documented in [release readiness](release-readiness.md); it is not a claim of
native Windows execution. Synchronous sensor ownership, density limitations,
Traffic Manager prerequisites, incomplete-cleanup reporting and other boundaries
are listed in the [release notes](alpha-release-notes.md#known-limitations-and-release-conditions).
