"""Adapter listener setup and frame delivery without implicit world advancement."""

from __future__ import annotations

from typing import TYPE_CHECKING

from carla_agentic_toolkit import experiment_perception
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.sensor_rendering import require_sensor_rendering
from carla_agentic_toolkit.sensor_schedule import observed_fixed_sensor_step
from carla_agentic_toolkit.sensor_subscription import (
    DEFAULT_SENSOR_DRAIN_SECONDS,
    EVENT_SENSOR_TYPES,
    SensorSubscription,
    validate_capacity,
)

if TYPE_CHECKING:
    from pathlib import Path

    from carla_agentic_toolkit.carla_protocols import CarlaClient, CarlaSensor, CarlaWorld
    from carla_agentic_toolkit.sensor_memory import SensorQueueBudget


class PythonCarlaSensorListeningMixin:
    """Bound sensor listeners and delivery evidence for the concrete adapter."""

    _sensor_subscriptions: dict[int, SensorSubscription]
    _subscribed_sensor_handles: dict[int, CarlaSensor]
    _subscription_world_ids: dict[int, int]
    _sensor_queue_budget: SensorQueueBudget

    def _client(self) -> CarlaClient:
        """Return the retained native client."""
        raise NotImplementedError

    def _world(self, client: CarlaClient) -> CarlaWorld:
        """Return the native client's current world."""
        raise NotImplementedError

    def _sensor_cleanup_identity(self, sensor_id: int, world: CarlaWorld) -> int:
        """Resolve the originating episode for retained native handles."""
        raise NotImplementedError

    def _require_cleanup_episode(self, identity: int) -> None:
        """Refuse native access after an originating episode changes."""
        raise NotImplementedError

    def _sensor_actor(self, sensor_id: int) -> CarlaSensor:
        """Resolve the original native sensor handle."""
        raise NotImplementedError

    def subscribe_sensor(
        self, sensor_id: int, *, event_sensor: bool | None = None, capacity: int = 32
    ) -> dict[str, object]:
        """Install a bounded listener before the owner advances the world."""
        validate_capacity(capacity)
        if sensor_id in self._sensor_subscriptions:
            message = f"Sensor {sensor_id} already has an active subscription."
            raise CarlaAdapterError(message)
        world = self._world(self._client())
        identity = self._sensor_cleanup_identity(sensor_id, world)
        self._require_cleanup_episode(identity)
        sensor = self._sensor_actor(sensor_id)
        self._require_cleanup_episode(identity)
        require_sensor_rendering(world, sensor.type_id)
        if sensor.type_id.startswith("sensor.camera."):
            self._require_cleanup_episode(identity)
        is_event = sensor.type_id in EVENT_SENSOR_TYPES if event_sensor is None else event_sensor
        self._sensor_subscriptions[sensor_id] = SensorSubscription(
            sensor,
            event_sensor=is_event,
            capacity=capacity,
            byte_budget=self._sensor_queue_budget,
            fixed_delta_seconds=observed_fixed_sensor_step(
                world,
                sensor,
                event_sensor=is_event,
                after_settings_read=lambda: self._require_cleanup_episode(identity),
            ),
        )
        self._subscribed_sensor_handles[sensor_id] = sensor
        self._subscription_world_ids[sensor_id] = identity
        return {"sensor_id": sensor_id, "event_sensor": is_event, "capacity": capacity}

    def drain_sensor(
        self,
        sensor_id: int,
        frame: int,
        *,
        timeout_seconds: float = DEFAULT_SENSOR_DRAIN_SECONDS,
        output_dir: Path | None = None,
        save_frames: bool = False,
    ) -> dict[str, object]:
        """Read available samples for an owner frame without issuing simulator ticks."""
        experiment_perception.validate_save_frames(save_frames=save_frames)
        subscription = self._subscription(sensor_id)
        batch = subscription.drain(frame, timeout_seconds=timeout_seconds)
        frames = list(batch.frames)
        paths = experiment_perception.save_drained_frames(
            frames,
            sensor_id,
            output_dir,
            sensor_type=self._subscribed_sensor_handles[sensor_id].type_id,
            save_frames=save_frames,
        )
        return {
            "sensor_id": sensor_id,
            **batch.to_dict(),
            "frames": experiment_perception.sensor_frame_digests(
                frames, self._subscribed_sensor_handles[sensor_id]
            ),
            "paths": [str(path) for path in paths],
        }

    def _subscription(self, sensor_id: int) -> SensorSubscription:
        """Require a listener owned by this adapter execution."""
        try:
            return self._sensor_subscriptions[sensor_id]
        except KeyError as exc:
            message = f"Sensor {sensor_id} has no subscription; call subscribe_sensor first."
            raise CarlaAdapterError(message) from exc
