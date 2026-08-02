# MCP Correlation Design

> **Status:** Historical design research, not current API documentation. The
> implemented release supersedes the separate-tool proposals below with the
> sandboxed `execute_carla_script` tool described in the [README](../README.md).
> Live simulator state remains inline as run-local `snapshots`; only explicitly
> published durable PNG/JPEG captures use native MCP image content and the
> bounded `carla-output://capture/{token}` Resource template.

This note maps the official Model Context Protocol shape to a CARLA MCP server.
It explains what the server should expose as MCP Tools, Resources, and Prompts,
and how those primitives correspond to the CARLA Python API support surface.

## Evidence Sources

- MCP architecture overview: https://modelcontextprotocol.io/docs/learn/architecture
- MCP latest specification: https://modelcontextprotocol.io/specification/2025-11-25
- MCP lifecycle: https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle
- MCP transports: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- MCP server feature overview: https://modelcontextprotocol.io/specification/2025-11-25/server
- MCP tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- MCP resources: https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- MCP prompts: https://modelcontextprotocol.io/specification/2025-11-25/server/prompts
- MCP authorization: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- CARLA support surface: ./carla-support-surface.md

## MCP Interpretation

Official MCP documentation describes three server-side primitives:

- Tools: model-controlled functions that perform actions or retrieve information.
- Resources: application-controlled context identified by URIs.
- Prompts: user-controlled templates or workflows that users explicitly invoke.

For CARLA MCP, this implies:

- Side-effecting CARLA operations are Tools.
- Current simulator state, inventories, recordings, captured frames, and evidence
  files are Resources.
- Common workflows such as "diagnose my CARLA server" or "record a minimal
  traffic scene" are Prompts.

## Server Identity And Capabilities

Initial server identity:

```json
{
  "name": "carla-mcp",
  "title": "CARLA MCP",
  "version": "0.1.0",
  "description": "MCP server for controlling, diagnosing, and recording CARLA simulations through the CARLA Python API."
}
```

Initial capabilities:

```json
{
  "tools": {
    "listChanged": true
  },
  "resources": {
    "listChanged": true,
    "subscribe": false
  },
  "prompts": {
    "listChanged": false
  },
  "logging": {}
}
```

Rationale:

- `tools.listChanged` is useful because available tools may depend on connected
  CARLA version, server reachability, and configured mode.
- `resources.listChanged` is useful because captures, recordings, and evidence
  packets appear during a session.
- `resources.subscribe` can be deferred. A first release can keep resources
  request/response only.
- `prompts.listChanged` can be false because prompts are static in the first
  release.

## Transport Shape

### First Release: stdio

Use stdio first. Official MCP docs describe stdio as the client launching the
server as a subprocess and exchanging newline-delimited JSON-RPC messages over
standard input/output.

Why stdio first:

- It is the simplest local developer workflow.
- It avoids designing HTTP auth before the tool surface is proven.
- CARLA itself already listens on local TCP ports, so keeping MCP local reduces
  accidental remote control risk.
- It fits clients that launch local MCP tools directly.

Important stdio rule:

- The server must write only valid MCP messages to stdout.
- Logs must go to stderr or MCP logging, not stdout.

### Later Release: Streamable HTTP

Add Streamable HTTP after the stdio server is stable. Official MCP docs describe
Streamable HTTP as an independent server process with a single MCP endpoint that
supports HTTP POST and GET, optionally using Server-Sent Events.

HTTP mode should be local-only by default:

```text
127.0.0.1:8765/mcp
```

Remote binding should require explicit configuration and authorization.

## Correlation Matrix

| CARLA capability | MCP primitive | Proposed names |
| --- | --- | --- |
| Connect/version/status | Tool + Resource | `health_check`, `carla://session/status` |
| Available maps | Tool + Resource | `list_worlds`, `carla://worlds` |
| Load/reload map | Tool | `load_world`, `reload_world` |
| Current world settings | Tool + Resource | `get_world_state`, `carla://world/current` |
| Synchronous mode/fixed timestep | Tool | `set_sync_mode` |
| Tick simulation | Tool | `tick`, `run_for_frames` |
| Blueprint library | Tool + Resource | `list_blueprints`, `describe_blueprint`, `carla://blueprints/{filter}` |
| Actor inventory | Tool + Resource | `get_actor_state`, `carla://actors`, `carla://actors/{id}` |
| Spawn/destroy actors | Tool | `spawn_actor`, `spawn_actor_batch`, `destroy_actor`, `destroy_actors` |
| Traffic Manager | Tool + Resource | `configure_traffic_manager`, `carla://traffic-manager/status` |
| Sensor attachment/capture | Tool + Resource | `attach_sensor`, `capture_sensor_frame`, `carla://captures/{id}` |
| Spectator camera | Tool | `set_spectator` |
| Recorder | Tool + Resource | `record_episode`, `stop_recording`, `replay_recording`, `carla://recordings/{id}` |
| Recorder queries | Tool | `query_recording_collisions`, `query_recording_blocked_actors` |
| Evidence export | Tool + Resource | `export_evidence_packet`, `carla://evidence/{id}` |
| Common workflows | Prompt | `diagnose_carla`, `record_minimal_scene`, `replay_recording` |

## Tool Catalog

Tool names should follow MCP guidance: ASCII letters, digits, underscores,
hyphens, or dots; no spaces; unique within the server.

### Diagnostic Tools

#### `health_check`

Purpose: connect to CARLA and report whether the Python API and simulator are
usable.

Input schema:

```json
{
  "type": "object",
  "properties": {
    "host": { "type": "string", "default": "127.0.0.1" },
    "port": { "type": "integer", "default": 2000 },
    "timeout_seconds": { "type": "number", "default": 10.0 }
  },
  "additionalProperties": false
}
```

Structured output:

```json
{
  "connected": true,
  "client_version": "0.10.0",
  "server_version": "0.10.0",
  "current_map": "Town10HD_Opt",
  "sync_mode": false,
  "fixed_delta_seconds": null,
  "actor_counts": {
    "vehicles": 0,
    "walkers": 0,
    "sensors": 0,
    "traffic": 0
  },
  "warnings": []
}
```

#### `diagnose_environment`

Purpose: check documented runtime assumptions without changing the simulator.

Checks:

- Python can import `carla`.
- CARLA client and server versions are visible when a server is running.
- Ports `2000` and `2001` are reachable or explain why they are not.
- Configured output directory is writable.
- GPU/driver information is reported when available.
- Disk space warning threshold is checked for CARLA packages and captures.

### World And Timing Tools

#### `list_worlds`

Purpose: call CARLA map discovery and return available map names.

#### `load_world`

Purpose: load a named map.

Required confirmation: yes, because loading a world destroys and replaces the
current CARLA world.

#### `reload_world`

Purpose: reload the current map.

Required confirmation: yes, because actors and runtime state can be reset.

#### `get_world_state`

Purpose: return current map, weather, settings, actor counts, sync mode, fixed
timestep, rendering mode, and Traffic Manager sync status when available.

#### `set_sync_mode`

Purpose: configure reproducible stepping.

Input fields:

- `enabled`
- `fixed_delta_seconds`
- `traffic_manager_port`
- `sync_traffic_manager`
- `seed`

Safety:

- Warn if the server cannot prove it owns the tick.
- If Traffic Manager is active and `sync_traffic_manager` is false, return a
  warning because CARLA docs require matching sync mode.

#### `tick`

Purpose: advance one synchronous frame.

Precondition:

- World synchronous mode is enabled.
- The MCP session owns ticking.

#### `run_for_frames`

Purpose: advance `N` frames and return frame IDs plus optional sampled snapshots.

### Actor And Traffic Tools

#### `list_blueprints`

Purpose: list blueprints matching a CARLA filter such as `vehicle.*` or
`sensor.camera.*`.

#### `describe_blueprint`

Purpose: return attributes, recommended values, and whether each attribute is
modifiable.

#### `spawn_actor`

Purpose: spawn one actor from a blueprint and transform.

Use `try_spawn_actor` for user-facing retries when a spawn collision is expected.

#### `spawn_actor_batch`

Purpose: spawn many actors with `apply_batch_sync`.

This should be the primary scenario setup tool because CARLA recommends batched
commands for reliable deterministic setup.

#### `destroy_actor` and `destroy_actors`

Purpose: explicit cleanup. The server should track actors it created and make
cleanup easy.

#### `configure_traffic_manager`

Purpose: configure Traffic Manager sync mode, seed, hybrid physics, global
distance, and speed-difference settings.

### Sensor And Capture Tools

#### `attach_sensor`

Purpose: attach a sensor actor to a parent actor using a relative transform.

Capture implementation:

- Use sensor `listen` callbacks.
- Store frames in bounded queues.
- Key captured outputs by sensor ID and CARLA frame ID.

#### `capture_sensor_frame`

Purpose: return the latest or next frame from a configured sensor.

Result options:

- Text summary plus `structuredContent`.
- Image content for small captures.
- Resource link for larger image files.

#### `set_spectator`

Purpose: set the spectator transform for visual inspection.

#### `save_screenshot`

Purpose: save an image capture to the configured output directory and return a
resource link.

### Recorder And Evidence Tools

#### `record_episode`

Purpose: start CARLA recorder output to a managed file.

#### `stop_recording`

Purpose: stop recorder and register the resulting log as a Resource.

#### `replay_recording`

Purpose: replay a recording with explicit arguments for start time, duration,
follow actor, and sensor/weather replay flags when supported.

#### `query_recording_collisions`

Purpose: call CARLA recorder collision query and return structured results when
possible.

#### `query_recording_blocked_actors`

Purpose: call CARLA recorder blocked-actor query and return structured results
when possible.

#### `export_evidence_packet`

Purpose: create a compact directory or archive containing:

- server and client versions
- current map and world settings
- tool invocation summary
- actor inventory
- blueprint filters used
- sensor manifest
- selected captures
- recorder files
- warnings and known version limitations

Return:

- `structuredContent` with packet metadata
- `resource_link` to `carla://evidence/{packet_id}`

## Resource Catalog

Use a custom URI scheme for live CARLA session resources:

```text
carla://session/status
carla://world/current
carla://worlds
carla://blueprints/{filter}
carla://actors
carla://actors/{actor_id}
carla://sensors
carla://captures/{capture_id}
carla://recordings/{recording_id}
carla://evidence/{packet_id}
carla://logs/{log_id}
```

Resource rules:

- Resources should be read-only through MCP.
- If reading a resource would require a side effect, expose a Tool instead.
- Large binary outputs should be returned as resource links rather than embedded
  directly in every tool result.
- Resource contents should include MIME types.
- Resource IDs should be stable within a server session.

Resource examples:

```json
{
  "uri": "carla://world/current",
  "name": "current-world",
  "title": "Current CARLA World",
  "description": "Current map, world settings, actor counts, and timing mode.",
  "mimeType": "application/json"
}
```

```json
{
  "uri": "carla://captures/rgb-front-000120",
  "name": "rgb-front-000120.png",
  "title": "Front RGB Camera Frame 120",
  "description": "Captured RGB camera frame from CARLA frame 120.",
  "mimeType": "image/png"
}
```

## Prompt Catalog

Prompts are user-selected workflows. They should not hide side effects; instead
they should guide the model through available tools and remind it where user
confirmation is needed.

### `diagnose_carla`

Use when a user says CARLA will not start, connect, spawn actors, or record.

Prompt behavior:

1. Call `health_check`.
2. If disconnected, call `diagnose_environment`.
3. If connected, call `get_world_state`.
4. Report likely cause and next action.

### `setup_reproducible_session`

Use when a user wants deterministic stepping or synchronized sensor capture.

Prompt behavior:

1. Call `get_world_state`.
2. Call `set_sync_mode` with fixed timestep.
3. Configure Traffic Manager sync mode if needed.
4. Call `tick` once and verify frame advancement.

### `record_minimal_scene`

Use when a user wants a minimal working CARLA recording.

Prompt behavior:

1. Confirm map reload if needed.
2. Load or use a known map.
3. Spawn one vehicle.
4. Attach one RGB camera.
5. Start recording.
6. Run for a small frame count.
7. Stop recording.
8. Export evidence packet.

### `capture_actor_view`

Use when a user wants a screenshot or sensor frame from an actor.

Prompt behavior:

1. List actors or use actor ID.
2. Attach or reuse a camera sensor.
3. Tick until a frame is available.
4. Return an image resource link.

## Error Model

Follow MCP distinction between protocol errors and tool execution errors:

- Protocol errors: malformed MCP request, unknown tool, invalid JSON-RPC shape.
- Tool execution errors: CARLA connection failure, invalid map, spawn collision,
  timeout, missing blueprint, unsupported feature, output directory failure.

Tool execution errors should return `isError: true` with actionable text and
structured data:

```json
{
  "isError": true,
  "structuredContent": {
    "error_type": "spawn_failed",
    "reason": "collision_at_transform",
    "retryable": true,
    "suggested_next_tools": ["list_blueprints", "spawn_actor"]
  }
}
```

## Security And Safety Policy

MCP docs require careful handling because servers can expose arbitrary data and
actions. CARLA MCP should start with a conservative policy:

- Local-only stdio by default.
- Streamable HTTP only when explicitly enabled.
- No arbitrary Python execution tool.
- No shell command tool.
- No remote network bind unless explicitly configured.
- Write files only under a configured output directory.
- Require user confirmation for destructive or state-resetting tools:
  - `load_world`
  - `reload_world`
  - `destroy_actor`
  - `destroy_actors`
  - `set_sync_mode` when it changes an already-running session
  - `replay_recording` when it will stop or replace current state
- Validate all tool inputs with JSON Schema.
- Sanitize text outputs from CARLA exceptions before returning them.
- Rate-limit high-frequency tools such as `tick`, `run_for_frames`, and sensor
  capture in HTTP mode.

## Session Model

The server should keep an in-memory session state:

```text
CarlaSession
  connection: host, port, timeout, client_version, server_version
  world: current_map, settings, frame, sync_mode, fixed_delta_seconds
  tick_owner: true/false/unknown
  actors_created: actor_id[]
  sensors_created: sensor_id[]
  captures: capture_id -> file/frame metadata
  recordings: recording_id -> file metadata
  evidence_packets: packet_id -> manifest
  warnings: warning[]
```

Session state should not pretend to be CARLA truth. It is a server-side cache
used to manage cleanup and resources. Authoritative state still comes from CARLA
queries.

## First Implementation Shape

Package layout:

```text
carla-mcp/
  README.md
  docs/
    carla-support-surface.md
    mcp-correlation.md
  pyproject.toml
  src/
    carla_mcp/
      __init__.py
      adapter.py
      carla_protocols.py
      models.py
      prompts.py
      resources.py
      server.py
      session.py
      tool_inputs.py
      tools/
        diagnostics.py
        world.py
        actors.py
        sensors.py
        recorder.py
        evidence.py
      resources.py
      prompts.py
  tests/
    test_actor_tools.py
    test_diagnostics_tools.py
    test_recorder_evidence_tools.py
    test_sensor_tools.py
    test_session.py
    test_world_control_tools.py
  scripts/
    check_radon.py
```

Implementation order:

1. Tool schema definitions and mockable CARLA adapter.
2. `health_check`.
3. `get_world_state`.
4. `list_worlds`.
5. `set_sync_mode`.
6. `list_blueprints`.
7. `spawn_actor_batch`.
8. `attach_camera` and `capture_sensor_frame`.
9. Resource registry.
10. `record_episode`, `stop_recording`, and `export_evidence_packet`.
11. Static prompt catalog.

## First Release Boundary

The first release should prove:

- An MCP client can discover CARLA tools.
- The server can connect to a running CARLA instance.
- The server can expose current CARLA state as Resources.
- The server can configure synchronous stepping.
- The server can spawn actors through typed, validated Tools.
- The server can capture or record evidence and return Resource links.
- Failure modes are structured and useful.

It should not promise:

- Full remote operation.
- Full scenario language support.
- Full OpenDRIVE or OpenStreetMap support across all CARLA versions.
- Unreal Editor automation.
- Replacement for ScenarioRunner, Scenic, ROS2, or Traffic Manager.
