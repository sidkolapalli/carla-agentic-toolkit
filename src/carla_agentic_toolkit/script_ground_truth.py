"""Curated, recoverable perception queries with run-local result snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit.experiment_ground_truth import query_origin
from carla_agentic_toolkit.script_operations import ScriptOperations, recover

if TYPE_CHECKING:
    from carla_agentic_toolkit.models import JsonObject


class ScriptGroundTruthOperations(ScriptOperations):
    """Expose read-only spatial selection and measured camera calibration."""

    @recover("get_level_bounding_boxes_failed")
    def get_level_bounding_boxes(
        self,
        label: str = "Any",
        max_count: int = 200,
        *,
        origin: dict[str, object] | None = None,
        max_distance: float | None = None,
    ) -> JsonObject:
        """Order by distance only with explicit origin; otherwise retain native order."""
        return self._snapshot(
            f"carla-snapshot://environment/{label}/bounding-boxes",
            self._adapter.get_level_bounding_boxes(
                label=label,
                max_count=max_count,
                origin=query_origin(origin),
                max_distance=max_distance,
            ),
        )

    @recover("get_actor_bounding_boxes_failed")
    def get_actor_bounding_boxes(self, actor_ids: list[int]) -> JsonObject:
        """Return world vertices for requested actors from one native snapshot frame."""
        return self._snapshot(
            "carla-snapshot://actors/bounding-boxes",
            self._adapter.get_actor_bounding_boxes(tuple(actor_ids)),
        )

    @recover("get_camera_intrinsics_failed")
    def get_camera_intrinsics(self, sensor_id: int) -> JsonObject:
        """Return actual camera dimensions, horizontal FOV and intrinsic matrix."""
        return self._snapshot(
            f"carla-snapshot://sensors/{sensor_id}/intrinsics",
            self._adapter.get_camera_intrinsics(sensor_id),
        )
