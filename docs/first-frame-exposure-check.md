# Fresh Camera Exposure Check

Investigated [#162](https://github.com/sidkolapalli/carla-agentic-toolkit/issues/162)
on 2026-10-09. The issue asks for a live comparison before choosing a warmup or
manual-exposure default. No material first-frame brightness difference was
observed in the tested setup, so capture behavior was not changed.

## Supported Live Measurement

Matching Windows-native CARLA 0.9.16 client and server, dedicated Town10HD_Opt,
off-screen rendering, Low quality, asynchronous variable-step settings, and
`no_rendering_mode=False`. This was a trusted native probe, not a Linux sandbox
test. No provider calls were made.

Two fresh stationary RGB cameras used the same actual map spawnpoint, elevated
six metres and facing down the street at pitch -12 degrees. The spectator was
not moved. Sun altitude was set to 70 degrees for the bright scene and -20 for
the dark scene, with native weather readback verified. Each scene advanced for
at least two wall-clock seconds and five delivered frames before its camera
existed. This is scene-lighting preparation, not discarded camera warmup.

Each camera used 640x360 pixels, `sensor_tick=0`, native `exposure_mode=histogram`,
post-processing enabled, gamma approximately 2.2, and native adaptation speeds
3.0 up and 1.0 down. These attributes agree with CARLA's
[RGB camera reference](https://carla.readthedocs.io/en/0.9.16/ref_sensors/#rgb-camera).
The actual first callback was pinned; 30 increasing native frames were retained
with zero queue drops. Raw BGRA hashes and separately encoded PNG hashes were
recorded. The first and last PNGs in both scenes were visually inspected and
showed the actual street, not a blank or obstructed view.

Mean luma below is a Rec.709-weighted average of encoded RGB channels on a
0-255 scale, not calibrated photometry. Black clipping means all three channels
are 0-2; white clipping means all three are 253-255.

| Scene | Native frames | First mean | Last mean | Entire sample range | First/last black fraction | Simulation span |
| --- | --- | --- | --- | --- | --- | --- |
| Bright | 6475-6504 | 131.6093 | 131.5912 | 131.5657-131.6353 | 0.003003 / 0.002891 | 0.4391 s |
| Dark | 7010-7041 | 28.9980 | 29.0030 | 28.9658-29.0102 | 0.098394 / 0.098181 | 0.5149 s |

Bright frames had no all-channel white clipping; the dark first/last fraction
was 0.00000434. First-to-last mean changes were -0.0181 and +0.0050 respectively.
These samples do not establish a useful warmup frame count or justify changing
the default to manual exposure. Results do not guarantee other maps, lighting,
resolutions, quality settings, platforms, longer adaptation periods, or builds.

Both original listener stops and camera destructions were acknowledged.
Original weather, all six typed world settings, episode, spectator transform,
and exact unrelated actor inventory were verified after fresh publication.
The probe's durable journal was cleared only after those checks. The local
receipts and images remain ignored and were not published.

## UE5 Limitation

The dedicated matching CARLA 0.10.0 client/server reported
`world.is_weather_enabled() == False`. Both immediate and frame-backed native
weather readback refused the requested sun altitude before any camera existed.
This map therefore did not supply a valid controlled bright/dark comparison.
It is not evidence that exposure adapts correctly or incorrectly on UE5.

Both failed attempts retained dirty evidence. Separate read-only verification
of their exact journals, fresh episode/frame, full original weather and settings,
spectator, and actor inventory cleared the same lease; no fresh-root bypass or
recovery mutation was used. CARLA's
[0.10.0 server weather handlers](https://github.com/carla-simulator/carla/blob/0.10.0/Unreal/CarlaUnreal/Plugins/Carla/Source/Carla/Server/CarlaServer.cpp#L638-L670)
expose that capability distinction.

## Capture Contract

`save_screenshot` retains the first delivered frame of its temporary RGB camera.
It neither discards a documented warmup count nor forces manual exposure.
Caller-supplied blueprint attributes remain authoritative, including an explicit
`exposure_mode`. A screenshot is not promised to be exposure-settled in an
untested scene. A persistent camera subscription is the existing way to retain
later frames when an application needs to inspect adaptation over time.
