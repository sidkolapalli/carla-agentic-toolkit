"""Readiness check for the Linux or WSL2 sandbox runtime."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from pathlib import Path

from carla_agentic_toolkit.sandbox import ScriptOutcome, execute_script

PREFLIGHT_OUTPUT_DIR = ".carla-agentic-toolkit-preflight"
PREFLIGHT_CODE = f'result = api.export_evidence_packet("{PREFLIGHT_OUTPUT_DIR}")'


def run(*, expect_wsl2: bool = False) -> tuple[int, dict[str, object]]:
    """Exercise the real runner and return its machine-readable readiness report."""
    environment = _environment()
    if expect_wsl2 and environment["wsl2"] is not True:
        return 1, {
            "ok": False,
            "environment": environment,
            "sandbox": {},
            "output": {},
            "error": "The Windows launcher requires a WSL2 distribution.",
            "error_type": "wsl2_required",
        }
    output_dir = Path(os.environ["CARLA_AGENTIC_TOOLKIT_OUTPUT_DIR"]).expanduser().resolve()
    preflight_output_dir = output_dir / PREFLIGHT_OUTPUT_DIR
    outcome = execute_script(PREFLIGHT_CODE, timeout_seconds=5)
    try:
        report = _report(outcome, output_dir, environment)
        return (0 if report["ok"] else 1), report
    finally:
        shutil.rmtree(preflight_output_dir, ignore_errors=True)


def _report(
    outcome: ScriptOutcome,
    output_dir: Path,
    environment: dict[str, object],
) -> dict[str, object]:
    """Build the readiness report from observable sandbox output."""
    sandbox = _mapping(outcome.sandbox)
    landlock = _mapping(sandbox.get("landlock"))
    result = _mapping(outcome.result)
    manifest_path = output_dir / str(result.get("manifest_path", ""))
    ruleset_enforced = landlock.get("ruleset_enforced") is True
    evidence_persisted = manifest_path.is_file()
    return {
        "ok": all((outcome.ok, ruleset_enforced, evidence_persisted)),
        "environment": environment,
        "sandbox": {
            "runner": sandbox.get("runner"),
            "ruleset_enforced": ruleset_enforced,
            "landlock": landlock.get("landlock"),
        },
        "output": {
            "directory": str(output_dir),
            "evidence_persisted": evidence_persisted,
        },
        "error": outcome.error,
        "error_type": outcome.error_type,
    }


def _mapping(value: object) -> dict[str, object]:
    """Return a string-keyed mapping or an empty mapping."""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _environment() -> dict[str, object]:
    """Describe the Linux runtime hosting the sandbox."""
    kernel_release = platform.release()
    return {
        "system": platform.system(),
        "kernel_release": kernel_release,
        "wsl2": _is_wsl2(kernel_release),
    }


def _is_wsl2(kernel_release: str) -> bool:
    """Return whether the kernel release identifies WSL2."""
    normalized = kernel_release.casefold()
    return "microsoft" in normalized and "wsl2" in normalized


def main() -> None:
    """Run the preflight console entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-wsl2",
        action="store_true",
        help="fail unless the sandbox is running inside WSL2",
    )
    options = parser.parse_args()
    exit_code, report = run(expect_wsl2=options.expect_wsl2)
    json.dump(report, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
