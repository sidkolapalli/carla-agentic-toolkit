"""Live CARLA smoke test for the MCP runtime.

This script requires a running CARLA server on the requested host/port and the
CARLA Python API installed in the current uv environment.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from carla_agentic_toolkit.adapter import PythonCarlaAdapter
from carla_agentic_toolkit.models import TrafficControllerStartRequest, TrafficDensityRequest
from carla_agentic_toolkit.traffic_controller_service import InProcessTrafficControllerService


def main() -> int:
    """Run the live smoke test."""
    args = _parse_args()
    adapter = PythonCarlaAdapter(host=args.host, port=args.port, timeout=args.timeout_seconds)
    health = adapter.health_check()
    controller = InProcessTrafficControllerService()
    status = controller.start(
        TrafficControllerStartRequest(
            density=TrafficDensityRequest(
                vehicle_count=args.vehicle_count,
                traffic_manager_port=args.traffic_manager_port,
                seed=args.seed,
                reset_existing=args.reset_existing,
                global_distance_to_leading_vehicle=args.global_distance_to_leading_vehicle,
                global_percentage_speed_difference=args.global_percentage_speed_difference,
            ),
            host=args.host,
            port=args.port,
            timeout_seconds=args.timeout_seconds,
        )
    )
    deadline = time.monotonic() + args.wait_seconds
    while time.monotonic() < deadline and status.moving_vehicle_count == 0:
        time.sleep(1.0)
        status = controller.get_status()
    sys.stdout.write(
        json.dumps(
            {
                "connected": health.connected,
                "server_version": health.server_version,
                "map": health.current_map,
                "controller": status.to_dict(),
            },
            sort_keys=True,
        )
        + "\n"
    )
    if not args.keep_running:
        controller.stop()
    else:
        _wait_until_interrupted(controller)
    return 0 if status.active and status.vehicle_count > 0 else 1


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Run a live CARLA Agentic Toolkit smoke test.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=2000, type=int)
    parser.add_argument("--timeout-seconds", default=10.0, type=float)
    parser.add_argument("--traffic-manager-port", default=8000, type=int)
    parser.add_argument("--vehicle-count", default=12, type=int)
    parser.add_argument("--seed", default=0, type=int)
    parser.add_argument("--global-distance-to-leading-vehicle", default=4.0, type=float)
    parser.add_argument("--global-percentage-speed-difference", default=0.0, type=float)
    parser.add_argument("--wait-seconds", default=12.0, type=float)
    parser.add_argument("--reset-existing", action="store_true")
    parser.add_argument("--keep-running", action="store_true")
    return parser.parse_args()


def _wait_until_interrupted(controller: InProcessTrafficControllerService) -> None:
    """Keep the live controller process alive until interrupted."""
    try:
        while controller.get_status().active:
            time.sleep(1.0)
    except KeyboardInterrupt:
        controller.stop()


if __name__ == "__main__":
    raise SystemExit(main())
