"""Durable evidence manifest export for one script execution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from carla_mcp.models import JsonObject
    from carla_mcp.snapshots import RunSnapshots


def export_evidence_packet(snapshots: RunSnapshots, output_dir: str) -> JsonObject:
    """Write and register a compact manifest of script-created snapshots."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    snapshot_uris = snapshots.snapshot_uris()
    packet_id = "evidence-001"
    manifest_path = directory / f"{packet_id}.json"
    manifest = {"packet_id": packet_id, "snapshots": list(snapshot_uris)}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    payload: JsonObject = {
        "packet_id": packet_id,
        "manifest_path": str(manifest_path),
        "snapshot_uri": f"carla-snapshot://evidence/{packet_id}",
        "snapshots": list(snapshot_uris),
    }
    snapshots.register_snapshot(str(payload["snapshot_uri"]), payload)
    return payload
