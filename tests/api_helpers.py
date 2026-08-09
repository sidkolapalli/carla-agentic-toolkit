"""Small test constructor for the surviving script facade boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from carla_agentic_toolkit.script_api import CarlaScriptApi

if TYPE_CHECKING:
    from carla_agentic_toolkit.adapter import PythonCarlaAdapter
    from carla_agentic_toolkit.snapshots import RunSnapshots
    from carla_agentic_toolkit.traffic_controller_service import InProcessTrafficControllerService


def build_api(
    adapter: object,
    snapshots: RunSnapshots,
    controller: object | None = None,
) -> CarlaScriptApi:
    """Build the facade from a partial adapter and optional controller fake."""
    api = CarlaScriptApi(cast("PythonCarlaAdapter", adapter), snapshots)
    if controller is not None:
        api._traffic_controller = cast("InProcessTrafficControllerService", controller)  # noqa: SLF001
    return api
