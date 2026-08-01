# Contributing

Thank you for helping improve CARLA MCP.

## Development Setup

The local gate requires Linux, Python 3.12+, uv, and Rust. CARLA itself is only
required for manual live testing.

```bash
git clone https://github.com/sidkolapalli/carla-mcp.git
cd carla-mcp
uv sync --locked
cargo build --locked --manifest-path sandbox-runner/Cargo.toml --release
make check
```

Use `make format` before the final check when formatting changes are needed.

## Quality Bar

`make check` must pass before a pull request is ready. It runs:

- Ruff with all rules enabled except the documented project exceptions
- Ty type checking
- Radon A-grade complexity and maintainability checks
- Rustfmt and Clippy with warnings denied
- a debug Rust runner build and Rust tests
- pytest, including the real Landlock preflight

Tests use mock CARLA adapters and must not require a running simulator. If a
change affects real CARLA behavior, also run the manual smoke test and report the
CARLA version and result in the pull request:

```bash
uv run python scripts/live_smoke.py --reset-existing --vehicle-count 12
```

Do not run destructive live tests against a shared simulation.

## Making Changes

Keep pull requests focused. For a new script API operation, update only the
layers it actually needs:

1. Stable models or protocol boundaries
2. The CARLA adapter/runtime implementation
3. The tool implementation and `CarlaScriptApi` wrapper
4. A small mock-backed behavior test
5. User-facing documentation when the public contract changes

Security boundaries require tests. In particular, changes to script validation,
filesystem access, network access, process limits, or the Rust runner must show
that unsafe behavior remains rejected.

Update `uv.lock` or `sandbox-runner/Cargo.lock` only when dependencies change.
Never commit CARLA assets, recordings, generated captures, credentials, or local
configuration.

## Pull Requests

Explain the problem and why the chosen change is the smallest correct fix. Link
related issues, include the commands you ran, and call out whether live CARLA was
tested. Maintainers may ask to split unrelated changes.

By contributing, you agree that your contribution is licensed under the MIT
License and to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Security Reports

Do not open a public issue for a suspected vulnerability. Follow
[SECURITY.md](SECURITY.md).

## Releases

The v0.1 distribution is a source checkout because the Python wheel does not yet
bundle the Linux Rust sandbox runner. Do not publish the wheel to PyPI until the
runner packaging and platform support are solved and tested.
