"""Native adapter queries for read-only perception ground truth."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit import experiment_environment, experiment_ground_truth

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaWorld
    from carla_agentic_toolkit.models import Location


class PythonCarlaGroundTruthMixin:
    """Keep spatial queries separate from creation and sensor lifetime operations."""

    def _client(self) -> CarlaClient:
        """Return the concrete adapter's budget-configured native client."""
        raise NotImplementedError

    def _world(self, client: CarlaClient) -> CarlaWorld:
        """Return the current world without ticking."""
        raise NotImplementedError

    def get_level_bounding_boxes(
        self,
        *,
        label: str,
        max_count: int,
        origin: Location | None = None,
        max_distance: float | None = None,
    ) -> dict[str, object]:
        """Return semantic level bounds, optionally sorted and filtered around an origin."""
        return experiment_environment.get_level_bounding_boxes(
            self._world(self._client()),
            label=label,
            max_count=max_count,
            origin=origin,
            max_distance=max_distance,
        )

    def get_actor_bounding_boxes(self, actor_ids: tuple[int, ...]) -> dict[str, object]:
        """Return native world vertices bound to one physics frame."""
        return experiment_ground_truth.actor_bounding_boxes(self._world(self._client()), actor_ids)

    def get_camera_intrinsics(self, sensor_id: int) -> dict[str, object]:
        """Read actual camera attributes and return the tutorial intrinsic matrix."""
        return experiment_ground_truth.camera_intrinsics(self._world(self._client()), sensor_id)
