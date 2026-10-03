"""Optional small MCP lifecycle surface backed by the local experiment supervisor."""

from typing import Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from carla_agentic_toolkit.managed_control import ManagedController
from carla_agentic_toolkit.managed_spec import ExperimentSpec


def register_managed_tool(mcp: MCPServer) -> None:
    """Register only when the trusted server administrator explicitly opts in."""

    @mcp.tool(
        annotations=ToolAnnotations(
            title="Managed CARLA Experiment",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=False,
            open_world_hint=True,
        ),
        structured_output=True,
    )
    def managed_experiment(
        action: Literal["start", "status", "stop", "result", "recover"],
        run_id: str | None = None,
        spec: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Start a reviewed numerical experiment, or promptly inspect/request its stop.

        Requires a dedicated CARLA instance. Stop requests cancellation; termination
        and cleanup are separate status fields. Credentials remain trusted server config.
        """
        controller = ManagedController()
        if action == "start":
            return controller.start(ExperimentSpec.model_validate(spec or {}))
        if run_id is None:
            message = "A local run ID is required for status, stop, result, and recover."
            raise ValueError(message)
        handlers = {
            "status": controller.status,
            "stop": controller.stop,
            "result": controller.result,
            "recover": controller.recover,
        }
        return handlers[action](run_id)
