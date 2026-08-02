# LinkedIn launch copy

Post the video natively and put the repository URL in the first comment. Replace
bracketed values after recording.

## Short version

I gave an AI agent one plain-English request:

“Spawn a Tesla in CARLA, drive it, capture what it sees, and clean everything up.”

It composed one MCP workflow, accelerated the vehicle from **[before] to [after]
m/s**, returned the camera frame inline, restored the weather and spectator, and
left **zero actors** behind.

The script ran through a Rust + Landlock sandbox—not unrestricted Python.

I’m open-sourcing the CARLA Agentic Toolkit today. Repository link in the first
comment.

#AutonomousVehicles #CARLA #MCP #OpenSource #AIEngineering #Simulation

## Long version

Most AI + simulator demos stop at “the model called a tool.”

I wanted to see whether an agent could run a **complete, measurable CARLA
experiment** without receiving unrestricted access to the machine.

In this uncut split-screen demo, I ask for one thing in plain English: spawn a
Tesla, create a rainy golden-hour scene, accelerate the car, follow it with the
spectator camera, return an RGB frame, and clean up.

The agent turns that into one composable MCP workflow. The server then:

- validates the generated Python against a curated CARLA API;
- executes it behind a Rust subprocess and Linux Landlock;
- reports the speed change from **[before] to [after] m/s**;
- returns the camera frame as native MCP image content;
- restores the weather and spectator camera; and
- leaves **zero test actors** in the simulator.

The project also supports traffic experiments, sensors, semantic map queries,
recording, replay, evidence export, Windows clients through WSL2, and runtime
capability probing across CARLA builds.

It is experimental and local-first—not a production multi-user boundary—but the
core is open source under MIT.

Repository link in the first comment. Feedback and contributions are welcome.

#AutonomousVehicles #CARLA #ModelContextProtocol #MCP #OpenSource
#AIEngineering #Simulation #Robotics

## First comment

Source, setup guide, security model, and demo instructions:

[repository URL]

If you try it, I’d especially like feedback on the first workflow you want an
agent to automate in CARLA.

## Video captions

Use these as large burned-in captions, one at a time:

1. **One natural-language request**
2. **One composable MCP tool**
3. **Rust + Landlock enforced**
4. **Live CARLA motion**
5. **Native image returned**
6. **World restored · 0 leftovers**
7. **Open source · MIT**
