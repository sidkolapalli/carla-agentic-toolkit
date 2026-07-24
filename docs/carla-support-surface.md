# CARLA Support Surface for MCP

> **Status:** Capability research, not the current API contract. See the
> [README](../README.md) for the implemented one-tool script API.

This note summarizes what an MCP server can reasonably support through the
official CARLA Python API and official CARLA source tree. It is intentionally
scoped to operations that can be implemented without forking CARLA.

## Evidence Sources

- CARLA UE5 documentation: https://carla-ue5.readthedocs.io/en/latest/
- CARLA UE5 quick start: https://carla-ue5.readthedocs.io/en/latest/start_quickstart/
- CARLA UE5 foundations: https://carla-ue5.readthedocs.io/en/latest/foundations/
- CARLA UE5 first steps: https://carla-ue5.readthedocs.io/en/latest/tuto_first_steps/
- CARLA UE5 actors: https://carla-ue5.readthedocs.io/en/latest/core_actors/
- CARLA UE5 sensors: https://carla-ue5.readthedocs.io/en/latest/core_sensors/
- CARLA UE5 Linux build docs: https://carla-ue5.readthedocs.io/en/latest/build_linux_ue5/
- CARLA UE5 OSM tutorial: https://carla-ue5.readthedocs.io/en/latest/tuto_G_openstreetmap/
- CARLA synchrony and timestep docs: https://carla.readthedocs.io/en/latest/adv_synchrony_timestep/
- CARLA recorder docs: https://carla.readthedocs.io/en/latest/adv_recorder/
- CARLA 0.10.0 release notes: https://carla.org/2024/12/19/release-0.10.0/
- Official source, Python client binding:
  https://github.com/carla-simulator/carla/blob/ue5-dev/PythonAPI/carla/src/Client.cpp
- Official source, world client implementation:
  https://github.com/carla-simulator/carla/blob/ue5-dev/LibCarla/source/carla/client/World.cpp
- Official source, configuration utility:
  https://github.com/carla-simulator/carla/blob/ue5-dev/PythonAPI/util/config.py
- Official source, traffic example:
  https://github.com/carla-simulator/carla/blob/ue5-dev/PythonAPI/examples/generate_traffic.py
- Official source, manual control example:
  https://github.com/carla-simulator/carla/blob/ue5-dev/PythonAPI/examples/manual_control.py

## Current CARLA Shape

CARLA exposes a client-server architecture. The simulator runs as the server;
Python code connects as a client, normally to `localhost:2000`, and sends
requests through the Python API. Official docs show the client retrieving a
`world`, loading maps, getting weather, reading the blueprint library, spawning
actors, attaching sensors, recording episodes, and ticking the simulation.

This is a good fit for MCP because an MCP server can be a Python process that
wraps the CARLA client API and exposes safe, typed tools to AI agents.

## Supported Through Public Python API

| Area | Official CARLA support | MCP implication |
| --- | --- | --- |
| Connect to server | `carla.Client(host, port)`, `set_timeout`, version reads | Support `health_check` and version diagnostics. |
| Map/world state | `get_world`, `get_available_maps`, `load_world`, `reload_world` | Support map listing, map loading, and current-world inspection. |
| World settings | `world.get_settings`, `world.apply_settings` | Support synchronous mode, fixed timestep, and rendering flags. |
| Deterministic stepping | `world.tick`, `world.wait_for_tick`, fixed delta seconds | Support controlled stepping when the MCP server owns the tick. |
| Traffic manager | `client.get_trafficmanager`, sync mode, seeds, autopilot registration | Support traffic-manager setup for deterministic traffic runs. |
| Blueprint discovery | `world.get_blueprint_library`, `filter`, `find`, attributes | Support listing vehicles, walkers, sensors, and modifiable attributes. |
| Actor spawning | `world.spawn_actor`, `world.try_spawn_actor`, batch commands | Support single and batch actor spawning with structured failure reports. |
| Actor control/state | actor transforms, velocity, acceleration, location, physics toggles | Support actor state reads and limited control commands. |
| Walkers | walker blueprints, navigation locations, walker controllers | Support pedestrian spawning as a first-class feature. |
| Sensors | sensor blueprints, `listen`, camera attachment, image saving | Support camera/sensor attachment and data capture. |
| Spectator | `world.get_spectator`, spectator transform | Support camera viewpoint setup for visual capture. |
| Recorder | `start_recorder`, `stop_recorder`, `replay_file`, recorder queries | Support episode recording, replay, collision queries, blocked-actor queries. |
| Batch commands | `client.apply_batch`, `client.apply_batch_sync` | Prefer batch operations for reliable scenario setup. |
| Environment inspection | map, weather, actors, traffic lights/signs, level bounding boxes | Support diagnostic snapshots and evidence packets. |
| OpenDRIVE generation | `client.generate_opendrive_world` appears in Python bindings and utility script | Treat as advanced and version-sensitive; validate against the connected CARLA build. |
| OSM conversion | CARLA docs describe OSM to OpenDRIVE conversion and `carla.Osm2Odr.convert` appears in the utility script | Treat as advanced and version-sensitive; distinguish docs for latest/dev builds from 0.10.0 release limitations. |

## High-Value MCP Tools

The first public release should bias toward diagnostics, repeatability, and
small composable controls.

### Diagnostics

- `health_check`
  - Connect to `host:port`.
  - Report client version, server version, timeout behavior, current map, actor
    counts, synchronous mode, fixed delta seconds, rendering mode, and available
    maps.
- `diagnose_environment`
  - Check documented runtime assumptions: OS family, GPU visibility when
    available, free disk space, Python importability, package version, and ports
    `2000`/`2001`.
- `list_capabilities`
  - Report which tool groups are usable against the connected CARLA version.

### World And Timing

- `list_worlds`
- `load_world`
- `reload_world`
- `get_world_state`
- `set_sync_mode`
  - Enable synchronous mode with fixed delta seconds.
  - Optionally configure Traffic Manager sync mode in the same operation.
- `tick`
- `run_for_frames`

### Actors And Traffic

- `list_blueprints`
- `describe_blueprint`
- `spawn_actor`
- `spawn_actor_batch`
- `destroy_actor`
- `destroy_actors`
- `get_actor_state`
- `list_actors`
- `set_actor_transform`
- `set_vehicle_autopilot`
- `spawn_traffic`
  - Build from the official `generate_traffic.py` pattern.
- `start_traffic_controller`
- `stop_traffic_controller`
- `traffic_controller_status`
- `set_traffic_density`
- `set_vehicle_behavior`
  - Keep Traffic Manager alive for moving traffic.
  - Apply behavior profiles through official Traffic Manager vehicle knobs.

### Sensors And Capture

- `attach_camera`
- `attach_sensor`
- `capture_sensor_frame`
- `set_spectator`
- `save_screenshot`
- `record_episode`
- `stop_recording`
- `replay_recording`

### Evidence

- `export_evidence_packet`
  - Include versions, map, world settings, actor inventory, blueprint filters,
    recorder file paths, selected frames, command responses, warnings, and
    failure causes.
- `query_recording_collisions`
- `query_recording_blocked_actors`

## Design Constraints From CARLA Docs

### One Ticker In Synchronous Mode

CARLA warns that only one client should tick in a multiclient setup. The MCP
server should either own the tick or refuse tick operations when another driver
is active. This should be visible in tool descriptions.

### Fixed Timestep For Reliable Runs

The official synchrony docs state that synchronous mode plus fixed delta seconds
is the best mode for precision, sensor alignment, and determinism. The server
should make fixed timestep the default for reproducible scenario runs.

### Traffic Manager Must Match Sync Mode

When synchronous mode is enabled and Traffic Manager is running, Traffic Manager
must also be put in synchronous mode. `set_sync_mode` should configure both when
requested, and diagnostics should warn when they disagree.

Traffic Manager movement also depends on a live client/controller. One-shot
scripts can spawn vehicles and register autopilot, but vehicles may stop when the
owning Traffic Manager client exits. Keep process-local traffic control in one
long-running script or use `scripts/live_smoke.py --keep-running` as a sidecar.

### Prefer Batch Commands For Setup

The official Python client bindings expose `apply_batch` and `apply_batch_sync`.
The synchrony docs note that batched commands are more reliable for deterministic
repetitions. Scenario setup tools should use batches for actor spawn/destroy
where possible.

### Sensors Need Explicit Queuing

Sensor data arrives through `listen` callbacks, and camera data can be delayed by
a few frames. Capture tools should use queues and frame IDs rather than assuming
that a sensor callback corresponds to the same client-side loop iteration.

### Actors Need Explicit Cleanup

CARLA actors are not automatically destroyed when a Python script exits. MCP
tools that spawn actors should track created actor IDs and expose cleanup tools.

## Version And Runtime Caveats

The CARLA UE5 quick start documents a heavy runtime profile:

- Ubuntu 22.04 or Windows 11 minimum for UE5 builds.
- NVIDIA RTX-class GPU recommended, with 16 GB or more VRAM recommended.
- NVIDIA driver requirements documented by platform.
- Around 130 GB disk space for packaged CARLA.
- Ports `2000` and `2001` are used by default.

Source builds are heavier. The UE5 build docs say the source workflow uses the
CARLA fork of Unreal Engine 5.5 and may download/build a very large engine tree.

## Known 0.10.0 Limitations To Surface Honestly

CARLA latest documentation can describe workflows that are only available in
newer, nightly, source-built, or otherwise version-specific configurations.
CARLA 0.10.0 release notes describe important UE5 gaps. The MCP server should
surface these in diagnostics and docs instead of hiding them:

- Performance was reported around 24/25 FPS in internal tests.
- GPUs below 12 GB VRAM may not load the default Town 10 map; 16 GB VRAM is
  recommended.
- Weather is fixed to daylight; clouds, rain, fog, and sun position cannot be
  modified in that release.
- Only Town 10 was upgraded among the listed towns; many older towns were not
  included in the UE5 package.
- Map layers API is not supported in that release.
- Large maps are not supported in that release.
- OpenDRIVE import is not supported in that release.
- OpenStreetMap import is not supported in that release.
- SUMO, Chrono, and PTV Vissim co-simulations are disabled or untested in that
  release.
- Vehicle blueprint coverage is smaller than older CARLA releases.
- V2X and G-buffers are not supported in that release.

These limitations do not prevent a useful MCP server. They mean tools must be
version-aware and must report unavailable features clearly.

## Recommended First Milestone

Build a minimal Python MCP server around the official CARLA Python client:

1. `health_check`
2. `list_worlds`
3. `load_world`
4. `get_world_state`
5. `set_sync_mode`
6. `list_blueprints`
7. `spawn_actor_batch`
8. `tick`
9. `attach_camera`
10. `capture_sensor_frame`
11. `record_episode`
12. `stop_recording`
13. `export_evidence_packet`

This milestone proves the most valuable surface: a connected AI agent can verify
CARLA, configure a reproducible world, create actors, step the simulation, attach
a camera, capture or record evidence, and export a useful evidence packet.

## Out Of Scope For The First Release

- Forking CARLA.
- Unreal Editor automation.
- Full scenario-language authoring.
- Full OpenSCENARIO support.
- Full OpenDRIVE or OSM import promises for UE5 0.10.0.
- Remote unauthenticated control over a public network.
- Replacing ScenarioRunner, Scenic, ROS2, or Traffic Manager.

## Implementation Notes

- Use Python first. It matches the official tutorial path and keeps setup simple.
- Keep tools typed and narrow. Avoid exposing arbitrary Python execution.
- Treat file writes as explicit outputs under a configured workspace directory.
- Add a session object that tracks spawned actors, sensors, recorder files, and
  whether this server owns synchronous ticking.
- Add a compatibility layer keyed by CARLA client/server version.
- Return structured errors with CARLA exception messages, command response
  errors, and suggested next checks.
- Use CI tests with mocked CARLA objects for tool contracts, plus optional live
  integration tests gated by `CARLA_HOST` and `CARLA_PORT`.
