# GitHub and LinkedIn demo storyboard

The strongest demo is a **single uncut split-screen recording**: the MCP client
on the left and the CARLA window on the right. Viewers see a plain-English
request become a moving simulation, an inline image, measurable output, and a
clean world.

## Hero message

> Natural language in. A complete, sandboxed CARLA experiment out.

The proof points to keep visible are:

- one `execute_carla_script` tool call;
- live vehicle motion in CARLA;
- an RGB frame returned as native MCP image content;
- Landlock enforcement;
- measured speed before and after;
- restored weather and spectator camera;
- zero leftover test actors.

## Before recording

1. Start CARLA and load `Town10HD_Opt` or another map with free spawn points.
2. Connect Claude Code, Codex, or VS Code to the MCP server.
3. Put the MCP client on the left 45% of the screen and CARLA on the right 55%.
4. Use a 1920×1080 or 2560×1440 canvas at 30 fps. Increase the client font until
   the prompt and final result remain readable on a phone.
5. Hide notifications, tokens, usernames, unrelated terminals, and browser tabs.
6. Rehearse once with the deterministic runner below. Rich saves the real inline
   frame as `docs/assets/hero-frame.png` and a polished terminal capture as
   `docs/assets/hero-report.svg`.

Linux:

```bash
uv run python -m scripts.hero_demo --confirm-live
```

Windows client with CARLA on Windows and the MCP server in WSL2:

```powershell
uv run python -m scripts.hero_demo --confirm-live --windows `
  --host 172.18.112.1 --port 3000
```

Adjust the host and port for the local CARLA process. The demo is opt-in and
will not run without `--confirm-live`.

## Prompt to record

Paste this into the visible MCP client. Do not paste Python; seeing the agent
compose the workflow is the point of the demo.

> Use the CARLA MCP server to create one eight-second cinematic demo in a single
> tool call. Save the current weather. Spawn one red Tesla Model 3 at the first
> free spawn point, tag it `carla-mcp-hero-demo`, and show it driving naturally
> with Traffic Manager at 8 m/s in rainy golden-hour weather with its lights on.
> Call `watch_actor` for a smooth yaw-relative chase view so I can watch it move
> in real time and the spectator is restored afterward. Attach an RGB camera and
> publish one frame as
> `demos/hero.png`. Return the map, speed before and after, capture metadata,
> Landlock status, restored-state checks, and leftovers. In `finally`, detach
> sensors, destroy only actors created by this run, and restore both weather and
> spectator camera. Use bounded loops and leave zero test actors behind.

If the client supports MCP prompts, `run_visual_showcase` provides the same
workflow guidance.

## 55-second shot list

| Time | MCP client panel | CARLA panel | Narration / caption |
| --- | --- | --- | --- |
| 0–4s | Show the plain-English prompt | Idle map | “What if an AI agent could run the whole experiment safely?” |
| 4–10s | Submit; approve the annotated destructive tool | Idle map | “One MCP tool composes the workflow.” |
| 10–18s | Briefly show the generated call | Tesla spawns; weather changes | “The server validates the script before execution.” |
| 18–30s | Tool remains running | Chase view follows the moving Tesla | “The experiment runs inside a Rust + Landlock sandbox.” |
| 30–38s | Inline RGB image appears | Tesla continues moving | “Sensor output comes back as native MCP image content.” |
| 38–47s | Show speed before/after and map | Vehicle and rain disappear during cleanup | “Results are measurable, not just visual.” |
| 47–53s | Highlight restored state and `leftovers: []` | Original weather and camera return | “The world is restored. Zero test actors left behind.” |
| 53–55s | Cut to repository cover | Repository cover | “Open source. Link in comments.” |

Keep the generated tool call collapsed after the first second so the recording
focuses on the prompt, live simulation, image, and result rather than a wall of
Python.

## Two shorter companion demos

### Security proof — 15 seconds

Run:

```bash
uv run python scripts/sandbox_demo.py
```

Show successful Landlock enforcement, rejected private API traversal, and a
persisted evidence manifest. Use the existing `docs/assets/demo.gif` on GitHub.

### Research workflow — 30 seconds

Prompt:

> Run a bounded Traffic Manager experiment with seed 42. Spawn eight vehicles,
> tune half for an 8 m/s desired speed and half for 14 m/s, run for six seconds,
> compare telemetry, and report the two average speeds. Restore settings and
> destroy only actors created by the experiment.

This companion demonstrates that the toolkit is useful for repeatable research,
not only cinematic control.

## Publishing checklist

- Upload the video directly to LinkedIn instead of linking to a video host.
- Export H.264 MP4, 1080p, 30 fps, under 60 seconds, with burned-in captions.
- Use the captured `hero-frame.png` as the post thumbnail.
- Do not commit the MP4 to Git; keep only the representative `hero-frame.png`.
- In GitHub **Settings → Social preview**, upload a 1280×640 crop based on the
  hero frame and project title.
- Add alt text: “An MCP client controlling a CARLA Tesla in a sandboxed,
  self-cleaning experiment.”
