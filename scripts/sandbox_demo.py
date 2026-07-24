"""Demonstrate sandbox enforcement without a running CARLA server."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast

from carla_mcp.sandbox import execute_script


def main() -> int:
    """Run three real sandbox checks and print a compact walkthrough."""
    _write("CARLA MCP — sandbox walkthrough", "")

    basic = execute_script("result = {'message': 'hello from the sandbox'}", timeout_seconds=2)
    if not basic.ok:
        return _failure("Sandbox execution failed", basic.to_dict())
    sandbox = cast("dict[str, object]", basic.sandbox)
    landlock = cast("dict[str, object]", sandbox["landlock"])
    _write(
        "✓ Script executed inside Linux Landlock",
        f"  ruleset_enforced: {landlock['ruleset_enforced']}",
        f"  result: {basic.result}",
        "",
    )

    rejected = execute_script("result = api._adapter.host", timeout_seconds=2)
    if rejected.error_type != "script_rejected":
        return _failure("Private API access was not rejected", rejected.to_dict())
    _write(
        "✓ Private API traversal rejected",
        f"  error_type: {rejected.error_type}",
        f"  reason: {rejected.error}",
        "",
    )

    evidence = execute_script(
        'result = api.export_evidence_packet("readme-demo")',
        timeout_seconds=2,
    )
    if not evidence.ok:
        return _failure("Evidence export failed", evidence.to_dict())
    result = cast("dict[str, object]", evidence.result)
    output_dir = Path(
        os.environ.get("CARLA_MCP_OUTPUT_DIR", "carla-mcp-output")
    ).expanduser().resolve()
    manifest = output_dir / str(result["manifest_path"])
    if not manifest.is_file():
        return _failure("Evidence manifest did not persist", {"path": str(manifest)})
    _write(
        "✓ Evidence persisted outside per-run scratch space",
        f"  manifest: {manifest}",
    )
    return 0


def _failure(message: str, details: object) -> int:
    """Write one failed demo check."""
    _write(f"✗ {message}", f"  details: {details}")
    return 1


def _write(*lines: str) -> None:
    """Write demo lines to standard output."""
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
