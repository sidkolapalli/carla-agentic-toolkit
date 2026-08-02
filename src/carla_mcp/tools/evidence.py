"""Evidence packet tool implementations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from carla_mcp.models import ToolResult

if TYPE_CHECKING:
    from pathlib import Path

    from carla_mcp.snapshots import RunSnapshots


def export_evidence_packet(snapshots: RunSnapshots, output_dir: Path) -> ToolResult:
    """Write a compact evidence manifest and publish it as a snapshot."""
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot_uris = snapshots.snapshot_uris()
    packet_id = "evidence-001"
    manifest_path = output_dir / f"{packet_id}.json"
    manifest = {"packet_id": packet_id, "snapshots": list(snapshot_uris)}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    payload = {
        "packet_id": packet_id,
        "manifest_path": str(manifest_path),
        "snapshot_uri": f"carla-snapshot://evidence/{packet_id}",
        "snapshots": list(snapshot_uris),
    }
    snapshots.register_snapshot(str(payload["snapshot_uri"]), payload)
    return ToolResult.ok(payload)
