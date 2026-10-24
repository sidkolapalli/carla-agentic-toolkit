"""Journal every route actor before control; read only owner-frame kinematics."""

from __future__ import annotations

import math
from dataclasses import asdict
from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

from carla_agentic_toolkit.actor_boxes import project_actor_box
from carla_agentic_toolkit.errors import CarlaAdapterError
from carla_agentic_toolkit.managed_names import managed_role
from carla_agentic_toolkit.managed_observations import background_actors
from carla_agentic_toolkit.merge_sensor_evidence import MergeSensor
from carla_agentic_toolkit.route_fixture import VEHICLE_BLUEPRINT, WALKER_BLUEPRINT
from carla_agentic_toolkit.route_models import RouteActor
from carla_agentic_toolkit.sensor_subscription import SensorSubscription

if TYPE_CHECKING:
    from carla_agentic_toolkit.carla_protocols import CarlaSensor
    from carla_agentic_toolkit.merge_models import LocalControl
    from carla_agentic_toolkit.route_geometry import RoutePath, RoutePoint


class RouteActors:
    """Own no clock or world settings; the existing managed session retains those duties."""

    def __init__(self, session: object, path: RoutePath) -> None:
        """Retain native handles until the session's confirmed cleanup."""
        self.session = cast("Any", session)
        self.path = path
        self.handles: dict[str, Any] = {}
        self.sensors: list[MergeSensor] = []

    def spawn(self, role: str, point: RoutePoint, *, walker: bool = False) -> None:
        """Register ownership immediately after spawn, before sensor or actuator calls."""
        blueprint = self._blueprint(role, walker=walker)
        carla = cast("Any", import_module("carla"))
        spawn_clearance_m = 1.2 if walker else 0.5
        transform = carla.Transform(
            carla.Location(x=point.x, y=point.y, z=point.z + spawn_clearance_m),
            carla.Rotation(yaw=point.yaw_degrees),
        )
        handle = self.session.spawn_actor(
            blueprint,
            transform,
            role_name=managed_role(self.session.spec, self.session.run_id, role),
            controller=f"route:{role}",
        )
        self.handles[role] = handle
        if not walker:
            self._sensor(role, handle)

    def _blueprint(self, role: str, *, walker: bool) -> object:
        blueprint = self.session.world.get_blueprint_library().find(
            WALKER_BLUEPRINT if walker else VEHICLE_BLUEPRINT
        )
        blueprint.set_attribute(
            "role_name", managed_role(self.session.spec, self.session.run_id, role)
        )
        if walker and blueprint.has_attribute("is_invincible"):
            blueprint.set_attribute("is_invincible", "false")
        return blueprint

    def _sensor(self, role: str, handle: object) -> None:
        blueprint = self.session.world.get_blueprint_library().find("sensor.other.collision")
        role_name = managed_role(self.session.spec, self.session.run_id, f"{role}-collision")
        blueprint.set_attribute("role_name", role_name)
        sensor = self.session.spawn_actor(
            blueprint,
            import_module("carla").Transform(),
            attach_to=handle,
            role_name=role_name,
            controller="sensor-listener",
        )
        subscription = SensorSubscription(cast("CarlaSensor", sensor), event_sensor=True)
        self.sensors.append(
            MergeSensor(
                int(sensor.id), int(cast("Any", handle).id), role, "collision", subscription
            )
        )

    def observe(self, snapshot: object) -> dict[str, RouteActor]:
        """Use one supplied snapshot for all moving actors; never mix actor getter frames."""
        return {role: self.observe_actor(handle, snapshot) for role, handle in self.handles.items()}

    def observe_actor(self, handle: object, snapshot: object) -> RouteActor:
        """Read an explicit retained handle only from the supplied immutable snapshot."""
        actor = cast("Any", handle)
        frozen = cast("Any", snapshot).find(actor.id)
        if frozen is None:
            message = f"Route actor {actor.id} is absent from the owner snapshot."
            raise CarlaAdapterError(message)
        transform, velocity = frozen.get_transform(), frozen.get_velocity()
        location, extent = transform.location, actor.bounding_box.extent
        projection = self.path.project(float(location.x), float(location.y))
        return RouteActor(
            int(actor.id),
            "walker" if str(actor.type_id).startswith("walker.") else "vehicle",
            float(location.x),
            float(location.y),
            float(transform.rotation.yaw),
            math.hypot(float(velocity.x), float(velocity.y)),
            float(velocity.x),
            float(velocity.y),
            float(extent.x) * 2,
            float(extent.y) * 2,
            projection.progress_m,
            projection.lateral_m,
            self.path.sample(projection.progress_m).lane_id,
            float(location.z),
            project_actor_box(transform, actor.bounding_box),
        )

    def visible_neighbors(
        self, values: dict[str, RouteActor], snapshot: object, *, radius: float
    ) -> tuple[RouteActor, ...]:
        """Use the same snapshot for owned fixture and optional background evidence."""
        policy = values["policy"]
        return tuple(
            actor
            for actor in self._neighbor_values(values, snapshot)
            if math.hypot(actor.x - policy.x, actor.y - policy.y) <= radius
        )

    def _neighbor_values(self, values: dict[str, RouteActor], snapshot: object) -> list[RouteActor]:
        neighbors = [actor for role, actor in values.items() if role != "policy"]
        neighbors.extend(
            self.observe_actor(actor, snapshot) for actor in background_actors(self.session)
        )
        return neighbors

    def vehicle_control(self, role: str, control: LocalControl) -> None:
        """Apply one local numerical actuator command without advancing a frame."""
        self.handles[role].apply_control(import_module("carla").VehicleControl(**asdict(control)))

    def walk(self, *, speed_mps: float) -> None:
        """Cross perpendicular to the initial straight using physical walker control."""
        yaw = math.radians(self.path.points[0].yaw_degrees)
        carla = cast("Any", import_module("carla"))
        self.handles["pedestrian"].apply_control(
            carla.WalkerControl(
                direction=carla.Vector3D(x=math.sin(yaw), y=-math.cos(yaw)),
                speed=speed_mps,
            )
        )

    def traffic_light(self, role: str = "policy") -> str:
        """Read the controlled vehicle's light trigger within the owned synchronous frame."""
        policy = self.handles[role]
        if not policy.is_at_traffic_light():
            return "None"
        return str(policy.get_traffic_light_state()).split(".")[-1]
