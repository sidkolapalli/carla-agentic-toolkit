"""Evidence packet tool implementations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from carla_mcp.models import ToolResult

if TYPE_CHECKING:
    from pathlib import Path

    from carla_mcp.session import CarlaSession


def export_evidence_packet(session: CarlaSession, output_dir: Path) -> ToolResult:
    """Write a compact evidence manifest and publish it as a resource."""
    output_dir.mkdir(parents=True, exist_ok=True)
    resource_uris = session.resource_uris()
    packet_id = "evidence-001"
    manifest_path = output_dir / f"{packet_id}.json"
    manifest = {"packet_id": packet_id, "resources": list(resource_uris)}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    payload = {
        "packet_id": packet_id,
        "manifest_path": str(manifest_path),
        "resource_uri": f"carla://evidence/{packet_id}",
        "resources": list(resource_uris),
    }
    session.register_resource(str(payload["resource_uri"]), payload)
    return ToolResult.ok(payload)
