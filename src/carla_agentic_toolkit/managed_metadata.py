"""Initial trace metadata preserves spec, release, and source-provenance identity."""

from __future__ import annotations

import hashlib
import platform
from pathlib import Path
from typing import TYPE_CHECKING

from carla_agentic_toolkit import __version__
from carla_agentic_toolkit.merge_planner import PLANNER_VERSION, TRACKER_VERSION
from carla_agentic_toolkit.route_geometry import TRACKER_VERSION as ROUTE_TRACKER_VERSION
from carla_agentic_toolkit.route_policy import PLANNER_VERSION as ROUTE_PLANNER_VERSION

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_versions import VersionInfo
    from carla_agentic_toolkit.managed_spec import ExperimentSpec


def run_metadata(spec: ExperimentSpec, versions: VersionInfo) -> dict[str, object]:
    """Record full available native versions before compatibility or world startup."""
    return {
        "spec": spec.model_dump(),
        "fixture_version": spec.fixture,
        "policy_version": spec.policy,
        **_implementation_versions(spec.fixture),
        "package_version": __version__,
        "code_sha256": _source_digest(),
        "replicate_index": spec.replicate_index,
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "carla_client": versions.client_version,
            "carla_server": versions.server_version,
        },
    }


def _source_digest() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _implementation_versions(fixture: str) -> dict[str, str]:
    if fixture == "town10-route-ue5-v1":
        return {
            "planner_version": ROUTE_PLANNER_VERSION,
            "controller_version": ROUTE_TRACKER_VERSION,
        }
    return {"planner_version": PLANNER_VERSION, "controller_version": TRACKER_VERSION}
