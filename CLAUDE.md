# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` applies to Claude as well. It sets the trust model for issue, PR, log and tool
content, and the one-issue-at-a-time TDD workflow. This file does not repeat it.

## Platform

The security boundary is Linux Landlock (ABI V7), so the Rust runner, the MCP server and the
full gate run on Linux or WSL2. On a Windows host, `ruff`, `ty` and pytest collection work. The
Rust crate does not build there (it uses `std::os::unix`), the real Landlock preflight test
skips, and `make radon` needs a POSIX shell. `carla-agentic-toolkit-windows`
(`windows_launcher.py`) only starts the WSL2-hosted server from Windows.

If one checkout is used from both Windows and WSL, keep a separate Linux environment (for
example `UV_PROJECT_ENVIRONMENT=.venv/wsl`). Otherwise uv run from WSL replaces the Windows
`.venv`.

## Commands

```bash
uv sync --locked --all-extras                                  # what CI installs (extras = optional Jev SDK)
cargo build --locked --manifest-path sandbox-runner/Cargo.toml --release
make check                                                     # required gate: lint type radon rust-check test
uv run ruff format --check .                                   # CI runs this too; `make format` fixes Python + Rust
```

Single targets are `make lint`, `make type`, `make radon`, `make rust-check` and `make test`.
Focused tests:

```bash
uv run pytest tests/test_script_api.py
uv run pytest tests/test_script_api.py::test_name
uv run pytest -k "lease and not live"
cargo test --locked --manifest-path sandbox-runner/Cargo.toml <name>
```

Tests use mock CARLA adapters and need no simulator, `carla` wheel, or Jev key. Running the server
does need them:

```bash
uv pip install --python .venv "carla==0.9.16"     # must match the server; not in uv.lock, so `uv sync` removes it
uv run --no-sync carla-agentic-toolkit-preflight  # proceed only on ok: true and ruleset_enforced: true
uv run python scripts/live_smoke.py --reset-existing --vehicle-count 12   # dedicated simulator only
```

### What the gate enforces

- Ruff runs with `select = ["ALL"]` at line length 100. Every suppression in `src/` is per line
  and gives a reason, for example `# noqa: PLR0913 - Public endpoint permission inputs.`
  `tests/test_noqa_reasons.py` fails on a bare one.
- Radon fails on any function below complexity grade A and any module below maintainability
  grade A (`-n B`). This is why the package has about 150 small single-purpose modules. Split
  code into a new module rather than growing an existing one past the limit.
- `ty` is configured to ignore unresolved imports, since `carla` is not in the lock.

## Architecture

### Three execution modes behind one lease

1. **Finite scripts** (the default `execute_carla_script` tool in `server.py`). `sandbox.py`
   takes the `SimulatorLease`, creates a per-run work directory, and launches the Rust runner.
   The runner (`sandbox-runner/src/main.rs`) applies the Landlock filesystem and port rules,
   sets rlimits, and starts a cleanup guardian (`guardian.rs`). It then runs `script_runner.py`
   with a cleared environment. The child AST-checks the code against forbidden names and imports
   (defense in depth, not the boundary), injects `api` (a `CarlaScriptApi`), and emits `result`
   plus snapshots as JSON. Back in the parent, `sandbox.py` decodes that JSON. On failure or
   timeout it destroys only the journaled actors, and it verifies the restored settings. Captures
   marked `publish=True` are returned as MCP image content through `output_content.py`.
2. **Persistent sessions**, enabled with `CARLA_AGENTIC_TOOLKIT_ENABLE_SCRIPT_SESSIONS=1`, which
   adds the `carla_script_session` tool. The code is in `session_mcp.py`, `sandbox_session*.py`
   and `persistent_*.py`. Sessions use the same sandbox, validator and serialization, keep a
   long-lived namespace, and talk over bounded file IPC (`session_protocol.py`).
3. **Managed experiments**, enabled with `CARLA_AGENTIC_TOOLKIT_MANAGED_EXPERIMENTS=1`, which adds
   the `managed_experiment` tool. The CLI is `carla-agentic-toolkit-experiment`
   (`managed_cli.py`). This is trusted Python outside Landlock: supervisor, then worker, then
   `ManagedSession`/`managed_engine.py`. A strict numerical `ExperimentSpec` (`managed_spec.py`)
   drives it, never generated code. The scenarios are merge (`merge_*`) and route (`route_*`).
   The policy is either the no-key rules baseline or optional Jev (`managed_jev*`), which needs
   the `jev` extra and `TYPESAFE_API_KEY` in the trusted environment only.

All three modes coordinate through `simulator_lease.py`, which fails closed. After a failed
cleanup the endpoint stays dirty or quarantined until trusted recovery
(`carla-agentic-toolkit-recover-scripts`, or managed recovery) verifies it is clean. Never clear
recovery markers to unblock a run. `docs/managed-architecture.md` lists the managed invariants.

### Script API layering

CONTRIBUTING.md says a new script operation should touch only the layers it needs, in order:

1. Stable models and protocols: `models.py`, `carla_protocols.py`, `tool_inputs.py`.
2. The adapter: `PythonCarlaAdapter` in `adapter.py`, composed from `adapter_*` mixins, which
   delegate to the `experiment_*`, `traffic_*`, `sensor_*` and `world_timing.py` runtime helpers.
3. The single `CarlaScriptApi` facade (`script_api.py` plus the `script_*_operations.py` mixins).
   `script_operations.py` holds the shared recoverable-error and snapshot policy.
   `api_discovery.py` builds the method catalog that scripts see, so private hooks stay off the
   facade.
4. A small mock-backed facade test, built with `tests/api_helpers.build_api`.
5. User docs, when the public contract changes.

### Ownership and restoration

- Actors created through the curated API are written to an episode-bound journal before setup
  can fail (`ownership*.py`, `actor_creation.py`). On failure, only journaled IDs are destroyed,
  in reverse order. Successful scripts keep their actors.
- World settings and Traffic Manager globals are journaled and restored (`script_settings.py`,
  `traffic_manager_settings.py`). Role or name matches never establish ownership.

### Security boundary

`SECURITY.md` specifies the Landlock paths, TCP ports, rlimits and output limits.
`sandbox_paths.py` is the only filesystem allowlist, and `runtime_ports.py` is the only port
layout. Changes to these, to the script validator, or to the Rust runner need tests showing that
unsafe behavior is still rejected.

Key environment variables:

- `CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR`: writable by the sandbox; holds captures and evidence that
  persist.
- `CARLA_AGENTIC_TOOLKIT_STATE_DIR`: private trusted state, outside the sandbox allowlists.
- `CARLA_AGENTIC_TOOLKIT_SANDBOX`: overrides the runner path. Otherwise the runner is found at
  `sandbox-runner/target/release`, then `target/debug`.
- `CARLA_AGENTIC_TOOLKIT_RECORDER_DIR`: a path on the simulator host, not this host.
- `CARLA_AGENTIC_TOOLKIT_HOST` and `CARLA_AGENTIC_TOOLKIT_PORT`: the default endpoint.

## Repository conventions

- GitFlow: branch from `develop` and open PRs into `develop`, using `feature/*`, `fix/*`,
  `docs/*`, `chore/*` or `WIP/*` branches. `scripts/check_branch_policy.py` enforces PR targets
  in CI. `main` holds released work only.
- Commits use Conventional Commit prefixes (`fix:`, `feat(managed):`, `docs:`, `test:`). Each
  issue's verification is recorded in `docs/issue-resolution-status.md`: the failing test, the
  focused run, the gate counts, and any live CARLA checks still pending.
- Change `uv.lock` or `sandbox-runner/Cargo.lock` only when dependencies change. Raw recordings,
  captures and traces stay local. `docs/evidence/` takes only reviewed evidence with attribution
  and provenance.
